"""Revocation as an append-only mark stream, not as a new generation (Owner 2026-10-19).

The owner's framing is the design: *revocation is a label*. Two consequences follow, and
both are implemented here rather than argued about in prose.

**The generation does not change.** Marking evidence stale says nothing about the
document's content, so bumping the generation id would make "version" mean two things at
once and every consumer would have to re-check whether the content actually moved.

**There is no second ledger.** A separate registry file would be a second source of truth
for the same fact — the failure mode this project already paid for in L23–L27. The marks
live with the generation they describe.

What *does* change is a write model: the log is append-only. ``revoke`` and ``restore`` are
both marks; the current state is a fold over the stream. Nothing is rewritten, so the
history cannot be edited into agreement with a later story.

Concurrency: one ``flock`` per log file, held across "read the last sequence number →
append → fsync", so two writers cannot hand out the same ``seq`` or interleave a line.
"""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .. import ExtensionError

__all__ = [
    "REVOKE",
    "RESTORE",
    "ACTIONS",
    "Mark",
    "append_mark",
    "read_marks",
    "fold_marks",
]

REVOKE = "REVOKE"
RESTORE = "RESTORE"
ACTIONS = (REVOKE, RESTORE)


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class Mark:
    seq: int
    action: str
    candidate_evidence_id: str
    reviewer: str
    review_decision_ref: str
    reason: str
    at: str
    recorded_at: str
    previous_state: str | None = None
    previous_decision_ref: str | None = None

    def to_line(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "action": self.action,
                "candidate_evidence_id": self.candidate_evidence_id,
                "reviewer": self.reviewer,
                "review_decision_ref": self.review_decision_ref,
                "reason": self.reason,
                "at": self.at,
                "recorded_at": self.recorded_at,
                "previous_state": self.previous_state,
                "previous_decision_ref": self.previous_decision_ref,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def read_marks(path: pathlib.Path | str) -> list[dict[str, Any]]:
    target = pathlib.Path(path)
    if not target.exists():
        return []
    marks: list[dict[str, Any]] = []
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            marks.append(json.loads(line))
        except json.JSONDecodeError as exc:
            # A half-written line would be a torn append; refusing is the only option that
            # does not silently drop a revocation.
            raise _input_error(f"{target}:{number} is not valid JSON: {exc}") from exc
    return marks


def _last_action(marks: Iterable[Mapping[str, Any]], candidate_id: str) -> str | None:
    action = None
    for mark in marks:
        if mark.get("candidate_evidence_id") == candidate_id:
            action = mark.get("action")
    return action


def _timestamp(value: Any, *, name: str) -> str:
    """ISO-8601 or refuse. A free-form string cannot be sorted, compared or audited."""
    if not isinstance(value, str) or not value.strip():
        raise _input_error(f"{name} must be an ISO-8601 timestamp")
    text = value.strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _input_error(f"{name} {text!r} is not ISO-8601: {exc}") from exc
    return text


def append_mark(
    path: pathlib.Path | str,
    *,
    action: str,
    candidate_evidence_id: str,
    reviewer: str,
    review_decision_ref: str,
    reason: str,
    at: str,
    previous_state: str | None = None,
    previous_decision_ref: str | None = None,
) -> Mark | None:
    """Append one mark, or return ``None`` when it would repeat the current action.

    Repeating a revoke is not an error (two reviewers may both act), but writing a second
    identical line would make the log look like two decisions. The check happens under the
    same lock as the append, so two concurrent reducers cannot both decide it is new.
    """
    at = _timestamp(at, name="at")
    if action not in ACTIONS:
        raise _input_error(f"action must be one of {list(ACTIONS)}, got {action!r}")
    for name, value in (
        ("candidate_evidence_id", candidate_evidence_id),
        ("reviewer", reviewer),
        ("review_decision_ref", review_decision_ref),
        ("reason", reason),
        ("at", at),
    ):
        if not isinstance(value, str) or not value.strip():
            raise _input_error(f"{name} must be a non-empty string")

    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            marks = [json.loads(line) for line in handle.read().splitlines() if line.strip()]
            latest = _last_action(marks, candidate_evidence_id)
            if latest == action:
                return None
            mark = Mark(
                seq=(marks[-1]["seq"] + 1) if marks else 1,
                action=action,
                candidate_evidence_id=candidate_evidence_id,
                reviewer=reviewer.strip(),
                review_decision_ref=review_decision_ref.strip(),
                reason=reason.strip(),
                at=at,
                # ``at`` is what the caller claims; this is when the mark actually
                # landed. Keeping both means a wrong clock at the call site cannot
                # rewrite the history of when the write happened.
                recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                previous_state=previous_state,
                previous_decision_ref=previous_decision_ref,
            )
            handle.seek(0, os.SEEK_END)
            handle.write(mark.to_line() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            return mark
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fold_marks(marks: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Current state per candidate: the last mark wins.

    This is the whole read model. Keeping it a fold — rather than storing a "current" row
    next to the log — is what prevents the log and the state from disagreeing.
    """
    state: dict[str, dict[str, Any]] = {}
    for mark in marks:
        candidate_id = str(mark.get("candidate_evidence_id"))
        if mark.get("action") == REVOKE:
            state[candidate_id] = {
                "state": "STALE",
                "review_decision_ref": mark.get("review_decision_ref"),
                "reviewer": mark.get("reviewer"),
                "at": mark.get("at"),
                "recorded_at": mark.get("recorded_at"),
                "previous_state": mark.get("previous_state"),
                "seq": mark.get("seq"),
            }
        elif mark.get("action") == RESTORE:
            state[candidate_id] = {
                "state": mark.get("previous_state") or "UNREVIEWED",
                "review_decision_ref": mark.get("previous_decision_ref"),
                "reviewer": mark.get("reviewer"),
                "at": mark.get("at"),
                "recorded_at": mark.get("recorded_at"),
                "previous_state": None,
                "seq": mark.get("seq"),
            }
    return state
