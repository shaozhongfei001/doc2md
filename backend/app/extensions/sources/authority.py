"""Source authority, licence and validity rules (T203).

The single most important rule here: **an external reference can never become a
bank-effective rule**, and the classification is decided by declared evidence, not by
a file name or a model's opinion.

Checkpoints
-----------
1. ``EXTERNAL_POLICY`` / ``PEER_REFERENCE`` need a licence reference; without it the
   version may exist as an unprocessed registration but must not be used.
2. ``authority_class == BANK_EFFECTIVE_RULE`` requires ``source_kind == BANK_RULE``
   **and** a bank authority decision reference. A peer document with that class is a
   contradiction, not a preference.
3. Validity windows are enforced against a caller-supplied clock so the behaviour is
   testable and does not depend on the machine time.
4. Two sources may describe the same rule name; they stay side by side with distinct
   ``source_kind``/``authority_class``. Nothing in this module merges them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .. import ExtensionError

__all__ = [
    "AuthorityDecision",
    "assert_authority",
    "compare_in_house_and_external",
    "EXTERNAL_SOURCE_KINDS",
]

EXTERNAL_SOURCE_KINDS = ("EXTERNAL_POLICY", "PEER_REFERENCE")


@dataclass(frozen=True)
class AuthorityDecision:
    usable: bool
    reason: str
    requires_license: bool
    blocked_field: str | None = None

    def assert_usable(self) -> None:
        if not self.usable:
            raise ExtensionError(
                "E_SCOPE_DENIED",
                self.reason,
                detail={"field": self.blocked_field},
            )


def _parse(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ExtensionError(
                "E_INPUT_INVALID", f"validity timestamp is not ISO-8601: {value!r}"
            ) from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ExtensionError("E_INPUT_INVALID", f"unsupported timestamp type: {type(value)!r}")


def assert_authority(
    version: Mapping[str, Any], *, now: datetime | None = None
) -> AuthorityDecision:
    """Decide whether a registered version may be *used* (not merely stored)."""
    now = now or datetime.now(timezone.utc)
    source_kind = version.get("source_kind")
    authority_class = version.get("authority_class")
    processing_route = version.get("processing_route")

    if source_kind in EXTERNAL_SOURCE_KINDS:
        if not version.get("license_ref"):
            return AuthorityDecision(
                usable=False,
                reason=(
                    "external/peer material has no licence reference; it may be registered "
                    "as metadata but must not be processed or published"
                ),
                requires_license=True,
                blocked_field="license_ref",
            )
        if authority_class == "BANK_EFFECTIVE_RULE":
            return AuthorityDecision(
                usable=False,
                reason=(
                    "an external/peer document cannot be a bank-effective rule; the bank "
                    "version must be supplied as a BANK_RULE with its own authority reference"
                ),
                requires_license=True,
                blocked_field="authority_class",
            )

    if authority_class == "BANK_EFFECTIVE_RULE":
        if source_kind != "BANK_RULE":
            return AuthorityDecision(
                usable=False,
                reason="BANK_EFFECTIVE_RULE requires source_kind == BANK_RULE",
                requires_license=False,
                blocked_field="source_kind",
            )
        if not version.get("authority_decision_ref"):
            return AuthorityDecision(
                usable=False,
                reason=(
                    "BANK_EFFECTIVE_RULE requires a bank authority decision reference; a "
                    "file name or an LLM judgement is not evidence"
                ),
                requires_license=False,
                blocked_field="authority_decision_ref",
            )

    valid_from = _parse(version.get("valid_from"))
    valid_to = _parse(version.get("valid_to"))
    if valid_to is not None and now >= valid_to:
        return AuthorityDecision(
            usable=False,
            reason="the version is past its validity window",
            requires_license=source_kind in EXTERNAL_SOURCE_KINDS,
            blocked_field="valid_to",
        )
    if valid_from is not None and now < valid_from:
        return AuthorityDecision(
            usable=False,
            reason="the version is not yet effective",
            requires_license=source_kind in EXTERNAL_SOURCE_KINDS,
            blocked_field="valid_from",
        )

    if processing_route == "REUSE_QUERY" and not version.get("legacy_query_ref"):
        return AuthorityDecision(
            usable=False,
            reason="REUSE_QUERY needs an authorised query reference",
            requires_license=source_kind in EXTERNAL_SOURCE_KINDS,
            blocked_field="legacy_query_ref",
        )

    if not version.get("purpose_scopes"):
        return AuthorityDecision(
            usable=False,
            reason="a purpose scope is required before any use",
            requires_license=source_kind in EXTERNAL_SOURCE_KINDS,
            blocked_field="purpose_scopes",
        )

    return AuthorityDecision(
        usable=True,
        reason="authority checks passed",
        requires_license=source_kind in EXTERNAL_SOURCE_KINDS,
    )


def compare_in_house_and_external(
    in_house: Mapping[str, Any], external: Mapping[str, Any]
) -> dict[str, Any]:
    """Report a clash without merging the two sources.

    Returns a structure the caller can log and show to a reviewer. It never returns a
    "winner": the bank rule wins by *authority class*, which is a property of the
    record, not a decision this function is allowed to make silently.
    """
    bank = assert_authority(in_house)
    other = assert_authority(external)
    same_name = (
        str(in_house.get("external_object_id")) == str(external.get("external_object_id"))
        or str(in_house.get("legacy_query_ref") or "") == str(external.get("legacy_query_ref") or "")
    )
    return {
        "same_object_reference": same_name,
        "in_house_usable": bank.usable,
        "external_usable": other.usable,
        "in_house_authority": in_house.get("authority_class"),
        "external_authority": external.get("authority_class"),
        "external_may_override_in_house": False,
        "external_block_reason": None if other.usable else other.reason,
        "status": "COEXIST" if same_name else "DISTINCT_OBJECTS",
    }
