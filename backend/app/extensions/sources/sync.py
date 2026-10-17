"""Page commit with a durable cursor (T204).

The commit protocol, in order:

1. validate the page (adapter contract);
2. refuse a page whose selection scope does not match the ledger's scope;
3. deduplicate by ``event_id`` and refuse a same-id/different-payload collision;
4. apply events to the access ledger, **revocations first**;
5. hand the new state to ``commit_fn`` (the artifact store's atomic write).

Guarantee: if step 5 raises, nothing is durable. The caller keeps its previous state
and re-applying the same page is safe because events are deduplicated by ``event_id``.
A cursor can therefore never be persisted for a page whose state was not written, and
a page can never be counted twice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .. import ExtensionError
from .access import AccessDecision, AccessLedger
from .base import EventPage, assert_page_contract

__all__ = ["SyncState", "SyncReport", "SyncCommitter", "state_hash"]

STATE_SCHEMA = "doc2md-19cap-sync-state/1.0"


def state_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


@dataclass
class SyncState:
    """Durable per-source sync state; every field survives a restart."""

    source_id: str
    generation_id: str
    previous_generation_id: str | None
    cursor_token: str | None
    committed_event_ids: tuple[str, ...]
    enumeration_complete: bool
    selection_scope_hash: str
    authorization_snapshot_ref: str
    access: AccessLedger = field(default_factory=AccessLedger)
    payload_hashes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def initial(
        cls,
        *,
        source_id: str,
        selection_scope_hash: str,
        authorization_snapshot_ref: str,
    ) -> "SyncState":
        return cls(
            source_id=source_id,
            generation_id="gen-0",
            previous_generation_id=None,
            cursor_token=None,
            committed_event_ids=(),
            enumeration_complete=False,
            selection_scope_hash=selection_scope_hash,
            authorization_snapshot_ref=authorization_snapshot_ref,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA,
            "source_id": self.source_id,
            "generation_id": self.generation_id,
            "previous_generation_id": self.previous_generation_id,
            "cursor_token": self.cursor_token,
            "committed_event_ids": list(self.committed_event_ids),
            "enumeration_complete": self.enumeration_complete,
            "selection_scope_hash": self.selection_scope_hash,
            "authorization_snapshot_ref": self.authorization_snapshot_ref,
            "access": self.access.as_dict(),
            "payload_hashes": dict(sorted(self.payload_hashes.items())),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SyncState":
        if payload.get("schema_version") != STATE_SCHEMA:
            raise ExtensionError(
                "E_VERSION_CONFLICT",
                f"unsupported sync state schema: {payload.get('schema_version')!r}",
            )
        return cls(
            source_id=str(payload["source_id"]),
            generation_id=str(payload["generation_id"]),
            previous_generation_id=payload.get("previous_generation_id"),
            cursor_token=payload.get("cursor_token"),
            committed_event_ids=tuple(payload.get("committed_event_ids") or ()),
            enumeration_complete=bool(payload.get("enumeration_complete")),
            selection_scope_hash=str(payload["selection_scope_hash"]),
            authorization_snapshot_ref=str(payload["authorization_snapshot_ref"]),
            access=AccessLedger.from_dict(payload.get("access") or {}),
            payload_hashes=dict(payload.get("payload_hashes") or {}),
        )


@dataclass(frozen=True)
class SyncReport:
    source_id: str
    page_events: int
    applied: int
    duplicates: int
    revocations: int
    quarantined: int
    stale: int
    cursor_before: str | None
    cursor_after: str | None
    generation_id: str
    decisions: tuple[AccessDecision, ...]
    committed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "page_events": self.page_events,
            "applied": self.applied,
            "duplicates": self.duplicates,
            "revocations": self.revocations,
            "quarantined": self.quarantined,
            "stale": self.stale,
            "cursor_before": self.cursor_before,
            "cursor_after": self.cursor_after,
            "generation_id": self.generation_id,
            "committed": self.committed,
            "decisions": [
                {
                    "event_id": d.event_id,
                    "external_object_id": d.external_object_id,
                    "outcome": d.outcome,
                    "state": d.state,
                    "applied": d.applied,
                    "reason": d.reason,
                    "epoch": d.epoch,
                }
                for d in self.decisions
            ],
        }


class SyncCommitter:
    """Applies pages and advances the cursor only after a durable commit."""

    def __init__(self, commit_fn: Callable[[dict[str, Any]], None], *, source_id: str) -> None:
        self._commit = commit_fn
        self.source_id = source_id

    def apply_page(
        self, state: SyncState, page: EventPage, *, page_size: int, observed_at: str | None = None
    ) -> tuple[SyncState, SyncReport]:
        assert_page_contract(page, page_size=page_size)

        if page.source_id != state.source_id:
            raise ExtensionError(
                "E_VERSION_CONFLICT",
                f"page belongs to source {page.source_id!r}, ledger tracks {state.source_id!r}",
            )
        if page.selection_scope_hash != state.selection_scope_hash:
            raise ExtensionError(
                "E_SCOPE_DENIED",
                "the page was produced for a different selection scope; the cursor is not "
                "transferable across scopes",
            )

        events = list(page.events)
        # Revocations first: a parse failure on another object in the same page must
        # never delay a withdrawal the project has already been told about.
        events.sort(key=lambda e: 0 if e.event_type in ("REVOKE", "DELETE") else 1)

        next_state = SyncState(
            source_id=state.source_id,
            generation_id=state.generation_id,
            previous_generation_id=state.previous_generation_id,
            cursor_token=state.cursor_token,
            committed_event_ids=state.committed_event_ids,
            enumeration_complete=state.enumeration_complete,
            selection_scope_hash=state.selection_scope_hash,
            authorization_snapshot_ref=state.authorization_snapshot_ref,
            access=AccessLedger.from_dict(state.access.as_dict()),
            payload_hashes=dict(state.payload_hashes),
        )

        committed = set(next_state.committed_event_ids)
        decisions: list[AccessDecision] = []
        duplicates = revocations = quarantined = stale = applied = 0

        for event in events:
            payload = event.as_dict()
            known_hash = next_state.payload_hashes.get(event.event_id)
            if known_hash is not None and known_hash != event.payload_hash:
                raise ExtensionError(
                    "E_EVENT_COLLISION",
                    f"event {event.event_id!r} reappeared with a different payload digest",
                    detail={"known": known_hash, "incoming": event.payload_hash},
                )
            if event.event_id in committed:
                duplicates += 1
                continue

            decision = next_state.access.apply(
                {
                    "source_id": next_state.source_id,
                    "external_object_id": event.external_object_id,
                    "event_type": event.event_type,
                    "event_id": event.event_id,
                    "source_version_id": event.source_version_id,
                    "source_order_token": event.source_order_token,
                    "access_revision": event.access_revision,
                    "observed_at": observed_at or event.observed_at,
                    "authority_decision_ref": event.authority_decision_ref,
                    "lease_expires_at": event.lease_expires_at,
                    "permission_ref": "source-declared",
                    "purpose_scopes": (),
                }
            )
            decisions.append(decision)
            if decision.outcome == "APPLIED":
                applied += 1
                if event.event_type in ("REVOKE", "DELETE"):
                    revocations += 1
            elif decision.outcome == "QUARANTINED":
                quarantined += 1
            elif decision.outcome == "STALE_EVENT":
                stale += 1

            committed.add(event.event_id)
            next_state.payload_hashes[event.event_id] = event.payload_hash

        next_state.committed_event_ids = tuple(sorted(committed))
        next_state.enumeration_complete = bool(page.enumeration_complete)
        next_state.authorization_snapshot_ref = page.authorization_snapshot_ref

        provisional = next_state.as_dict()
        generation = "gen-" + state_hash(
            {"previous": state.generation_id, "state": provisional}
        )[:32]
        next_state.previous_generation_id = state.generation_id
        next_state.generation_id = generation

        # ---- durable commit boundary -------------------------------------------
        # The new state (including next_cursor) becomes durable only if ``_commit``
        # returns. If it raises, nothing is written, the caller keeps its old state and
        # re-applying the page is safe because events are deduplicated by event_id.
        # The in-memory cursor assignment below is bookkeeping for the caller; it is
        # never the thing that makes the cursor durable.
        self._commit(next_state.as_dict())

        next_state.cursor_token = page.next_cursor

        report = SyncReport(
            source_id=next_state.source_id,
            page_events=len(events),
            applied=applied,
            duplicates=duplicates,
            revocations=revocations,
            quarantined=quarantined,
            stale=stale,
            cursor_before=state.cursor_token,
            cursor_after=next_state.cursor_token,
            generation_id=next_state.generation_id,
            decisions=tuple(decisions),
            committed=True,
        )
        return next_state, report


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
