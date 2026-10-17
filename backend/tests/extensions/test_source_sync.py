"""Sync commit and access-ledger tests (L02 / T204).

The decisive property is ordering: **cursor moves only after the durable commit
returns**. ``test_cursor_does_not_move_when_the_commit_fails`` is the assertion that
would silently pass if the implementation advanced the cursor first — it is written so
that reordering the two statements turns it red.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import ExtensionError
from app.extensions.sources.access import AccessLedger
from app.extensions.sources.base import EventPage, SourceEventPayload
from app.extensions.sources.sync import SyncCommitter, SyncState

SCOPE = "d" * 64
SHA_A = "a" * 64


def _event(event_id: str, object_id: str, event_type: str = "UPSERT", **overrides):
    base = dict(
        event_id=event_id,
        external_object_id=object_id,
        event_type=event_type,
        source_version_id="sv-1" if event_type != "REVOKE" else None,
        payload_hash="c" * 64,
        source_order_token="00000001",
        access_revision="1",
        observed_at="2026-10-18T00:00:00Z",
    )
    base.update(overrides)
    return SourceEventPayload(**base)


def _page(events, *, next_cursor="1", complete=False) -> EventPage:
    return EventPage(
        source_id="demo",
        events=tuple(events),
        next_cursor=next_cursor,
        enumeration_complete=complete,
        selection_scope_hash=SCOPE,
        authorization_snapshot_ref="authz-1",
    )


def _state() -> SyncState:
    return SyncState.initial(
        source_id="demo", selection_scope_hash=SCOPE, authorization_snapshot_ref="authz-1"
    )


class _Recorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, payload: dict) -> None:
        self.calls.append(payload)
        if self.fail:
            raise ExtensionError("E_COMMIT_INCOMPLETE", "simulated durable-write failure")


# ------------------------------------------------------------------ commit flow
def test_a_page_advances_the_cursor_after_the_commit():
    recorder = _Recorder()
    committer = SyncCommitter(recorder, source_id="demo")
    state, report = committer.apply_page(_state(), _page([_event("e1", "obj_1")]), page_size=10)

    assert len(recorder.calls) == 1, "exactly one durable commit per page"
    assert state.cursor_token == "1"
    assert report.committed is True
    assert report.cursor_before is None and report.cursor_after == "1"
    assert state.committed_event_ids == ("e1",)


def test_cursor_does_not_move_when_the_commit_fails():
    committer = SyncCommitter(_Recorder(fail=True), source_id="demo")
    state = _state()
    with pytest.raises(ExtensionError) as excinfo:
        committer.apply_page(state, _page([_event("e1", "obj_1")]), page_size=10)
    assert excinfo.value.code == "E_COMMIT_INCOMPLETE"
    assert state.cursor_token is None, "the caller's state must not absorb the new cursor"
    assert state.committed_event_ids == ()


def test_each_page_produces_a_new_generation_linked_to_the_previous_one():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    first, _ = committer.apply_page(_state(), _page([_event("e1", "obj_1")]), page_size=10)
    second, _ = committer.apply_page(
        first, _page([_event("e2", "obj_2")], next_cursor=None, complete=True), page_size=10
    )
    assert first.generation_id != second.generation_id
    assert second.previous_generation_id == first.generation_id
    assert second.enumeration_complete is True


def test_duplicate_events_are_counted_and_not_reapplied():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(_state(), _page([_event("e1", "obj_1")]), page_size=10)
    _, report = committer.apply_page(state, _page([_event("e1", "obj_1")]), page_size=10)
    assert report.duplicates == 1
    assert report.applied == 0


def test_same_event_id_with_a_different_payload_is_a_collision():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(_state(), _page([_event("e1", "obj_1")]), page_size=10)
    with pytest.raises(ExtensionError) as excinfo:
        committer.apply_page(
            state, _page([_event("e1", "obj_1", payload_hash="f" * 64)]), page_size=10
        )
    assert excinfo.value.code == "E_EVENT_COLLISION"


def test_a_page_from_another_scope_is_refused():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    other = EventPage(
        source_id="demo",
        events=(_event("e1", "obj_1"),),
        next_cursor="1",
        enumeration_complete=False,
        selection_scope_hash="e" * 64,
        authorization_snapshot_ref="authz-1",
    )
    with pytest.raises(ExtensionError) as excinfo:
        committer.apply_page(_state(), other, page_size=10)
    assert excinfo.value.code == "E_SCOPE_DENIED"


def test_a_page_from_another_source_is_refused():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    other = EventPage(
        source_id="other",
        events=(_event("e1", "obj_1"),),
        next_cursor="1",
        enumeration_complete=False,
        selection_scope_hash=SCOPE,
        authorization_snapshot_ref="authz-1",
    )
    with pytest.raises(ExtensionError) as excinfo:
        committer.apply_page(_state(), other, page_size=10)
    assert excinfo.value.code == "E_VERSION_CONFLICT"


# ------------------------------------------------------------- revocation rules
def test_revocation_is_processed_before_other_events_in_the_same_page():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    page = _page(
        [
            _event("e1", "obj_1"),
            _event("r1", "obj_1", "REVOKE", payload_hash="d" * 64),
        ]
    )
    _, report = committer.apply_page(_state(), page, page_size=10)
    assert report.decisions[0].event_id == "r1", "revocations must be applied first"
    assert report.revocations == 1


def test_stale_upsert_after_revocation_cannot_resurrect_access():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(
        _state(),
        _page([_event("r1", "obj_1", "REVOKE")]),
        page_size=10,
    )
    assert state.access.get("demo", "obj_1").state == "REVOKED"

    state, report = committer.apply_page(
        state,
        _page([_event("e9", "obj_1", source_order_token="00000009")], next_cursor="2"),
        page_size=10,
    )
    assert state.access.get("demo", "obj_1").state == "REVOKED"
    assert report.stale == 1
    with pytest.raises(ExtensionError) as excinfo:
        state.access.check_read("demo", "obj_1")
    assert excinfo.value.code == "E_SOURCE_REVOKED"


def test_incomparable_order_is_quarantined_not_trusted():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(
        _state(),
        _page([_event("r1", "obj_1", "REVOKE", source_order_token=None)]),
        page_size=10,
    )
    state, report = committer.apply_page(
        state,
        _page([_event("e9", "obj_1", source_order_token="abc")], next_cursor="2"),
        page_size=10,
    )
    assert report.quarantined == 1
    assert state.access.get("demo", "obj_1").state == "REVOKED"


def test_restore_without_a_regrant_reference_is_quarantined():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(
        _state(), _page([_event("r1", "obj_1", "REVOKE")]), page_size=10
    )
    state, report = committer.apply_page(
        state,
        _page([_event("s1", "obj_1", "RESTORE")], next_cursor="2"),
        page_size=10,
    )
    assert report.quarantined == 1
    assert state.access.get("demo", "obj_1").state == "REVOKED"


def test_restore_with_a_regrant_reference_reactivates_the_object():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(
        _state(), _page([_event("r1", "obj_1", "REVOKE")]), page_size=10
    )
    restored = _event(
        "s1",
        "obj_1",
        "RESTORE",
        source_version_id="sv-2",
        payload_hash="e" * 64,
        authority_decision_ref="authz-regrant-1",
    )
    state, report = committer.apply_page(
        state,
        _page([restored], next_cursor="2"),
        page_size=10,
    )
    assert report.quarantined == 0
    assert state.access.get("demo", "obj_1").state == "ACTIVE"
    assert state.access.get("demo", "obj_1").authority_decision_ref == "authz-regrant-1"
    assert state.access.is_tombstoned("demo", "obj_1") is False


def test_a_restore_without_regrant_evidence_cannot_reopen_access():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(
        _state(), _page([_event("r1", "obj_1", "REVOKE")]), page_size=10
    )
    state, report = committer.apply_page(
        state,
        _page([_event("s1", "obj_1", "RESTORE", payload_hash="e" * 64)], next_cursor="2"),
        page_size=10,
    )
    assert report.quarantined == 1
    assert state.access.get("demo", "obj_1").state == "REVOKED"


def test_tombstones_survive_a_state_round_trip():
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(
        _state(), _page([_event("r1", "obj_1", "REVOKE")]), page_size=10
    )
    revived = SyncState.from_dict(state.as_dict())
    assert revived.access.get("demo", "obj_1").state == "REVOKED"
    assert revived.access.is_tombstoned("demo", "obj_1") is True
    with pytest.raises(ExtensionError):
        revived.access.check_read("demo", "obj_1")


# --------------------------------------------------------------- access checks
def test_unknown_object_read_is_scope_denied():
    with pytest.raises(ExtensionError) as excinfo:
        AccessLedger().check_read("demo", "obj_1")
    assert excinfo.value.code == "E_SCOPE_DENIED"


def test_active_object_is_readable():
    ledger = AccessLedger()
    ledger.apply(
        {
            "source_id": "demo",
            "external_object_id": "obj_1",
            "event_type": "UPSERT",
            "event_id": "e1",
            "source_version_id": "sv-1",
            "source_order_token": "00000001",
            "permission_ref": "perm-1",
            "purpose_scopes": ["PREVISIT"],
        }
    )
    record = ledger.check_read("demo", "obj_1", purpose="PREVISIT")
    assert record.state == "ACTIVE"
    assert record.current_source_version_id == "sv-1"


def test_purpose_outside_the_grant_is_refused():
    ledger = AccessLedger()
    ledger.apply(
        {
            "source_id": "demo",
            "external_object_id": "obj_1",
            "event_type": "UPSERT",
            "event_id": "e1",
            "source_version_id": "sv-1",
            "source_order_token": "00000001",
            "purpose_scopes": ["PREVISIT"],
        }
    )
    with pytest.raises(ExtensionError) as excinfo:
        ledger.check_read("demo", "obj_1", purpose="POSTVISIT")
    assert excinfo.value.code == "E_SCOPE_DENIED"


def test_expired_lease_is_stale_not_silently_extended():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(
        _state(),
        _page([_event("e1", "obj_1", lease_expires_at=past)]),
        page_size=10,
    )
    with pytest.raises(ExtensionError) as excinfo:
        state.access.check_read("demo", "obj_1")
    assert excinfo.value.code == "E_ACCESS_STALE"


def test_a_live_lease_still_allows_the_read():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    committer = SyncCommitter(_Recorder(), source_id="demo")
    state, _ = committer.apply_page(
        _state(),
        _page([_event("e1", "obj_1", lease_expires_at=future)]),
        page_size=10,
    )
    assert state.access.check_read("demo", "obj_1").state == "ACTIVE"


def test_revocation_bumps_the_epoch():
    ledger = AccessLedger()
    ledger.apply(
        {
            "source_id": "demo",
            "external_object_id": "obj_1",
            "event_type": "UPSERT",
            "event_id": "e1",
            "source_version_id": "sv-1",
            "source_order_token": "00000001",
        }
    )
    before = ledger.get("demo", "obj_1").access_epoch
    decision = ledger.apply(
        {
            "source_id": "demo",
            "external_object_id": "obj_1",
            "event_type": "REVOKE",
            "event_id": "r1",
            "source_order_token": "00000002",
        }
    )
    assert decision.epoch == before + 1
    assert ledger.get("demo", "obj_1").current_source_version_id is None
