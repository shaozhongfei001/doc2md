"""Who is allowed to sign a review decision (``L31-FIND-02``).

The distinction this module exists to keep is **mechanism vs policy**:

* the *mechanism* is a whitelist / pattern check;
* the *policy* is whether it is enforced — an Owner decision, recorded in
  ``evidence/OWNER_DECISIONS_20261018.md`` (D1).

Today the policy is ``ALLOW_ALL``: the Owner asked for "全部放行". That is a deliberate
choice, so ``"---"`` is still accepted and the audit trail still cannot prove who
signed. What this module buys is that narrowing it later is a **policy change, not a
code change**, and that the narrowing branch is already tested — a rule that has never
been executed is not a rule.

Do not read the default as an endorsement: while ``ALLOW_ALL`` is in force,
"reviewer identity is unverified" must stay in the delivery notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .. import ExtensionError

__all__ = [
    "ALLOW_ALL",
    "RESTRICTED",
    "MODES",
    "DEFAULT_REVIEWER_PATTERN",
    "ReviewerPolicy",
]

#: Shipped legal form: starts with a letter/digit/underscore/CJK character, 2-64
#: characters, then letters/digits/underscore/CJK/``.``/``@``/``-``.
#:
#: A placeholder such as ``---`` fails because it does not *start* with an
#: identifier character, so no blacklist is needed. Chinese names pass, which
#: matters here: the reviewers are people, not only system accounts.
DEFAULT_REVIEWER_PATTERN = r"[0-9A-Za-z_\u4e00-\u9fff][0-9A-Za-z_\u4e00-\u9fff.@-]{1,63}"

#: Every reviewer is accepted. Recorded, not enforced.
ALLOW_ALL = "ALLOW_ALL"
#: Only reviewers matching ``allowed`` or ``pattern`` are accepted.
RESTRICTED = "RESTRICTED"
MODES = (ALLOW_ALL, RESTRICTED)


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class ReviewerPolicy:
    """A reviewer check plus the decision to actually apply it.

    ``allowed`` is an exact-match set; ``pattern`` is a full-match regex. Both may be
    given: a reviewer passes if either accepts it. ``RESTRICTED`` with neither rule is
    allowed and rejects everyone — a misconfiguration that fails closed, which is the
    only kind of misconfiguration worth having here.
    """

    mode: str = RESTRICTED
    allowed: tuple[str, ...] = ()
    pattern: str | None = DEFAULT_REVIEWER_PATTERN

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            # Silently treating an unknown mode as ALLOW_ALL would turn a typo into a
            # security hole that reports success.
            raise _input_error(
                f"unknown reviewer policy mode {self.mode!r}; expected one of {list(MODES)}"
            )
        if not isinstance(self.allowed, tuple) or any(
            not isinstance(item, str) or not item for item in self.allowed
        ):
            raise _input_error("allowed must be a tuple of non-empty strings")
        if self.pattern is not None:
            if not isinstance(self.pattern, str) or not self.pattern:
                raise _input_error("pattern must be a non-empty string or None")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise _input_error(f"pattern {self.pattern!r} is not a valid regex: {exc}")

    def check(self, reviewer: Any, *, where: str = "reviewer") -> str:
        """Return the normalised reviewer, or raise.

        Whitespace is stripped on the way in **and** out: ``" RV-1 "`` is a real
        decision with accidental spaces, and storing it verbatim would make exact
        matching against a ledger miss.
        """
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise _input_error(
                f"{where} must come from the local OS subject / bank auth context, not from "
                "the payload being reviewed"
            )
        value = reviewer.strip()
        if self.mode == ALLOW_ALL:
            return value
        if value in self.allowed:
            return value
        if self.pattern is not None and re.fullmatch(self.pattern, value):
            return value
        raise _input_error(
            f"{where} {value!r} is not on the allowed list; the policy is RESTRICTED, so an "
            "unrecognised identity is a refusal rather than an anonymous signature",
            where=where,
            mode=self.mode,
        )
