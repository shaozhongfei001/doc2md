"""Review decisions bound to a patch, a generation, an access revision and a scope.

SDD 12.1:

    "提交reviewer从本地OS主体或银行认证上下文取得，不能信输入JSON自报'审批人'。"
    "本地单用户review记录仅证明LOCAL_OPERATOR复核，不冒充银行双人审批；
     银行审批适配未接入时该分支不能达到BANK_APPROVED。"
    "review绑定patch digest、base generation、source access revision和scope；任一变化旧决定失效。"

So a decision here is a *tuple of four bindings* plus a reviewer that the caller
supplies from its auth context. :func:`is_decision_valid` re-checks all four on
every use; there is no "approved forever" flag to leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import ExtensionError
from .authority import ReviewerPolicy

OUTCOMES = ("LOCAL_OPERATOR", "BANK_APPROVED", "REJECTED")

__all__ = ["OUTCOMES", "is_decision_valid", "record_decision"]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class _Binding:
    patch_digest: str
    base_generation_id: str
    access_revision: str
    scope: str


def _binding(patch_digest: Any, base_generation_id: Any, access_revision: Any, scope: Any) -> _Binding:
    for name, value in (
        ("patch_digest", patch_digest),
        ("base_generation_id", base_generation_id),
        ("access_revision", access_revision),
        ("scope", scope),
    ):
        if not isinstance(value, str) or not value.strip():
            raise _input_error(f"{name} must be a non-empty string, got {value!r}")
    return _Binding(patch_digest, base_generation_id, access_revision, scope)


def record_decision(
    *,
    decision_id: str,
    patch_digest: str,
    base_generation_id: str,
    access_revision: str,
    scope: str,
    reviewer: str,
    outcome: str,
    bank_auth_connected: bool = False,
    policy: ReviewerPolicy | None = None,
) -> dict[str, Any]:
    """Record one review decision.

    ``BANK_APPROVED`` requires a connected bank approval adapter. Without one the
    call fails with ``E_CONSUMER_NOT_CONNECTED`` — a local operator cannot approve
    on the bank's behalf, and the system must not pretend otherwise.
    """
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise _input_error(f"decision_id must be a non-empty string, got {decision_id!r}")
    reviewer = (policy or ReviewerPolicy()).check(reviewer)
    if outcome not in OUTCOMES:
        raise _input_error(f"outcome must be one of {list(OUTCOMES)}, got {outcome!r}")
    if not isinstance(bank_auth_connected, bool):
        raise _input_error("bank_auth_connected must be a bool")
    if outcome == "BANK_APPROVED" and not bank_auth_connected:
        raise ExtensionError(
            "E_CONSUMER_NOT_CONNECTED",
            "no bank approval adapter is connected; this branch cannot reach BANK_APPROVED "
            "and a local operator decision is not a bank approval",
            detail={"reviewer": reviewer.strip()},
        )

    binding = _binding(patch_digest, base_generation_id, access_revision, scope)
    return {
        "decision_id": decision_id,
        "patch_digest": binding.patch_digest,
        "base_generation_id": binding.base_generation_id,
        "access_revision": binding.access_revision,
        "scope": binding.scope,
        "reviewer": reviewer,
        "outcome": outcome,
    }


def is_decision_valid(
    decision: Any,
    *,
    patch_digest: str,
    base_generation_id: str,
    access_revision: str,
    scope: str,
) -> bool:
    """True only when all four bindings still match.

    A mismatch is not an error — it simply means the decision no longer applies, and
    the caller must obtain a new one.
    """
    if not isinstance(decision, dict):
        return False
    expected = _binding(patch_digest, base_generation_id, access_revision, scope)
    return (
        decision.get("patch_digest") == expected.patch_digest
        and decision.get("base_generation_id") == expected.base_generation_id
        and decision.get("access_revision") == expected.access_revision
        and decision.get("scope") == expected.scope
        and decision.get("outcome") in ("LOCAL_OPERATOR", "BANK_APPROVED")
    )
