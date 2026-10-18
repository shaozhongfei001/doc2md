"""Migration reconciliation (SDD 6.3, ``PI-F03-03``).

    "对账分母是源端该scope完整清单；逐对象归为REUSED、REPROCESSED、EXCLUDED_WITH_REASON、
     FAILED、PENDING；总数必须回算一致，不能把失败从分母去掉。"

That sentence is the whole module. The denominator is the source's *complete* list
for the scope; a failed object stays in the denominator and is counted as
``FAILED``. There is no code path that removes a failure to make the numbers add up
— instead, a totals mismatch is itself reported as a blocker.

Also enforced:

* an object produced **outside** the scope is a permission expansion (or a scope
  bug) and blocks the batch; it is not silently ignored;
* a processed version/hash that disagrees with the source is a blocker;
* without ``enumeration_complete`` no delete candidate may be inferred, because a
  truncated page listing looks exactly like a deletion (SDD 6.2/6.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

STATUSES = ("REUSED", "REPROCESSED", "EXCLUDED_WITH_REASON", "FAILED", "PENDING")

__all__ = ["STATUSES", "ReconcileReport", "reconcile"]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class ReconcileReport:
    scope_id: str
    denominator: int
    counts: Mapping[str, int]
    rows: list[dict[str, Any]]
    blockers: list[dict[str, Any]] = field(default_factory=list)
    delete_candidates: list[str] = field(default_factory=list)
    enumeration_complete: bool = True

    @property
    def clean(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "denominator": self.denominator,
            "counts": dict(self.counts),
            "blockers": [dict(b) for b in self.blockers],
            "delete_candidates": list(self.delete_candidates),
            "enumeration_complete": self.enumeration_complete,
            "rows": [dict(r) for r in self.rows],
        }


def _source_row(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise _input_error(f"scope_objects[{index}] must be a mapping")
    object_id = raw.get("object_id")
    if not isinstance(object_id, str) or not object_id:
        raise _input_error(f"scope_objects[{index}] has no usable object_id")
    version = raw.get("source_version_id")
    if not isinstance(version, str) or not version:
        raise _input_error(f"scope object {object_id}: source_version_id is required")
    digest = raw.get("content_sha256")
    if digest is not None and (not isinstance(digest, str) or len(digest) != 64):
        raise _input_error(
            f"scope object {object_id}: content_sha256 must be a 64-char digest or null"
        )
    return {
        "object_id": object_id,
        "source_version_id": version,
        "content_sha256": digest,
        "access_revision": raw.get("access_revision"),
    }


def reconcile(
    *,
    scope_id: str,
    scope_objects: Sequence[Mapping[str, Any]],
    processed: Mapping[str, Mapping[str, Any]],
    enumeration_complete: bool = True,
) -> ReconcileReport:
    """Classify every object of the scope, keeping failures in the denominator."""
    if not isinstance(scope_id, str) or not scope_id:
        raise _input_error(f"scope_id must be a non-empty string, got {scope_id!r}")
    if not isinstance(enumeration_complete, bool):
        raise _input_error(
            f"enumeration_complete must be an explicit bool, got {enumeration_complete!r}"
        )
    if isinstance(scope_objects, (str, bytes)) or not isinstance(scope_objects, Sequence):
        raise _input_error("scope_objects must be a sequence of source objects")
    if not isinstance(processed, Mapping):
        raise _input_error("processed must be a mapping object_id -> result")

    sources = [_source_row(raw, index) for index, raw in enumerate(scope_objects)]
    source_ids = {row["object_id"] for row in sources}
    if len(source_ids) != len(sources):
        raise _input_error("scope_objects contains duplicate object ids")

    blockers: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    if not enumeration_complete:
        # A truncated listing is not evidence of deletion.
        blockers.append(
            {
                "code": "ENUMERATION_INCOMPLETE",
                "message": (
                    "the source listing is incomplete; no delete candidate may be inferred "
                    "and the batch is not reconcilable"
                ),
            }
        )

    for row in sources:
        object_id = row["object_id"]
        result = processed.get(object_id)
        if result is None:
            rows.append({**row, "status": "PENDING", "reason": "not processed yet"})
            continue
        if not isinstance(result, Mapping):
            raise _input_error(f"processed[{object_id!r}] must be a mapping")
        status = result.get("status")
        if status not in STATUSES:
            raise _input_error(
                f"processed[{object_id!r}].status must be one of {list(STATUSES)}, got "
                f"{status!r}"
            )
        reason = result.get("reason")
        if status == "EXCLUDED_WITH_REASON" and (not isinstance(reason, str) or not reason.strip()):
            raise _input_error(
                f"object {object_id}: EXCLUDED_WITH_REASON requires a recorded reason"
            )
        processed_version = result.get("source_version_id")
        if processed_version is not None and processed_version != row["source_version_id"]:
            blockers.append(
                {
                    "code": "WRONG_VERSION",
                    "object_id": object_id,
                    "message": (
                        f"processed version {processed_version!r} differs from the source "
                        f"version {row['source_version_id']!r}"
                    ),
                }
            )
        digest = result.get("content_sha256")
        if digest is not None and row["content_sha256"] is not None and digest != row["content_sha256"]:
            blockers.append(
                {
                    "code": "HASH_MISMATCH",
                    "object_id": object_id,
                    "message": "processed content hash differs from the source hash",
                }
            )
        rows.append({**row, "status": status, "reason": reason or result.get("reason")})

    outside = [object_id for object_id in processed if object_id not in source_ids]
    if outside:
        blockers.append(
            {
                "code": "PERMISSION_EXPANSION",
                "message": (
                    "results exist for objects outside the reconcilable scope; that is an "
                    "access expansion or a scope bug, never silently ignored"
                ),
                "object_ids": sorted(outside),
            }
        )

    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] += 1

    total = sum(counts.values())
    if total != len(sources):
        # Defensive: a classification that loses a row must fail loudly, because the
        # next reader would trust these totals.
        blockers.append(
            {
                "code": "DENOMINATOR_MISMATCH",
                "message": "classified rows do not add up to the denominator",
                "denominator": len(sources),
                "classified": total,
            }
        )

    # An object present in the previous generation but absent from a *complete*
    # listing is a delete candidate; this only proposes, the caller decides. With an
    # incomplete listing there are no delete candidates at all.
    delete_candidates = (
        sorted(set(processed) - source_ids) if enumeration_complete else []
    )

    return ReconcileReport(
        scope_id=scope_id,
        denominator=len(sources),
        counts=counts,
        rows=rows,
        blockers=blockers,
        delete_candidates=delete_candidates,
        enumeration_complete=enumeration_complete,
    )
