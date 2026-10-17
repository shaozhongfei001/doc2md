"""``SourceAdapter`` protocol and capability probing.

Contract basis: ``01_SDD.md`` §5 IF01/IF02, §6.2; T201.

Design rules encoded here
-------------------------
* **Capability is declared *and* probed.** A source that cannot report deletions
  must say so; an empty event list is never evidence that nothing changed.
* **No adapter may claim a cursor it does not have.** ``cursor_supported=False``
  forces the caller into full enumeration instead of a silent incremental run.
* **A capability gap is ``UNSUPPORTED``, not success.** This is the L02 analogue of
  the L01 ``NEEDS_BASE_PARSE`` rule.

There is deliberately **no** HTTP client here. The only shipped adapter reads a
local manifest file; a bank connector can only be added once its interface,
authorisation and error semantics are known (open input OI02).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable

from .. import ExtensionError

__all__ = [
    "SourceCapabilities",
    "CapabilityReport",
    "SourceEventPayload",
    "EventPage",
    "SourceAdapter",
    "assert_page_contract",
]

EVENT_TYPES = ("UPSERT", "REVOKE", "DELETE", "RESTORE")


@dataclass(frozen=True)
class SourceCapabilities:
    """What an adapter can actually do, as declared by the adapter itself."""

    incremental_events: bool
    cursor: bool
    full_enumeration: bool
    reports_deletions: bool
    reports_revocations: bool
    authorization_lease: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "incremental_events": self.incremental_events,
            "cursor": self.cursor,
            "full_enumeration": self.full_enumeration,
            "reports_deletions": self.reports_deletions,
            "reports_revocations": self.reports_revocations,
            "authorization_lease": self.authorization_lease,
        }


@dataclass(frozen=True)
class CapabilityReport:
    """Result of probing an adapter: declared capability vs observed behaviour."""

    adapter: str
    source_id: str
    declared: SourceCapabilities
    reachable: bool
    state: str  # CONNECTED | NOT_CONNECTED | UNSUPPORTED | PROBE_FAILED
    gaps: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def usable_for_sync(self) -> bool:
        return self.state == "CONNECTED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "source_id": self.source_id,
            "reachable": self.reachable,
            "state": self.state,
            "declared": self.declared.as_dict(),
            "gaps": list(self.gaps),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SourceEventPayload:
    """One event as delivered by an adapter, before validation/commit."""

    event_id: str
    external_object_id: str
    event_type: str
    source_version_id: str | None
    payload_hash: str
    source_order_token: str | None
    access_revision: str
    observed_at: str
    #: Regrant evidence. A ``RESTORE`` without it must not re-open access.
    authority_decision_ref: str | None = None
    #: Optional lease carried by the source; an expired lease is refused at read time.
    lease_expires_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "external_object_id": self.external_object_id,
            "event_type": self.event_type,
            "source_version_id": self.source_version_id,
            "payload_hash": self.payload_hash,
            "source_order_token": self.source_order_token,
            "access_revision": self.access_revision,
            "observed_at": self.observed_at,
            "authority_decision_ref": self.authority_decision_ref,
            "lease_expires_at": self.lease_expires_at,
        }


@dataclass(frozen=True)
class EventPage:
    """One atomic unit of synchronisation: events + the cursor that follows them."""

    source_id: str
    events: tuple[SourceEventPayload, ...]
    next_cursor: str | None
    enumeration_complete: bool
    selection_scope_hash: str
    authorization_snapshot_ref: str

    @property
    def is_empty(self) -> bool:
        return not self.events


@runtime_checkable
class SourceAdapter(Protocol):
    """Minimal adapter surface. Implementations must not invent capabilities."""

    adapter_name: str

    def capabilities(self) -> SourceCapabilities:  # pragma: no cover - protocol
        ...

    def probe(self) -> CapabilityReport:  # pragma: no cover - protocol
        ...

    def fetch_page(
        self, *, cursor: str | None, page_size: int, scope: dict[str, Any] | None = None
    ) -> EventPage:  # pragma: no cover - protocol
        ...

    def enumerate_all(self, *, scope: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        ...  # pragma: no cover - protocol


def assert_page_contract(page: EventPage, *, page_size: int) -> EventPage:
    """Validate a page before it can reach the ledger.

    An adapter that violates these rules is a defect, not a data condition, so the
    error is an engine-contract error (exit 3) rather than a retryable transport one.
    """
    if not page.source_id:
        raise ExtensionError("E_ENGINE_SCHEMA_DRIFT", "event page carries no source_id")
    if len(page.events) > page_size:
        raise ExtensionError(
            "E_ENGINE_SCHEMA_DRIFT",
            f"adapter returned {len(page.events)} events for page_size={page_size}",
        )
    seen: set[str] = set()
    for event in page.events:
        if event.event_type not in EVENT_TYPES:
            raise ExtensionError(
                "E_ENGINE_SCHEMA_DRIFT",
                f"unknown event_type {event.event_type!r}; expected one of {EVENT_TYPES}",
            )
        if event.event_id in seen:
            raise ExtensionError(
                "E_ENGINE_SCHEMA_DRIFT",
                f"adapter returned duplicate event_id {event.event_id!r} inside one page",
            )
        seen.add(event.event_id)
        if event.event_type in ("UPSERT", "RESTORE") and not event.source_version_id:
            raise ExtensionError(
                "E_ENGINE_SCHEMA_DRIFT",
                f"{event.event_type} event {event.event_id!r} carries no source_version_id",
            )
    if page.events and page.next_cursor is None and not page.enumeration_complete:
        raise ExtensionError(
            "E_ENGINE_SCHEMA_DRIFT",
            "page has events but neither a next cursor nor an enumeration-complete flag; "
            "the caller would loop forever",
        )
    return page


def capability_gaps(caps: SourceCapabilities) -> Iterable[str]:
    """Capabilities a *complete* bank-grade source would need but this one lacks."""
    if not caps.incremental_events:
        yield "NO_INCREMENTAL_EVENTS"
    if not caps.cursor:
        yield "NO_CURSOR"
    if not caps.full_enumeration:
        yield "NO_FULL_ENUMERATION"
    if not caps.reports_deletions:
        yield "NO_DELETE_REPORTING"
    if not caps.reports_revocations:
        yield "NO_REVOCATION_REPORTING"
