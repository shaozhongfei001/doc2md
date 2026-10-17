"""Source version registration and authority tests (L02 / T202–T203)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import ExtensionError, identity
from app.extensions.sources.authority import (
    assert_authority,
    compare_in_house_and_external,
)
from app.extensions.sources.versioning import (
    VersionLedgerView,
    register_source_version,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _payload(**overrides) -> dict:
    base = {
        "schema_version": "source-version/1.0",
        "source_version_id": "",
        "source_id": "demo_bank_policy",
        "external_object_id": "policy_demo_01",
        "version_kind": "SOURCE_NATIVE",
        "source_revision": "r1",
        "snapshot_id": None,
        "content_sha256": SHA_A,
        "legacy_query_ref": None,
        "processing_route": "REPROCESS",
        "source_kind": "BANK_RULE",
        "authority_class": "BANK_EFFECTIVE_RULE",
        "permission_ref": "perm-1",
        "owner_ref": "owner-1",
        "classification": "INTERNAL",
        "purpose_scopes": ["PREVISIT"],
        "license_ref": None,
        "valid_from": None,
        "valid_to": None,
        "source_file_ref": {
            "root": "RAW_VAULT",
            "relative_path": "by_hash/aa/aa/original.bin",
            "sha256": SHA_A,
        },
    }
    base.update(overrides)
    base["source_version_id"] = _identity_of(base)
    return base


def _identity_of(document: dict) -> str:
    query = document.get("legacy_query_ref")
    return identity.source_version_id(
        {
            "source_id": document["source_id"],
            "external_object_id": document["external_object_id"],
            "version_kind": document["version_kind"],
            "source_revision": document["source_revision"],
            "snapshot_id": document["snapshot_id"],
            "content_sha256": document["content_sha256"],
            "legacy_query_ref_hash": None
            if query is None
            else identity.digest("legacy-query-ref/1", {"ref": query}),
        }
    )


# ------------------------------------------------------------------- versions
def test_registers_a_native_source_version():
    result = register_source_version(_payload())
    assert result.source_version_id.startswith("sv-")
    assert result.is_new is True
    assert result.access_key.startswith("access-")
    assert result.version.version_kind == "SOURCE_NATIVE"


def test_registers_a_local_snapshot_without_pretending_it_is_a_revision():
    document = _payload(
        version_kind="LOCAL_SNAPSHOT",
        source_revision=None,
        snapshot_id="snap-1",
    )
    result = register_source_version(document)
    assert result.version.version_kind == "LOCAL_SNAPSHOT"
    assert result.version.source_revision is None


def test_native_version_cannot_borrow_a_snapshot_id_instead_of_a_revision():
    document = _payload(source_revision=None, snapshot_id="snap-1")
    with pytest.raises(ExtensionError) as excinfo:
        register_source_version(document)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "SOURCE_NATIVE" in excinfo.value.message


def test_snapshot_cannot_smuggle_a_source_revision():
    document = _payload(
        version_kind="LOCAL_SNAPSHOT", source_revision="r1", snapshot_id="snap-1"
    )
    with pytest.raises(ExtensionError) as excinfo:
        register_source_version(document)
    assert "LOCAL_SNAPSHOT" in excinfo.value.message


def test_same_revision_with_other_bytes_is_isolated_not_overwritten():
    existing = _payload(content_sha256=SHA_A)
    ledger = VersionLedgerView.from_entries([existing])
    incoming = _payload(content_sha256=SHA_B, source_file_ref={
        "root": "RAW_VAULT", "relative_path": "by_hash/bb/bb/original.bin", "sha256": SHA_B
    })
    with pytest.raises(ExtensionError) as excinfo:
        register_source_version(incoming, ledger=ledger)
    assert excinfo.value.code == "E_SOURCE_VERSION_CONFLICT"
    assert excinfo.value.detail["existing_source_version_id"] == existing["source_version_id"]


def test_re_registering_the_same_version_is_idempotent():
    existing = _payload()
    ledger = VersionLedgerView.from_entries([existing])
    result = register_source_version(_payload(), ledger=ledger)
    assert result.is_new is False


def test_declared_identity_that_does_not_match_the_inputs_is_a_conflict():
    document = _payload()
    document["source_version_id"] = "sv-" + "0" * 64
    with pytest.raises(ExtensionError) as excinfo:
        register_source_version(document)
    assert excinfo.value.code == "E_VERSION_CONFLICT"
    assert excinfo.value.detail["computed"].startswith("sv-")


def test_same_bytes_from_two_sources_keep_distinct_identities():
    first = register_source_version(_payload())
    second = register_source_version(_payload(source_id="other_source"))
    assert first.source_version_id != second.source_version_id
    assert first.access_key != second.access_key


def test_reuse_route_requires_an_authorised_query_reference():
    document = _payload(
        processing_route="REUSE_QUERY",
        legacy_query_ref=None,
        source_file_ref=None,
        content_sha256=SHA_A,
    )
    with pytest.raises(ExtensionError) as excinfo:
        register_source_version(document)
    assert excinfo.value.code == "E_CONTRACT_INVALID"


def test_reprocess_route_requires_bytes_and_digest():
    document = _payload(processing_route="REPROCESS", source_file_ref=None)
    with pytest.raises(ExtensionError) as excinfo:
        register_source_version(document)
    assert excinfo.value.code == "E_CONTRACT_INVALID"


# ------------------------------------------------------------------ authority
def test_bank_rule_without_an_authority_reference_is_not_usable():
    decision = assert_authority({**_payload(), "authority_decision_ref": None})
    assert decision.usable is False
    assert decision.blocked_field == "authority_decision_ref"
    with pytest.raises(ExtensionError) as excinfo:
        decision.assert_usable()
    assert excinfo.value.code == "E_SCOPE_DENIED"


def test_external_policy_without_licence_is_not_usable():
    document = {
        **_payload(),
        "source_kind": "EXTERNAL_POLICY",
        "authority_class": "EXTERNAL_REFERENCE",
        "license_ref": None,
        "authority_decision_ref": "authz-1",
    }
    decision = assert_authority(document)
    assert decision.usable is False
    assert decision.requires_license is True
    assert decision.blocked_field == "license_ref"


def test_external_material_can_never_be_a_bank_effective_rule():
    document = {
        **_payload(),
        "source_kind": "PEER_REFERENCE",
        "authority_class": "BANK_EFFECTIVE_RULE",
        "license_ref": "lic-1",
        "authority_decision_ref": "authz-1",
    }
    decision = assert_authority(document)
    assert decision.usable is False
    assert decision.blocked_field == "authority_class"


def test_licensed_external_reference_is_usable_as_reference_only():
    document = {
        **_payload(),
        "source_kind": "EXTERNAL_POLICY",
        "authority_class": "EXTERNAL_REFERENCE",
        "license_ref": "lic-1",
        "authority_decision_ref": None,
    }
    assert assert_authority(document).usable is True


def test_expired_version_is_not_usable():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    document = {
        **_payload(),
        "authority_decision_ref": "authz-1",
        "valid_to": past.isoformat(),
    }
    decision = assert_authority(document)
    assert decision.usable is False
    assert decision.blocked_field == "valid_to"


def test_not_yet_effective_version_is_not_usable():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    document = {
        **_payload(),
        "authority_decision_ref": "authz-1",
        "valid_from": future.isoformat(),
    }
    assert assert_authority(document).usable is False


def test_window_is_checked_against_the_supplied_clock():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    document = {
        **_payload(),
        "authority_decision_ref": "authz-1",
        "valid_to": past.isoformat(),
    }
    earlier = past - timedelta(days=1)
    assert assert_authority(document, now=earlier).usable is True


def test_version_without_a_purpose_scope_is_not_usable():
    document = {**_payload(), "authority_decision_ref": "authz-1", "purpose_scopes": []}
    decision = assert_authority(document)
    assert decision.usable is False
    assert decision.blocked_field == "purpose_scopes"


def test_external_reference_never_overrides_the_in_house_rule():
    bank = {**_payload(), "authority_decision_ref": "authz-1"}
    external = {
        **_payload(),
        "source_id": "peer",
        "source_kind": "PEER_REFERENCE",
        "authority_class": "EXTERNAL_REFERENCE",
        "license_ref": "lic-1",
        "authority_decision_ref": None,
    }
    comparison = compare_in_house_and_external(bank, external)
    assert comparison["external_may_override_in_house"] is False
    assert comparison["in_house_authority"] == "BANK_EFFECTIVE_RULE"
    assert comparison["external_authority"] == "EXTERNAL_REFERENCE"
    assert comparison["status"] == "COEXIST"


def test_unlicensed_external_reference_is_reported_as_blocked():
    bank = {**_payload(), "authority_decision_ref": "authz-1"}
    external = {
        **_payload(),
        "source_id": "peer",
        "source_kind": "PEER_REFERENCE",
        "authority_class": "EXTERNAL_REFERENCE",
        "license_ref": None,
        "authority_decision_ref": None,
    }
    comparison = compare_in_house_and_external(bank, external)
    assert comparison["external_usable"] is False
    assert "licence" in comparison["external_block_reason"]
