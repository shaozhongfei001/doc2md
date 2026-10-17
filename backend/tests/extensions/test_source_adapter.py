"""Adapter and local-manifest connector tests (L02 / T201)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.sources.base import (
    EventPage,
    SourceCapabilities,
    SourceEventPayload,
    assert_page_contract,
    capability_gaps,
)
from app.extensions.sources.local_manifest import LocalManifestConnector

SHA_A = "a" * 64
SHA_B = "b" * 64


def _manifest(tmp_path: Path, **overrides) -> Path:
    payload = {
        "source_id": "demo_bank_policy",
        "selection_scope": {"purpose": ["PREVISIT"]},
        "authorization_snapshot_ref": "authz-demo-1",
        "objects": [
            {"external_object_id": "obj_1", "source_revision": "r1", "content_sha256": SHA_A},
            {"external_object_id": "obj_2", "source_revision": None, "snapshot_id": "snap-2",
             "content_sha256": SHA_B},
            {"external_object_id": "obj_3", "source_revision": "r3", "content_sha256": SHA_A},
        ],
        "revoked": [{"external_object_id": "obj_9", "reason": "withdrawn"}],
        "deleted": [],
    }
    payload.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------------------- probing
def test_missing_manifest_is_not_connected(tmp_path: Path):
    report = LocalManifestConnector(tmp_path / "absent.json").probe()
    assert report.state == "NOT_CONNECTED"
    assert report.usable_for_sync is False
    assert "NOT_CONNECTED" in report.gaps


def test_malformed_manifest_is_probe_failed(tmp_path: Path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    report = LocalManifestConnector(path).probe()
    assert report.state == "PROBE_FAILED"
    assert report.reachable is False


def test_manifest_without_revocations_reports_a_capability_gap(tmp_path: Path):
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("revoked")
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = LocalManifestConnector(path).probe()
    assert report.reachable is True
    assert "NO_REVOCATION_REPORTING" in report.gaps
    assert report.state == "CONNECTED_WITH_GAPS"


def test_manifest_with_revocations_is_fully_connected(tmp_path: Path):
    report = LocalManifestConnector(_manifest(tmp_path)).probe()
    assert report.declared.reports_revocations is True
    assert report.gaps == ()
    assert report.state == "CONNECTED"


def test_declared_capabilities_match_the_manifest(tmp_path: Path):
    caps = LocalManifestConnector(_manifest(tmp_path)).capabilities()
    assert caps == SourceCapabilities(
        incremental_events=True,
        cursor=True,
        full_enumeration=True,
        reports_deletions=True,
        reports_revocations=True,
        authorization_lease=False,
    )
    assert list(capability_gaps(caps)) == []


# -------------------------------------------------------------------- paging
def test_first_page_exposes_a_cursor_and_is_not_complete(tmp_path: Path):
    connector = LocalManifestConnector(_manifest(tmp_path))
    page = connector.fetch_page(cursor=None, page_size=2)
    assert [e.external_object_id for e in page.events] == ["obj_1", "obj_2"]
    assert page.next_cursor == "2"
    assert page.enumeration_complete is False


def test_second_page_completes_and_carries_the_revocation(tmp_path: Path):
    connector = LocalManifestConnector(_manifest(tmp_path))
    page = connector.fetch_page(cursor="2", page_size=2)
    ids = [e.external_object_id for e in page.events]
    assert ids == ["obj_3", "obj_9"]
    assert page.events[-1].event_type == "REVOKE"
    assert page.next_cursor is None
    assert page.enumeration_complete is True


def test_pages_are_stable_and_ordered(tmp_path: Path):
    connector = LocalManifestConnector(_manifest(tmp_path))
    first = connector.fetch_page(cursor=None, page_size=1)
    again = connector.fetch_page(cursor=None, page_size=1)
    assert [e.event_id for e in first.events] == [e.event_id for e in again.events]
    assert first.events[0].source_order_token == "00000000"


def test_version_kind_follows_the_revision_presence(tmp_path: Path):
    connector = LocalManifestConnector(_manifest(tmp_path))
    page = connector.fetch_page(cursor=None, page_size=3)
    versions = {e.external_object_id: e.source_version_id for e in page.events}
    assert versions["obj_1"].startswith("sv-")
    assert versions["obj_2"].startswith("sv-")
    # Same digest, different version identity: a snapshot is not a source revision.
    assert versions["obj_1"] != versions["obj_3"]


def test_scope_hash_changes_when_the_selection_scope_changes(tmp_path: Path):
    wide = LocalManifestConnector(_manifest(tmp_path)).fetch_page(cursor=None, page_size=1)
    narrow = LocalManifestConnector(_manifest(tmp_path)).fetch_page(
        cursor=None, page_size=1, scope={"purpose": ["POSTVISIT"]}
    )
    assert wide.selection_scope_hash != narrow.selection_scope_hash


@pytest.mark.parametrize("cursor", ["abc", "-1", "99"])
def test_invalid_cursor_is_an_input_error(tmp_path: Path, cursor: str):
    connector = LocalManifestConnector(_manifest(tmp_path))
    with pytest.raises(ExtensionError) as excinfo:
        connector.fetch_page(cursor=cursor, page_size=2)
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_zero_page_size_is_refused(tmp_path: Path):
    with pytest.raises(ExtensionError) as excinfo:
        LocalManifestConnector(_manifest(tmp_path)).fetch_page(cursor=None, page_size=0)
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_manifest_object_without_digest_is_schema_drift(tmp_path: Path):
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["objects"][0].pop("content_sha256")
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExtensionError) as excinfo:
        LocalManifestConnector(path).fetch_page(cursor=None, page_size=2)
    assert excinfo.value.code == "E_ENGINE_SCHEMA_DRIFT"


# ------------------------------------------------------------- page contract
def _event(**overrides) -> SourceEventPayload:
    base = dict(
        event_id="e1",
        external_object_id="obj_1",
        event_type="UPSERT",
        source_version_id="sv-1",
        payload_hash="c" * 64,
        source_order_token="00000001",
        access_revision="1",
        observed_at="2026-10-18T00:00:00Z",
    )
    base.update(overrides)
    return SourceEventPayload(**base)


def _page(events, **overrides) -> EventPage:
    base = dict(
        source_id="demo",
        events=tuple(events),
        next_cursor="1",
        enumeration_complete=False,
        selection_scope_hash="d" * 64,
        authorization_snapshot_ref="authz-1",
    )
    base.update(overrides)
    return EventPage(**base)


def test_page_contract_accepts_a_normal_page():
    assert_page_contract(_page([_event()]), page_size=10)


def test_page_contract_rejects_duplicate_event_ids():
    with pytest.raises(ExtensionError) as excinfo:
        assert_page_contract(_page([_event(), _event()]), page_size=10)
    assert excinfo.value.code == "E_ENGINE_SCHEMA_DRIFT"
    assert "duplicate event_id" in excinfo.value.message


def test_page_contract_rejects_unknown_event_type():
    with pytest.raises(ExtensionError) as excinfo:
        assert_page_contract(_page([_event(event_type="MERGE")]), page_size=10)
    assert "unknown event_type" in excinfo.value.message


def test_page_contract_rejects_oversized_page():
    events = [_event(event_id=f"e{i}") for i in range(3)]
    with pytest.raises(ExtensionError) as excinfo:
        assert_page_contract(_page(events), page_size=2)
    assert "page_size" in excinfo.value.message


def test_page_contract_rejects_upsert_without_a_version():
    with pytest.raises(ExtensionError) as excinfo:
        assert_page_contract(_page([_event(source_version_id=None)]), page_size=10)
    assert "carries no source_version_id" in excinfo.value.message


def test_page_contract_rejects_a_page_that_would_loop_forever():
    with pytest.raises(ExtensionError) as excinfo:
        assert_page_contract(
            _page([_event()], next_cursor=None, enumeration_complete=False), page_size=10
        )
    assert "loop forever" in excinfo.value.message
