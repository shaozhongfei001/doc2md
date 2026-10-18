"""RFC6902 ``test``/``replace`` patches with a field whitelist and CAS (SDD 12.1).

SDD 12.1 is unambiguous about the division of labour:

    "采用成熟python-json-patch/RFC6902执行test/replace，不另造通用补丁引擎。
     项目只负责字段白名单、版本CAS、身份/审批、证据约束和依赖重算。"

So the patch is executed by ``jsonpatch``; everything this module adds is the part
the library cannot know:

* **only** ``test`` and ``replace`` — no ``add``/``remove``/``move``/``copy``, and
  every ``replace`` must be immediately preceded by a ``test`` on the same path,
  which is what makes a concurrent edit fail loudly instead of overwriting;
* a **field whitelist** with an explicit forbidden list (source hashes, schema
  version, evidence ids/locators, original files) — those are not patchable at all;
* **CAS** on the generation: the caller states what it believes is current, and a
  mismatch returns a version conflict instead of applying onto a newer document;
* **read-only preview**: the preview never touches the input document.

Structure changes (splitting/merging cells) are deliberately impossible here: SDD
says they need a dedicated structure patch and a grid rebuild, and this version
uses re-parsing or a human-rebuilt table instead of widening the whitelist.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import jsonpatch

from .. import ExtensionError
from .authority import ReviewerPolicy

#: Paths a reviewer may change. Everything else is refused, including valid JSON.
PATCHABLE_PATHS = (
    r"^/tables/[^/]+/cells/[^/]+/text$",
    r"^/tables/[^/]+/cell_semantics/[^/]+/(column_header_cell_ids|row_header_cell_ids|unit_raw|state)$",
    r"^/tables/[^/]+/notes/[^/]+/(target_ids|scope|text)$",
    r"^/continuations/[^/]+/decision$",
    r"^/visuals/[^/]+/flow/edges/[^/]+/(source_node_id|target_node_id|label|direction_state)$",
    r"^/visuals/[^/]+/chart/points/[^/]+/(exact_value|value_type|unit_raw)$",
)

#: Never patchable, whatever whitelist entry happens to match.
FORBIDDEN_FRAGMENTS = (
    "source_sha256",
    "content_sha256",
    "schema_version",
    "generation_id",
    "/evidence",
    "locator",
    "raw_engine_ref",
    "normalizer_version",
    "profile_sha256",
)

ALLOWED_OPS = ("test", "replace")

__all__ = [
    "ALLOWED_OPS",
    "FORBIDDEN_FRAGMENTS",
    "PATCHABLE_PATHS",
    "PatchPreview",
    "apply_patch",
    "patch_digest",
    "preview_patch",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _version_conflict(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_VERSION_CONFLICT", message, detail=detail or None)


@dataclass(frozen=True)
class PatchPreview:
    """What the patch *would* change, computed on a copy."""

    document: dict[str, Any]
    changed_paths: list[str]
    digest: str
    accepted: bool
    reason: str | None = None
    audit: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_paths": list(self.changed_paths),
            "digest": self.digest,
            "accepted": self.accepted,
            "reason": self.reason,
            "audit": dict(self.audit),
        }


def patch_digest(patch: Sequence[Mapping[str, Any]]) -> str:
    """Stable digest of the patch itself, used to bind a review decision to it."""
    canonical = json.dumps(list(patch), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_patch(patch: Any) -> list[dict[str, Any]]:
    if isinstance(patch, (str, bytes)) or not isinstance(patch, Sequence) or not patch:
        raise _input_error("patch must be a non-empty sequence of operations")
    ops: list[dict[str, Any]] = []
    for index, raw in enumerate(patch):
        if not isinstance(raw, Mapping):
            raise _input_error(f"patch[{index}] must be a mapping")
        op = raw.get("op")
        if op not in ALLOWED_OPS:
            raise _input_error(
                f"patch[{index}]: op {op!r} is not allowed; only {list(ALLOWED_OPS)} may run. "
                "add/remove/move/copy would let a patch restructure the document",
                index=index,
            )
        path = raw.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise _input_error(f"patch[{index}]: path must be a JSON Pointer, got {path!r}")
        lowered = path.lower()
        for fragment in FORBIDDEN_FRAGMENTS:
            if fragment in lowered:
                raise _input_error(
                    f"patch[{index}]: path {path!r} touches {fragment!r}, which is never "
                    "patchable (source identity, provenance or schema)",
                    index=index,
                    path=path,
                )
        if not any(re.match(pattern, path) for pattern in PATCHABLE_PATHS):
            raise _input_error(
                f"patch[{index}]: path {path!r} is outside the reviewer field whitelist",
                index=index,
                path=path,
            )
        if "value" not in raw:
            raise _input_error(f"patch[{index}]: '{op}' requires a value")
        ops.append(dict(raw))

    for index, raw in enumerate(ops):
        if raw["op"] != "replace":
            continue
        previous = ops[index - 1] if index else None
        if previous is None or previous.get("op") != "test" or previous.get("path") != raw["path"]:
            raise _input_error(
                f"patch[{index}]: replace must be immediately preceded by a test on the same "
                "path; without it a concurrent edit is silently overwritten",
                index=index,
                path=raw["path"],
            )
    return ops


def _check_context(
    *,
    base_generation_id: Any,
    expected_current_generation_id: Any,
    current_generation_id: Any,
    reviewer: Any,
    policy: ReviewerPolicy | None = None,
) -> str:
    for name, value in (
        ("base_generation_id", base_generation_id),
        ("expected_current_generation_id", expected_current_generation_id),
        ("current_generation_id", current_generation_id),
    ):
        if not isinstance(value, str) or not value:
            raise _input_error(f"{name} must be a non-empty string, got {value!r}")
    if expected_current_generation_id != current_generation_id:
        raise _version_conflict(
            "the document moved since the reviewer opened it; the patch is refused rather "
            "than applied onto a newer generation",
            expected=expected_current_generation_id,
            current=current_generation_id,
        )
    return (policy or ReviewerPolicy()).check(reviewer)


def _apply(
    document: Mapping[str, Any],
    ops: Sequence[Mapping[str, Any]],
    *,
    validators: Sequence[Callable[[dict[str, Any]], None]] = (),
) -> tuple[dict[str, Any], list[str]]:
    working = copy.deepcopy(dict(document))
    try:
        result = jsonpatch.JsonPatch(list(ops)).apply(working, in_place=False)
    except jsonpatch.JsonPatchException as exc:
        raise _input_error(
            f"patch could not be applied: {exc}; a failing test means the document changed "
            "under the reviewer",
            digest=patch_digest(ops),
        ) from exc
    changed = [op["path"] for op in ops if op["op"] == "replace"]
    for validator in validators:
        validator(result)  # a failed re-validation must abort the whole patch
    return result, changed


def preview_patch(
    document: Mapping[str, Any],
    patch: Sequence[Mapping[str, Any]],
    *,
    base_generation_id: str,
    expected_current_generation_id: str,
    current_generation_id: str,
    reviewer: str,
    reason: str,
    evidence_ids: Sequence[str] = (),
    validators: Sequence[Callable[[dict[str, Any]], None]] = (),
    policy: ReviewerPolicy | None = None,
) -> PatchPreview:
    """Apply on a copy and report; the input document is untouched."""
    if not isinstance(document, Mapping):
        raise _input_error(f"document must be a mapping, got {type(document).__name__}")
    if not isinstance(reason, str) or not reason.strip():
        raise _input_error("a review patch must record a reason")
    ops = _validate_patch(patch)
    reviewer = _check_context(
        base_generation_id=base_generation_id,
        expected_current_generation_id=expected_current_generation_id,
        current_generation_id=current_generation_id,
        reviewer=reviewer,
        policy=policy,
    )
    result, changed = _apply(document, ops, validators=validators)
    return PatchPreview(
        document=result,
        changed_paths=changed,
        digest=patch_digest(ops),
        accepted=True,
        audit={
            "reviewer": reviewer,
            "base_generation_id": base_generation_id,
            "target_generation_id": current_generation_id,
            "reason": reason.strip(),
            "evidence_ids": list(evidence_ids),
            "ops": len(ops),
        },
    )


def apply_patch(
    document: Mapping[str, Any],
    patch: Sequence[Mapping[str, Any]],
    *,
    base_generation_id: str,
    expected_current_generation_id: str,
    current_generation_id: str,
    reviewer: str,
    reason: str,
    evidence_ids: Sequence[str] = (),
    validators: Sequence[Callable[[dict[str, Any]], None]] = (),
    policy: ReviewerPolicy | None = None,
) -> dict[str, Any]:
    """Return a **new** document; the input is never mutated.

    In this layer "apply" still means "produce the candidate next generation".
    Persisting it (CAS switch) belongs to the release step, which re-checks that the
    current generation and the authorization are unchanged.
    """
    return preview_patch(
        document,
        patch,
        base_generation_id=base_generation_id,
        expected_current_generation_id=expected_current_generation_id,
        current_generation_id=current_generation_id,
        reviewer=reviewer,
        reason=reason,
        evidence_ids=evidence_ids,
        validators=validators,
        policy=policy,
    ).document
