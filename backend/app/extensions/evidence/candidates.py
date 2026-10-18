"""Candidate evidence with permission checks on every read (SDD 11.3, ``PI-F05-06``).

The three observation classes are kept apart on purpose:

* ``SOURCE_CONTENT`` — what is visibly there. It is **not** an approved business fact.
* ``MODEL_INTERPRETATION`` — an inference. It never overwrites the visible content.
* ``HUMAN_TRANSCRIPTION`` — a person's reading, recorded separately with its decision.

Two rules drive the API:

1. *"来源撤回/替换后通过依赖表精确标STALE，读取守卫立即拒绝相关内容"* — so
   :func:`read_candidate` asks the caller for the **current** permission on every
   call. There is no cached "was allowed" flag to go stale.
2. *"无法回指具体region的精确数据/箭头关系不进入candidate evidence"* — a candidate
   needs a locator that can be pointed at. A payload without one is refused, not
   stored with an empty locator.

Error codes stay inside the fixed table: ``E_SCOPE_DENIED`` (source or permission
denied, exit 7) and ``E_INPUT_INVALID``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .. import ExtensionError

OBSERVATION_CLASSES = ("SOURCE_CONTENT", "MODEL_INTERPRETATION", "HUMAN_TRANSCRIPTION")
REVIEW_STATES = ("UNREVIEWED", "ACCEPTED", "REJECTED", "STALE")
LOCATOR_KINDS = ("DOCX_XML", "PAGE_REGION", "TEXT_SPAN")

__all__ = [
    "LOCATOR_KINDS",
    "OBSERVATION_CLASSES",
    "REVIEW_STATES",
    "AccessGuard",
    "mark_stale",
    "read_candidate",
    "register_candidate",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _denied(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_SCOPE_DENIED", message, detail=detail or None)


@dataclass(frozen=True)
class AccessGuard:
    """Current permission for one source version, asked fresh on each read.

    ``allowed`` is a callable so a long-running job cannot hold a stale boolean:
    a revocation that happened five minutes ago must affect the next export.
    """

    source_version_id: str
    allowed: Callable[[], bool]
    revision: str | None = None

    def require(self) -> None:
        if not self.allowed():
            raise _denied(
                "the source version is not currently readable (revoked, stale or "
                "out-of-scope); the candidate is not exported",
                source_version_id=self.source_version_id,
                revision=self.revision,
            )


def _locator(locator: Any, *, evidence_id: str) -> dict[str, Any]:
    if not isinstance(locator, Mapping):
        raise _input_error(
            f"candidate {evidence_id}: locator must be a mapping; precise data that cannot "
            "be pointed at a region does not enter candidate evidence"
        )
    kind = locator.get("kind")
    if kind not in LOCATOR_KINDS:
        raise _input_error(
            f"candidate {evidence_id}: locator.kind must be one of {list(LOCATOR_KINDS)}, "
            f"got {kind!r}"
        )
    digest = locator.get("source_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise _input_error(
            f"candidate {evidence_id}: locator.source_sha256 must be the 64-char digest of "
            "the original content"
        )
    detail = dict(locator)
    detail["source_sha256"] = digest.lower()
    return detail


def register_candidate(
    *,
    candidate_evidence_id: str,
    source_version_id: str,
    locator: Mapping[str, Any],
    observation_class: str,
    raw_engine_ref: str | None = None,
    quote: str | None = None,
    crop_ref: str | None = None,
) -> dict[str, Any]:
    """Create one candidate evidence entry.

    ``observation_class`` is stored as given and is never promoted: a
    ``MODEL_INTERPRETATION`` cannot become ``SOURCE_CONTENT`` by being accepted.
    """
    for name, value in (
        ("candidate_evidence_id", candidate_evidence_id),
        ("source_version_id", source_version_id),
    ):
        if not isinstance(value, str) or not value:
            raise _input_error(f"{name} must be a non-empty string, got {value!r}")
    if observation_class not in OBSERVATION_CLASSES:
        raise _input_error(
            f"observation_class must be one of {list(OBSERVATION_CLASSES)}, got "
            f"{observation_class!r}"
        )
    for name, value in (("raw_engine_ref", raw_engine_ref), ("quote", quote), ("crop_ref", crop_ref)):
        if value is not None and not isinstance(value, str):
            raise _input_error(f"{name} must be a string or null")

    return {
        "candidate_evidence_id": candidate_evidence_id,
        "source_version_id": source_version_id,
        "locator": _locator(locator, evidence_id=candidate_evidence_id),
        "raw_engine_ref": raw_engine_ref,
        "quote": quote,
        "crop_ref": crop_ref,
        "observation_class": observation_class,
        "review_state": "UNREVIEWED",
        "review_decision_ref": None,
    }


def read_candidate(
    candidate: Mapping[str, Any],
    *,
    guard: AccessGuard,
    purpose: str = "read",
) -> dict[str, Any]:
    """Return the candidate **only** if the source is currently readable.

    The check is inside this function rather than at the call site so that no
    export path can forget it.
    """
    if not isinstance(candidate, Mapping):
        raise _input_error(f"candidate must be a mapping, got {type(candidate).__name__}")
    if not isinstance(guard, AccessGuard):
        raise _input_error(
            "read_candidate requires an AccessGuard; a cached 'allowed' flag cannot "
            "reflect a revocation that already happened"
        )
    if not isinstance(purpose, str) or not purpose:
        raise _input_error("purpose must name what the candidate is read for")

    source_version_id = candidate.get("source_version_id")
    if source_version_id != guard.source_version_id:
        raise _input_error(
            "the guard is for a different source version than the candidate",
            candidate=source_version_id,
            guard=guard.source_version_id,
        )
    if candidate.get("review_state") == "STALE":
        raise _denied(
            "candidate is marked STALE; stale content is not exported",
            candidate_evidence_id=candidate.get("candidate_evidence_id"),
        )
    guard.require()
    return dict(candidate)


def mark_stale(
    candidates: Sequence[Mapping[str, Any]], *, revoked_source_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Mark every candidate of a revoked source ``STALE``.

    Nothing is deleted: a stale candidate still explains why a previously exported
    answer existed.
    """
    if isinstance(revoked_source_ids, (str, bytes)):
        raise _input_error("revoked_source_ids must be a sequence, not a bare string")
    revoked = {source for source in revoked_source_ids if isinstance(source, str)}
    marked: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise _input_error("candidates entries must be mappings")
        updated = dict(candidate)
        if updated.get("source_version_id") in revoked:
            updated["review_state"] = "STALE"
        marked.append(updated)
    return marked
