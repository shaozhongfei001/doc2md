"""T801/T802/T805: reconcile keeps the denominator, switching is CAS, rollback moves one pointer."""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.release.recovery import plan_recovery, verify_committed
from app.extensions.release.reconcile import reconcile
from app.extensions.release.switch import (
    ACTIVE,
    LOCAL_READY,
    activate,
    prepare_release,
    rollback,
    switch_route,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def scope() -> list[dict]:
    return [
        {"object_id": "O1", "source_version_id": "SV1", "content_sha256": DIGEST_A},
        {"object_id": "O2", "source_version_id": "SV1", "content_sha256": DIGEST_B},
        {"object_id": "O3", "source_version_id": "SV2", "content_sha256": DIGEST_A},
    ]


# --------------------------------------------------------------- reconcile
def test_denominator_is_the_full_source_list_even_with_failures():
    report = reconcile(
        scope_id="S1",
        scope_objects=scope(),
        processed={
            "O1": {"status": "REUSED"},
            "O2": {"status": "FAILED", "reason": "parse error"},
            "O3": {"status": "PENDING"},
        },
    )
    assert report.denominator == 3
    assert report.counts == {
        "REUSED": 1,
        "REPROCESSED": 0,
        "EXCLUDED_WITH_REASON": 0,
        "FAILED": 1,
        "PENDING": 1,
    }
    assert sum(report.counts.values()) == report.denominator


def test_unprocessed_object_stays_pending_not_dropped():
    report = reconcile(scope_id="S1", scope_objects=scope(), processed={})
    assert report.counts["PENDING"] == 3
    assert report.denominator == 3


def test_result_outside_the_scope_is_a_permission_expansion():
    report = reconcile(
        scope_id="S1", scope_objects=scope(), processed={"O1": {"status": "REUSED"}, "OX": {"status": "REUSED"}}
    )
    assert not report.clean
    assert any(b["code"] == "PERMISSION_EXPANSION" for b in report.blockers)


def test_wrong_version_and_hash_are_blockers():
    report = reconcile(
        scope_id="S1",
        scope_objects=scope(),
        processed={
            "O1": {"status": "REUSED", "source_version_id": "SV9"},
            "O2": {"status": "REUSED", "content_sha256": "c" * 64},
            "O3": {"status": "REUSED"},
        },
    )
    codes = {b["code"] for b in report.blockers}
    assert codes == {"WRONG_VERSION", "HASH_MISMATCH"}


def test_exclusion_requires_a_reason():
    with pytest.raises(ExtensionError):
        reconcile(
            scope_id="S1",
            scope_objects=scope(),
            processed={"O1": {"status": "EXCLUDED_WITH_REASON"}},
        )


def test_incomplete_enumeration_forbids_delete_candidates():
    report = reconcile(
        scope_id="S1",
        scope_objects=scope(),
        processed={"O1": {"status": "REUSED"}},
        enumeration_complete=False,
    )
    assert report.delete_candidates == []
    assert any(b["code"] == "ENUMERATION_INCOMPLETE" for b in report.blockers)


def test_delete_candidates_only_for_complete_enumeration():
    report = reconcile(
        scope_id="S1",
        scope_objects=[{"object_id": "O1", "source_version_id": "SV1"}],
        processed={"O1": {"status": "REUSED"}, "OLD": {"status": "REUSED"}},
        enumeration_complete=True,
    )
    assert report.delete_candidates == ["OLD"]


# ---------------------------------------------------------------- releases
def release(**overrides):
    kwargs = {
        "release_id": "REL-1",
        "generation_id": "G1",
        "scope": "table:T1",
        "read_set": ["SV1"],
        "reconcile_clean": True,
    }
    kwargs.update(overrides)
    return prepare_release(**kwargs)


def test_prepared_release_is_only_local_ready():
    assert release().status == LOCAL_READY


def test_dirty_reconciliation_cannot_produce_a_release():
    with pytest.raises(ExtensionError):
        release(reconcile_clean=False)


def test_activation_needs_real_acks_from_every_required_consumer():
    prepared = release()
    active = activate(
        prepared,
        required_consumers=["search-index"],
        acks={"search-index": {"status": "ACKED", "generation_id": "G1"}},
    )
    assert active.status == ACTIVE


def test_missing_ack_keeps_local_ready():
    with pytest.raises(ExtensionError) as excinfo:
        activate(release(), required_consumers=["search-index"], acks={})
    assert excinfo.value.code == "E_CONSUMER_NOT_CONNECTED"
    assert excinfo.value.detail["missing"] == ["search-index"]


def test_present_but_unfinished_ack_does_not_activate():
    """QA-TEST-01: the ``missing`` branch alone has an unclean failure mode.

    ``test_missing_ack_keeps_local_ready`` passes ack-absent input, so under a
    mutation that skips the guard the copy ``acks[name]`` raises ``KeyError``
    before any assertion runs — the test goes red, but not because its own
    assertions failed. This case keeps the key present with an unfinished status,
    so the guard is the *only* thing that can reject it: skipping the guard returns
    ACTIVE and the test fails with ``DID NOT RAISE``.
    """
    with pytest.raises(ExtensionError) as excinfo:
        activate(
            release(),
            required_consumers=["search-index"],
            acks={"search-index": {"status": "IN_PROGRESS", "generation_id": "G1"}},
        )
    assert excinfo.value.code == "E_CONSUMER_NOT_CONNECTED"
    assert excinfo.value.detail["stale_or_wrong_generation"] == ["search-index"]


def test_simulated_ack_never_activates():
    with pytest.raises(ExtensionError) as excinfo:
        activate(
            release(),
            required_consumers=["search-index"],
            acks={"search-index": {"status": "SIMULATED", "generation_id": "G1"}},
        )
    assert excinfo.value.detail["simulated"] == ["search-index"]


def test_ack_for_another_generation_is_not_an_ack():
    with pytest.raises(ExtensionError) as excinfo:
        activate(
            release(),
            required_consumers=["search-index"],
            acks={"search-index": {"status": "ACKED", "generation_id": "G0"}},
        )
    assert excinfo.value.detail["stale_or_wrong_generation"] == ["search-index"]


def test_no_required_consumer_means_no_active():
    with pytest.raises(ExtensionError):
        activate(release(), required_consumers=[], acks={})


# ------------------------------------------------------------------ routing
def test_switch_is_cas_on_the_pointer():
    result = switch_route(
        expected_previous_release="REL-0",
        current_release="REL-0",
        target_release="REL-1",
        scope="table:T1",
        route_epoch=4,
    )
    assert result["current_release"] == "REL-1"
    assert result["route_epoch"] == 5


def test_stale_switch_is_a_version_conflict():
    with pytest.raises(ExtensionError) as excinfo:
        switch_route(
            expected_previous_release="REL-0",
            current_release="REL-9",
            target_release="REL-1",
            scope="table:T1",
            route_epoch=4,
        )
    assert excinfo.value.code == "E_VERSION_CONFLICT"


def test_rollback_moves_only_the_pointer():
    ledger = {"epoch": 7, "revoked": ["SV9"]}
    result = rollback(
        expected_current_release="REL-1",
        current_release="REL-1",
        previous_target="REL-0",
        scope="table:T1",
        route_epoch=5,
        access_ledger=ledger,
        tombstones=["SV9"],
        target_contents=["SV1"],
    )
    assert result["current_release"] == "REL-0"
    assert result["route_epoch"] == 6
    assert result["access_ledger"] == ledger  # not rolled back
    assert result["tombstones"] == ["SV9"]  # tombstone stays


def test_rollback_refuses_to_reopen_revoked_content():
    with pytest.raises(ExtensionError) as excinfo:
        rollback(
            expected_current_release="REL-1",
            current_release="REL-1",
            previous_target="REL-0",
            scope="table:T1",
            route_epoch=5,
            access_ledger={},
            tombstones=["SV9"],
            target_contents=["SV9"],
        )
    assert excinfo.value.code == "E_SCOPE_DENIED"


def test_rollback_with_stale_pointer_conflicts():
    with pytest.raises(ExtensionError) as excinfo:
        rollback(
            expected_current_release="REL-0",
            current_release="REL-1",
            previous_target="REL-0",
            scope="table:T1",
            route_epoch=5,
            access_ledger={},
            tombstones=[],
        )
    assert excinfo.value.code == "E_VERSION_CONFLICT"


# ----------------------------------------------------------------- recovery
def test_recovery_only_removes_staging_and_keeps_committed():
    plan = plan_recovery(
        staging_entries=["staging/part.tmp"],
        committed_entries=["release/G1/manifest.json"],
        root_prefix="ext",
    )
    assert plan.removable == ["staging/part.tmp"]
    assert plan.retained == ["release/G1/manifest.json"]


def test_recovery_refuses_paths_outside_the_root():
    plan = plan_recovery(
        staging_entries=["/etc/passwd", "../out.tmp"],
        committed_entries=[],
        root_prefix="ext",
    )
    assert plan.removable == []
    assert len(plan.issues) == 2


def test_committed_digest_mismatch_fails_closed():
    with pytest.raises(ExtensionError) as excinfo:
        verify_committed({"output_sha256": DIGEST_A}, actual_sha256=DIGEST_B)
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"
    assert excinfo.value.detail["actual_sha256"] == DIGEST_B


def test_committed_digest_match_is_accepted():
    assert verify_committed({"output_hash": DIGEST_A}, actual_sha256=DIGEST_A) == DIGEST_A


def test_manifest_without_a_digest_is_refused():
    with pytest.raises(ExtensionError):
        verify_committed({}, actual_sha256=DIGEST_A)
