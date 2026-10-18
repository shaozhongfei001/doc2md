"""Interrupted-commit recovery (SDD 13.1 / 14, ``E_COMMIT_INCOMPLETE``).

    "E_COMMIT_INCOMPLETE — 原子提交失败 | 只恢复本根staging；reader仍读原完整版本"

and

    "reader先验证manifest/state再开文件，遇到sha不符不降级为旧缓存。"

So recovery has exactly two jobs, and it must not do a third:

1. decide what may be deleted — **only** files under this root's staging area;
2. verify a committed version before it is published — a digest mismatch is an
   error, never a reason to fall back to an older cached copy.

Anything that would "fix" a bad commit by rewriting it in place, or that would
delete a committed file to make a recovery look clean, is out of scope by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

__all__ = ["RecoveryPlan", "plan_recovery", "verify_committed"]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class RecoveryPlan:
    removable: list[str]
    retained: list[str]
    reason: str
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "removable": list(self.removable),
            "retained": list(self.retained),
            "reason": self.reason,
            "issues": [dict(issue) for issue in self.issues],
        }


def plan_recovery(
    *,
    staging_entries: Sequence[str],
    committed_entries: Sequence[str],
    root_prefix: str,
) -> RecoveryPlan:
    """Split leftovers into "safe to delete" and "must keep".

    ``root_prefix`` is the extension root; anything outside it is refused rather
    than recovered, because a recovery step is a write and must stay inside the
    root it owns.
    """
    if not isinstance(root_prefix, str) or not root_prefix:
        raise _input_error(f"root_prefix must be a non-empty string, got {root_prefix!r}")
    for name, entries in (("staging_entries", staging_entries), ("committed_entries", committed_entries)):
        if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
            raise _input_error(f"{name} must be a sequence of relative paths")

    issues: list[dict[str, Any]] = []
    removable: list[str] = []
    retained: list[str] = []

    for entry in staging_entries:
        path = str(entry)
        if path.startswith("/") or ".." in path.split("/"):
            issues.append(
                {
                    "code": "STAGING_OUTSIDE_ROOT",
                    "path": path,
                    "message": "staging entry leaves the extension root; not touched",
                }
            )
            continue
        removable.append(path)

    for entry in committed_entries:
        path = str(entry)
        if path not in retained:
            retained.append(path)

    return RecoveryPlan(
        removable=sorted(set(removable)),
        retained=sorted(set(retained)),
        reason=(
            "only this root's staging leftovers are removable; committed artifacts are "
            "retained and a reader keeps seeing the previously complete version"
        ),
        issues=issues,
    )


def verify_committed(
    manifest: Mapping[str, Any], *, actual_sha256: str
) -> str:
    """Check the committed digest before publishing; never fall back to a cache."""
    if not isinstance(manifest, Mapping):
        raise _input_error(f"manifest must be a mapping, got {type(manifest).__name__}")
    expected = manifest.get("output_sha256") or manifest.get("output_hash")
    if not isinstance(expected, str) or len(expected) != 64:
        raise _input_error(
            "manifest carries no 64-char output digest; a version without a digest cannot "
            "be published, and the absence is not a licence to use an older cache"
        )
    if not isinstance(actual_sha256, str) or len(actual_sha256) != 64:
        raise _input_error(f"actual_sha256 must be a 64-char digest, got {actual_sha256!r}")
    if expected.lower() != actual_sha256.lower():
        raise ExtensionError(
            "E_EVIDENCE_MISMATCH",
            "committed artifact digest does not match its manifest; the reader must fail "
            "closed instead of serving a stale cache",
            detail={"expected_sha256": expected.lower(), "actual_sha256": actual_sha256.lower()},
        )
    return actual_sha256.lower()
