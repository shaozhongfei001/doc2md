"""Turn a "these candidates are stale" list into a state change (Owner D2).

L35 could only *compute* the list: `correction.apply_correction` returns
``stale_candidate_evidence`` and no code consumed it, so "the evidence was revoked"
was not something the repository could claim. This module is the writer the Owner
asked for — with three properties that decide whether it is safe to run:

**Human-triggered.** Nothing here is called automatically; the caller passes the ids
it decided to revoke, plus the decision that authorises them.

**Auditable.** The report carries ``review_decision_ref``, the reviewer, the reason and
the clock value *the caller supplied*. The module never reads the clock: a timestamp
invented inside a pure function is unreproducible in a replay.

**Idempotent.** Revoking an already-STALE candidate is a no-op that is *reported*
(``already_revoked``), not an error and not a silent skip. Two operators approving the
same correction must not produce a failure, nor a second audit entry.

Two things it deliberately refuses:

* unknown ids are reported, never dropped — a typo must not look like success;
* a malformed batch is rejected **as a whole**. Half-revoked evidence is worse than
  untouched evidence: the remaining half still reads as current.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError
from ..review.authority import ReviewerPolicy

__all__ = ["RevocationReport", "revoke_candidates", "revoke_for_correction",
           "restore_candidates", "RestoreReport"]

#: Written into ``review_state``; ``candidates.REVIEW_STATES`` owns the vocabulary.
STALE = "STALE"


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _review_required(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_REVIEW_REQUIRED", message, detail=detail or None)


@dataclass(frozen=True)
class RevocationReport:
    """What actually changed, and what the caller must look at."""

    revoked: tuple[str, ...] = ()
    already_revoked: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    audit: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.revoked)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "revoked": list(self.revoked),
            "already_revoked": list(self.already_revoked),
            "unknown": list(self.unknown),
            "duplicates": list(self.duplicates),
            "changed": self.changed,
            "audit": dict(self.audit),
        }


def revoke_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    candidate_ids: Sequence[str],
    review_decision_ref: str,
    reviewer: str,
    reason: str,
    at: str,
    policy: ReviewerPolicy | None = None,
) -> tuple[list[dict[str, Any]], RevocationReport]:
    """Return a new candidate list with ``candidate_ids`` marked STALE.

    ``candidates`` is never mutated. ``at`` is an ISO-8601 string supplied by the
    caller; the module does not own time.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise _review_required("revoking evidence must record a reason")
    if not isinstance(review_decision_ref, str) or not review_decision_ref.strip():
        raise _review_required(
            "revoking evidence must name the review decision that authorises it"
        )
    if not isinstance(at, str) or not at.strip():
        raise _input_error("at must be an ISO-8601 timestamp supplied by the caller")
    reviewer = (policy or ReviewerPolicy()).check(reviewer)

    if isinstance(candidate_ids, (str, bytes)) or not isinstance(candidate_ids, Sequence):
        raise _input_error("candidate_ids must be a sequence of ids")
    requested: list[str] = []
    duplicates: list[str] = []
    for position, value in enumerate(candidate_ids):
        if not isinstance(value, str) or not value.strip():
            raise _input_error(
                f"candidate_ids[{position}] must be a non-empty string, got {value!r}"
            )
        value = value.strip()
        if value in requested:
            duplicates.append(value)
            continue
        requested.append(value)
    if not requested:
        raise _input_error("candidate_ids is empty; revoking nothing is not a call to make")

    # Validate the whole batch before touching anything, so a malformed entry cannot
    # leave the registry half-revoked.
    for position, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise _input_error(
                f"candidates[{position}] must be a mapping, got {type(candidate).__name__}"
            )
        if not isinstance(candidate.get("candidate_evidence_id"), str):
            raise _input_error(
                f"candidates[{position}] has no candidate_evidence_id; a registry entry "
                "without an id cannot be revoked by id"
            )

    by_id = {str(item["candidate_evidence_id"]): item for item in candidates}
    updated: list[dict[str, Any]] = [dict(item) for item in candidates]
    position_of = {
        str(item["candidate_evidence_id"]): index for index, item in enumerate(updated)
    }

    revoked: list[str] = []
    already: list[str] = []
    unknown: list[str] = []
    for candidate_id in requested:
        if candidate_id not in by_id:
            unknown.append(candidate_id)
            continue
        index = position_of[candidate_id]
        if updated[index].get("review_state") == STALE:
            already.append(candidate_id)
            continue
        # Keep what was there, so a restore can put it back instead of guessing
        # "UNREVIEWED". A revocation that cannot be undone is a deletion with extra
        # steps; recording the prior state is what makes it a reversible decision.
        updated[index] = {
            **updated[index],
            "previous_review_state": updated[index].get("review_state"),
            "previous_review_decision_ref": updated[index].get("review_decision_ref"),
            "review_state": STALE,
            "review_decision_ref": review_decision_ref.strip(),
            "revoked_at": at.strip(),
            "revoked_by": reviewer,
        }
        revoked.append(candidate_id)

    audit = {
        "reviewer": reviewer,
        "review_decision_ref": review_decision_ref.strip(),
        "reason": reason.strip(),
        "at": at.strip(),
        "requested": len(requested),
        "revoked": list(revoked),
        "already_revoked": list(already),
        "unknown": list(unknown),
        "duplicates": list(duplicates),
    }
    return updated, RevocationReport(
        revoked=tuple(revoked),
        already_revoked=tuple(already),
        unknown=tuple(unknown),
        duplicates=tuple(duplicates),
        audit=audit,
    )


@dataclass(frozen=True)
class RestoreReport:
    restored: tuple[str, ...] = ()
    audit: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {"restored": list(self.restored), "audit": dict(self.audit)}


def revoke_for_correction(
    outcome: Any,
    candidates: Sequence[Mapping[str, Any]],
    *,
    review_decision_ref: str,
    reviewer: str,
    reason: str,
    at: str,
    policy: "ReviewerPolicy | None" = None,
    require_attributable: bool = True,
) -> tuple[list[dict[str, Any]], RevocationReport]:
    """Revoke exactly the candidates a correction reported as stale.

    This is the connection L35 lacked: the list it produced is consumed here instead of
    being recomputed or hand-copied. Two refusals matter:

    * an empty list is a bug at the call site, not a no-op — the correction said nothing
      is stale, so calling the writer means something upstream is wrong;
    * ``unattributable_evidence`` non-empty is refused by default: those candidates were
      examined and could not be attributed, and revoking the rest while staying silent
      would report "all handled" for a list that was not fully handled.
    """
    stale = tuple(getattr(outcome, "stale_candidate_evidence", ()) or ())
    unattributable = tuple(getattr(outcome, "unattributable_evidence", ()) or ())

    if not stale:
        raise _input_error(
            "the correction reported no stale candidate evidence; there is nothing to "
            "revoke and calling the writer would hide that"
        )
    if unattributable and require_attributable:
        raise _review_required(
            "the correction could not attribute some candidate evidence "
            f"{list(unattributable)}; revoking the rest would report a fully handled list "
            "that was only partially handled",
            unattributable=list(unattributable),
        )

    updated, report = revoke_candidates(
        candidates,
        candidate_ids=list(stale),
        review_decision_ref=review_decision_ref,
        reviewer=reviewer,
        reason=reason,
        at=at,
        policy=policy,
    )
    audit = {
        **report.audit,
        "source": "correction",
        "unattributable_not_revoked": list(unattributable),
    }
    return updated, RevocationReport(
        revoked=report.revoked,
        already_revoked=report.already_revoked,
        unknown=report.unknown,
        duplicates=report.duplicates,
        audit=audit,
    )


def restore_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    candidate_ids: Sequence[str],
    review_decision_ref: str,
    reviewer: str,
    reason: str,
    at: str,
    policy: "ReviewerPolicy | None" = None,
) -> tuple[list[dict[str, Any]], RestoreReport]:
    """Undo a revocation, restoring the state recorded at revocation time.

    Guessing is not allowed: an entry without ``previous_review_state`` was not revoked
    by this module, so there is nothing trustworthy to restore to.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise _review_required("restoring evidence must record a reason")
    if not isinstance(review_decision_ref, str) or not review_decision_ref.strip():
        raise _review_required("restoring evidence must name the review decision")
    if not isinstance(at, str) or not at.strip():
        raise _input_error("at must be an ISO-8601 timestamp supplied by the caller")
    reviewer = (policy or ReviewerPolicy()).check(reviewer)
    if isinstance(candidate_ids, (str, bytes)) or not isinstance(candidate_ids, Sequence):
        raise _input_error("candidate_ids must be a sequence of ids")

    wanted = [value.strip() for value in candidate_ids if isinstance(value, str)]
    if not wanted:
        raise _input_error("candidate_ids is empty; restoring nothing is not a call to make")

    working: list[dict[str, Any]] = [dict(item) for item in candidates]
    index_of = {
        str(item.get("candidate_evidence_id")): position
        for position, item in enumerate(working)
        if isinstance(item.get("candidate_evidence_id"), str)
    }

    restored: list[str] = []
    for candidate_id in wanted:
        position = index_of.get(candidate_id)
        if position is None:
            continue
        item = working[position]
        if item.get("review_state") != STALE:
            raise _input_error(
                f"candidate {candidate_id!r} is not STALE; there is no revocation to undo",
                candidate_id=candidate_id,
            )
        if "previous_review_state" not in item:
            raise _input_error(
                f"candidate {candidate_id!r} carries no recorded prior state; restoring "
                "would have to guess what it was",
                candidate_id=candidate_id,
            )
        working[position] = {
            key: value
            for key, value in item.items()
            if key not in ("revoked_at", "revoked_by", "previous_review_state",
                           "previous_review_decision_ref")
        } | {
            "review_state": item["previous_review_state"],
            "review_decision_ref": item.get("previous_review_decision_ref"),
        }
        restored.append(candidate_id)
    if not restored:
        raise _input_error("none of the given ids exist in the registry")

    audit = {
        "reviewer": reviewer,
        "review_decision_ref": review_decision_ref.strip(),
        "reason": reason.strip(),
        "at": at.strip(),
        "restored": list(restored),
    }
    return working, RestoreReport(restored=tuple(restored), audit=audit)
