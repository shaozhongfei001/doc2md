"""Inventory reconciliation and shadow routing (T205).

Hard requirements
-----------------
* The **denominator is the source inventory**, not "the objects we managed to
  process". A failed or pending object stays in the denominator; the categories must
  sum back to the total or the reconciliation is refused.
* **No delete candidate without a complete enumeration.** A truncated list, an
  expired page token or a user-cancelled scope never become "the source deleted it".
* **LOCAL_READY is not ACTIVE.** This module can produce a shadow route decision; it
  cannot claim a bank cutover, and a source without an interface is reported as
  ``NOT_CONNECTED`` rather than as a successful empty reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

__all__ = [
    "RECONCILE_CATEGORIES",
    "ReconcileItem",
    "ReconcileReport",
    "reconcile_inventory",
    "shadow_route_decision",
]

RECONCILE_CATEGORIES = (
    "REUSED",
    "REPROCESSED",
    "EXCLUDED_WITH_REASON",
    "FAILED",
    "PENDING",
)


@dataclass(frozen=True)
class ReconcileItem:
    external_object_id: str
    category: str
    reason: str
    source_version_id: str | None = None
    delete_candidate: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_object_id": self.external_object_id,
            "category": self.category,
            "reason": self.reason,
            "source_version_id": self.source_version_id,
            "delete_candidate": self.delete_candidate,
        }


@dataclass(frozen=True)
class ReconcileReport:
    source_id: str
    denominator: int
    counts: dict[str, int]
    items: tuple[ReconcileItem, ...]
    enumeration_complete: bool
    delete_candidates: tuple[str, ...]
    state: str  # RECONCILED | NOT_CONNECTED | INCOMPLETE | BLOCKED
    blockers: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def sums_to_denominator(self) -> bool:
        return sum(self.counts.values()) == self.denominator

    @property
    def ready_for_release(self) -> bool:
        return self.state == "RECONCILED" and not self.blockers

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "denominator": self.denominator,
            "counts": self.counts,
            "enumeration_complete": self.enumeration_complete,
            "delete_candidates": list(self.delete_candidates),
            "state": self.state,
            "blockers": list(self.blockers),
            "sums_to_denominator": self.sums_to_denominator,
            "ready_for_release": self.ready_for_release,
            "detail": self.detail,
            "items": [item.as_dict() for item in self.items],
        }


def reconcile_inventory(
    *,
    source_id: str,
    inventory: Sequence[Mapping[str, Any]],
    ledger: Mapping[str, Mapping[str, Any]],
    enumeration_complete: bool,
    connected: bool = True,
    expected_total: int | None = None,
) -> ReconcileReport:
    """Classify every inventory object and prove the counts add up.

    ``inventory`` is what the source says exists right now; ``ledger`` is what this
    project holds, keyed by ``external_object_id``. A ledger entry missing from the
    inventory becomes a delete candidate **only** when enumeration is complete.
    """
    if not connected:
        return ReconcileReport(
            source_id=source_id,
            denominator=len(inventory),
            counts={category: 0 for category in RECONCILE_CATEGORIES},
            items=(),
            enumeration_complete=False,
            delete_candidates=(),
            state="NOT_CONNECTED",
            blockers=("SOURCE_NOT_CONNECTED",),
            detail={
                "note": "no interface for this source; nothing was reconciled and nothing "
                        "is reported as deleted"
            },
        )

    if expected_total is not None and expected_total != len(inventory):
        return ReconcileReport(
            source_id=source_id,
            denominator=len(inventory),
            counts={category: 0 for category in RECONCILE_CATEGORIES},
            items=(),
            enumeration_complete=enumeration_complete,
            delete_candidates=(),
            state="INCOMPLETE",
            blockers=("INVENTORY_MISMATCH",),
            detail={"expected_total": expected_total, "received": len(inventory)},
        )

    counts = {category: 0 for category in RECONCILE_CATEGORIES}
    items: list[ReconcileItem] = []
    seen: set[str] = set()

    for raw in inventory:
        object_id = str(raw.get("external_object_id") or "")
        if not object_id:
            raise ExtensionError(
                "E_INPUT_INVALID", "inventory entry has no external_object_id"
            )
        if object_id in seen:
            raise ExtensionError(
                "E_INPUT_INVALID",
                f"inventory lists {object_id!r} twice; a duplicate would distort the denominator",
            )
        seen.add(object_id)

        entry = ledger.get(object_id) or {}
        category, reason = _classify(raw, entry)
        counts[category] += 1
        items.append(
            ReconcileItem(
                external_object_id=object_id,
                category=category,
                reason=reason,
                source_version_id=entry.get("source_version_id"),
            )
        )

    missing = [key for key in ledger if key not in seen]
    delete_candidates: list[str] = []
    blockers: list[str] = []
    if missing:
        if enumeration_complete:
            delete_candidates = sorted(missing)
        else:
            blockers.append("ENUMERATION_INCOMPLETE")

    total = len(inventory)
    if sum(counts.values()) != total:  # pragma: no cover - defensive invariant
        raise ExtensionError(
            "E_INPUT_INVALID",
            f"category counts {counts} do not sum to the denominator {total}",
        )

    state = "RECONCILED" if not blockers else "INCOMPLETE"
    return ReconcileReport(
        source_id=source_id,
        denominator=total,
        counts=counts,
        items=tuple(items),
        enumeration_complete=enumeration_complete,
        delete_candidates=tuple(delete_candidates),
        state=state,
        blockers=tuple(blockers),
        detail={"ledger_entries": len(ledger), "missing_from_inventory": len(missing)},
    )


def _classify(raw: Mapping[str, Any], entry: Mapping[str, Any]) -> tuple[str, str]:
    route = raw.get("processing_route") or entry.get("processing_route")
    if raw.get("excluded_reason"):
        return "EXCLUDED_WITH_REASON", str(raw["excluded_reason"])
    if raw.get("failed_reason"):
        return "FAILED", str(raw["failed_reason"])
    if not entry:
        return "PENDING", "not processed yet"
    if route == "REUSE_QUERY":
        if not raw.get("legacy_query_ref") and not entry.get("legacy_query_ref"):
            return "EXCLUDED_WITH_REASON", "reuse route without an authorised query reference"
        return "REUSED", "served from the legacy query path"
    if route == "METADATA_ONLY":
        return "EXCLUDED_WITH_REASON", "metadata-only by policy"
    if route == "REPROCESS":
        if entry.get("generation_ids"):
            return "REPROCESSED", "reprocessed into a new generation"
        return "PENDING", "reprocess route selected but no generation committed"
    return "PENDING", "processing route not decided"


def shadow_route_decision(
    *,
    scope: str,
    expected_previous: str | None,
    target_release: str,
    local_ready: bool,
    required_consumers: Sequence[str],
    acknowledged_consumers: Sequence[str],
) -> dict[str, Any]:
    """Decide whether a *shadow* route could move — never claim a real cutover."""
    missing = sorted(set(required_consumers) - set(acknowledged_consumers))
    blockers: list[str] = []
    if not local_ready:
        blockers.append("LOCAL_NOT_READY")
    if missing:
        blockers.append("CONSUMER_ACK_MISSING")
    blockers.append("BANK_CUTOVER_NOT_AUTHORISED")
    return {
        "scope": scope,
        "expected_previous": expected_previous,
        "target_release": target_release,
        "local_state": "LOCAL_READY" if local_ready else "PREPARED",
        "release_state": "BLOCKED" if blockers else "ACTIVE",
        "missing_consumer_acks": missing,
        "blockers": blockers,
        "note": (
            "shadow decision only: switching the real FULLTEXT/bank route needs the "
            "consumer's own control plane and acknowledgements (open input OI08)"
        ),
    }
