"""Adjacent-page continuation *candidates* (SDD 9.2, ``PI-F04-07``).

Contract sentence: "相邻页续表候选，比较表名/期间/单位/列定义；确认前不合并".

The module deliberately cannot merge. It returns a ``Continuation`` payload whose
``decision`` is one of ``JOIN_CANDIDATE / SEPARATE / NEEDS_REVIEW`` — plus
``CONFIRMED_JOIN`` only when the caller replays a real review decision. Two tables
are never rewritten into one "authoritative" table here; SDD 9.2 requires both
originals plus their evidence to survive, and the combined view is a later,
review-gated artefact.

Decision rules, in the order they are applied:

* different source version, non-adjacent pages, same page, or different table
  type → ``SEPARATE``. Adjacency is a *signal*, so a same-page pair is never
  chained by index.
* currency / unit / period: both known and different → ``SEPARATE``; caption differing
  only by a continuation marker (``续表``/``续前``/``（续）``) is the *same* label, and a
  real caption mismatch is ``NEEDS_REVIEW`` (a label is not the data);
  exactly one known → ``NEEDS_REVIEW`` (an unknown side is not a match).
* column count differs → ``SEPARATE``; column paths differ → ``NEEDS_REVIEW``
  (same width but a different header is exactly the "looks alike" trap);
  column width ratios drift beyond tolerance → ``NEEDS_REVIEW``.
* everything compatible → ``JOIN_CANDIDATE``. A headerless continuation page is
  also only a candidate, never a join.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

CONTINUATION_CODE = "E_TABLE_CONTINUATION_INVALID"

#: Relative drift allowed between two column-width vectors before it is reported.
WIDTH_TOLERANCE = 0.15

__all__ = [
    "CONTINUATION_CODE",
    "ComparisonReason",
    "ContinuationResult",
    "propose_continuation",
    "WIDTH_TOLERANCE",
]

_TEXTS = ("caption", "unit_raw", "currency", "period", "table_type")
_FLAGS = ("repeats_header", "ends_at_page_bottom", "starts_at_page_top")


def _error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError(CONTINUATION_CODE, message, detail=detail or None)


@dataclass(frozen=True)
class ComparisonReason:
    """One comparison outcome; ``blocking`` explains a non-join decision."""

    dimension: str
    code: str
    message: str
    blocking: bool = False
    #: Three levels, and the default is deliberately the loudest:
    #: ``HARD``  — the pair is not a continuation at all (data disagrees);
    #: ``SOFT``  — a human should look (a label differs; it names things, not data);
    #: ``INFO``  — non-blocking, recorded so the trail explains why a join was OK.
    #: Anything that does *not* block must say so, otherwise a log reader cannot
    #: tell "this reason blocked it" from "this reason was merely observed".
    severity: str = "HARD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ContinuationResult:
    """The contract payload plus the comparison trail that produced it."""

    continuation: dict[str, Any]
    reasons: list[ComparisonReason] = field(default_factory=list)

    @property
    def decision(self) -> str:
        return self.continuation["decision"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "continuation": dict(self.continuation),
            "reasons": [reason.to_dict() for reason in self.reasons],
        }


@dataclass(frozen=True)
class _Signature:
    table_id: str
    page_index: int | None
    caption: str | None
    unit_raw: str | None
    currency: str | None
    period: str | None
    table_type: str | None
    column_paths: tuple[tuple[str, ...], ...] | None
    column_count: int | None
    column_widths: tuple[float, ...] | None
    repeats_header: bool | None
    ends_at_page_bottom: bool | None
    starts_at_page_top: bool | None

    @property
    def headerless(self) -> bool:
        return self.column_paths is not None and len(self.column_paths) == 0


#: Word users mark the second half of a long table in a handful of ways. Only
#: these *affixes* are stripped: doing ``replace("续", "")`` would mangle a caption
#: whose *content* starts with 续 (续贷/续期/续保/续存 — rollover, renewal), which is
#: a business word, not a marker.
#: A bare ``续`` is deliberately *not* stripped: ``续贷余额`` is a caption about
#: rolled-over loans, not a continuation marker.
_CAPTION_PREFIXES = ("续表", "续前", "（续）", "(续)")
_CAPTION_SUFFIXES = ("（续）", "(续)")


def _normalise_caption(value: str) -> str:
    """Drop a continuation marker so ``续表1 X`` and ``表1 X`` compare equal.

    Chinese financial statements routinely label the next page ``续表1 …``. Comparing
    captions byte-for-byte then reports ``CAPTION_CONFLICT`` — the strongest evidence
    against a continuation — for the commonest shape of a real continuation.
    """
    text = "".join(value.split())
    for marker in _CAPTION_PREFIXES:
        if text.startswith(marker) and len(text) > len(marker):
            # ``续表`` keeps its 表: it renames the same table, it does not drop it.
            text = ("表" if marker == "续表" else "") + text[len(marker):]
            break
    for marker in _CAPTION_SUFFIXES:
        if text.endswith(marker) and len(text) > len(marker):
            text = text[: -len(marker)]
            break
    return text


def _text(value: Any, *, field_name: str, table_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _error(
            f"table {table_id}: {field_name} must be a string or null, got "
            f"{type(value).__name__}"
        )
    collapsed = " ".join(value.split())
    return collapsed or None


def _flag(value: Any, *, field_name: str, table_id: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise _error(
        f"table {table_id}: {field_name} must be a bool or null, got "
        f"{type(value).__name__}"
    )


def _signature(raw: Mapping[str, Any], *, role: str) -> _Signature:
    if not isinstance(raw, Mapping):
        raise _error(f"{role} table must be a mapping, got {type(raw).__name__}")
    table_id = raw.get("table_id")
    if not isinstance(table_id, str) or not table_id:
        raise _error(f"{role} table has no usable table_id: {table_id!r}")

    page_index = raw.get("page_index")
    if page_index is not None and (
        isinstance(page_index, bool) or not isinstance(page_index, int) or page_index < 0
    ):
        raise _error(
            f"table {table_id}: page_index must be a non-negative integer or null, got "
            f"{page_index!r}"
        )

    column_count = raw.get("column_count")
    if column_count is not None and (
        isinstance(column_count, bool) or not isinstance(column_count, int) or column_count <= 0
    ):
        raise _error(
            f"table {table_id}: column_count must be a positive integer or null, got "
            f"{column_count!r}"
        )

    column_paths = raw.get("column_paths")
    if column_paths is not None:
        if isinstance(column_paths, (str, bytes)) or not isinstance(column_paths, Sequence):
            raise _error(
                f"table {table_id}: column_paths must be a sequence of cell-id "
                f"sequences, got {type(column_paths).__name__}"
            )
        normalised: list[tuple[str, ...]] = []
        for index, path in enumerate(column_paths):
            if isinstance(path, (str, bytes)) or not isinstance(path, Sequence):
                raise _error(
                    f"table {table_id}: column_paths[{index}] must be a sequence of "
                    "cell ids, not a bare string"
                )
            for cell_id in path:
                if not isinstance(cell_id, str) or not cell_id.strip():
                    raise _error(
                        f"table {table_id}: column_paths[{index}] contains {cell_id!r}"
                    )
            normalised.append(tuple(path))
        column_paths = tuple(normalised)

    column_widths = raw.get("column_widths")
    if column_widths is not None:
        if isinstance(column_widths, (str, bytes)) or not isinstance(column_widths, Sequence):
            raise _error(
                f"table {table_id}: column_widths must be a sequence of numbers"
            )
        widths: list[float] = []
        for value in column_widths:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise _error(
                    f"table {table_id}: column_widths contains {value!r}; widths must be "
                    "positive numbers"
                )
            widths.append(float(value))
        column_widths = tuple(widths)

    return _Signature(
        table_id=table_id,
        page_index=page_index,
        caption=_text(raw.get("caption"), field_name="caption", table_id=table_id),
        unit_raw=_text(raw.get("unit_raw"), field_name="unit_raw", table_id=table_id),
        currency=_text(raw.get("currency"), field_name="currency", table_id=table_id),
        period=_text(raw.get("period"), field_name="period", table_id=table_id),
        table_type=_text(raw.get("table_type"), field_name="table_type", table_id=table_id),
        column_paths=column_paths,
        column_count=column_count,
        column_widths=column_widths,
        **{name: _flag(raw.get(name), field_name=name, table_id=table_id) for name in _FLAGS},
    )


def _widths_drift(left: Sequence[float], right: Sequence[float]) -> bool:
    """Compare *ratios*, not absolute widths: two pages may use different scales."""
    if len(left) != len(right) or not left:
        return True
    total_left, total_right = sum(left), sum(right)
    if total_left <= 0 or total_right <= 0:
        return True
    for a, b in zip(left, right):
        if abs(a / total_left - b / total_right) > WIDTH_TOLERANCE:
            return True
    return False


def propose_continuation(
    previous: Mapping[str, Any],
    next: Mapping[str, Any],
    *,
    evidence_ids: Sequence[str],
    review_decision_ref: str | None = None,
    same_source_version: bool = True,
) -> ContinuationResult:
    """Compare two tables and emit a ``Continuation`` candidate, never a merge.

    ``evidence_ids`` is mandatory: a relation with no evidence is exactly the kind
    of unverifiable claim SDD 9.2 forbids ("保留两张原表及证据").
    """
    prev = _signature(previous, role="previous")
    nxt = _signature(next, role="next")

    if isinstance(evidence_ids, (str, bytes)) or not isinstance(evidence_ids, Sequence):
        raise _error("evidence_ids must be a sequence of ids, not a bare string")
    evidence = list(evidence_ids)
    if not evidence:
        raise _error(
            "evidence_ids is empty; a continuation relation requires source evidence"
        )
    for evidence_id in evidence:
        if not isinstance(evidence_id, str) or not evidence_id:
            raise _error(f"evidence_ids contains {evidence_id!r}")
    if review_decision_ref is not None and (
        not isinstance(review_decision_ref, str) or not review_decision_ref
    ):
        raise _error(
            f"review_decision_ref must be a non-empty string or null, got "
            f"{review_decision_ref!r}"
        )
    if not isinstance(same_source_version, bool):
        raise _error("same_source_version must be a bool")

    reasons: list[ComparisonReason] = []
    compared: list[str] = []

    def compare(dimension: str) -> None:
        if dimension not in compared:
            compared.append(dimension)

    def block(dimension: str, code: str, message: str, *, severity: str = "HARD") -> None:
        reasons.append(
            ComparisonReason(
                dimension=dimension,
                code=code,
                message=message,
                blocking=True,
                severity=severity,
            )
        )

    # --- structural signals -------------------------------------------------
    compare("source_version")
    if not same_source_version:
        block("source_version", "SOURCE_VERSION_DIFFERS", "tables come from different source versions")

    compare("page_adjacency")
    if prev.page_index is not None and nxt.page_index is not None:
        if prev.page_index == nxt.page_index:
            block(
                "page_adjacency",
                "SAME_PAGE_NOT_CHAINED",
                "both tables sit on the same page; same-page tables are not chained by index",
            )
        elif nxt.page_index != prev.page_index + 1:
            block(
                "page_adjacency",
                "PAGES_NOT_ADJACENT",
                f"pages {prev.page_index}→{nxt.page_index} are not adjacent",
            )
    else:
        block(
            "page_adjacency",
            "PAGE_UNKNOWN",
            "page index is missing on at least one side; adjacency cannot be established",
        )

    compare("table_type")
    if prev.table_type and nxt.table_type and prev.table_type != nxt.table_type:
        block("table_type", "TYPE_MISMATCH", f"{prev.table_type!r} vs {nxt.table_type!r}")

    # --- declared-content comparisons --------------------------------------
    for dimension in ("caption", "currency", "unit_raw", "period"):
        compare(dimension)
        left = getattr(prev, dimension)
        right = getattr(nxt, dimension)
        if left and right:
            if left == right:
                continue
            if dimension == "caption" and _normalise_caption(left) == _normalise_caption(right):
                # Whitespace and a continuation marker are different statements, so
                # they get different codes: a log reader must be able to tell "same
                # label with a marker" from "same label, differently spaced".
                whitespace_only = "".join(left.split()) == "".join(right.split())
                reasons.append(
                    ComparisonReason(
                        dimension="caption",
                        code=(
                            "CAPTION_WHITESPACE_ONLY"
                            if whitespace_only
                            else "CAPTION_CONTINUATION_MARKER"
                        ),
                        message=(
                            f"{left!r} vs {right!r} differ only in whitespace; the labels "
                            "name the same table"
                            if whitespace_only
                            else f"{left!r} vs {right!r} differ only by a continuation "
                            "marker; the labels name the same table"
                        ),
                        severity="INFO",
                    )
                )
                continue
            # A caption is a label, not the data: a real mismatch means "a human
            # should look", not "these are certainly different tables". Currency,
            # unit and period are data, so they stay hard.
            block(
                dimension,
                f"{dimension.upper()}_CONFLICT",
                f"{left!r} vs {right!r}",
                severity="SOFT" if dimension == "caption" else "HARD",
            )
        elif left or right:
            block(
                dimension,
                f"{dimension.upper()}_UNKNOWN",
                "one side states it and the other does not; that is not a match",
            )

    compare("column_count")
    if prev.column_count and nxt.column_count and prev.column_count != nxt.column_count:
        block(
            "column_count",
            "COLUMN_COUNT_MISMATCH",
            f"{prev.column_count} vs {nxt.column_count}",
        )

    compare("column_paths")
    if prev.column_paths is not None and nxt.column_paths is not None:
        if prev.column_paths and nxt.column_paths and prev.column_paths != nxt.column_paths:
            block(
                "column_paths",
                "COLUMN_PATH_CHANGED",
                "the two pages describe different header paths; same width is not sameness",
            )
        elif not nxt.column_paths:
            reasons.append(
                ComparisonReason(
                    dimension="column_paths",
                    code="NEXT_PAGE_HEADERLESS",
                    message="the next page has no header; a continuation may only be proposed",
                    severity="INFO",
                )
            )

    compare("column_widths")
    if (
        prev.column_widths
        and nxt.column_widths
        and _widths_drift(prev.column_widths, nxt.column_widths)
    ):
        block(
            "column_widths",
            "COLUMN_WIDTH_DRIFT",
            "column width ratios differ beyond tolerance",
        )

    # --- page-position signals ---------------------------------------------
    compare("page_position")
    if prev.ends_at_page_bottom is False:
        block(
            "page_position",
            "PREVIOUS_TABLE_ENDS_MID_PAGE",
            "the previous table ends away from the page bottom; a continuation is implausible",
        )
    elif nxt.starts_at_page_top is False:
        block(
            "page_position",
            "NEXT_TABLE_STARTS_MID_PAGE",
            "the next table starts away from the page top; a continuation is implausible",
        )

    compare("repeated_header")
    if nxt.repeats_header:
        reasons.append(
            ComparisonReason(
                dimension="repeated_header",
                code="HEADER_REPEATED",
                message="the next page repeats the header, which supports continuation",
                severity="INFO",
            )
        )

    blocking = [reason for reason in reasons if reason.blocking]
    hard = [reason for reason in blocking if reason.severity == "HARD"]
    if any(reason.code.endswith("_UNKNOWN") for reason in blocking):
        decision = "NEEDS_REVIEW"
    elif any(reason.code in {"COLUMN_PATH_CHANGED", "COLUMN_WIDTH_DRIFT"} for reason in blocking):
        decision = "NEEDS_REVIEW"
    elif hard:
        # A soft reason must never soften a hard one: if the data disagrees, the pair
        # is not a continuation no matter how similar the captions look.
        decision = "SEPARATE"
    elif blocking:
        decision = "NEEDS_REVIEW"
    else:
        decision = "JOIN_CANDIDATE"

    ref: str | None = None
    if decision == "JOIN_CANDIDATE" and review_decision_ref:
        decision = "CONFIRMED_JOIN"
        ref = review_decision_ref

    continuation = {
        "relation_id": f"CONT-{prev.table_id}--{nxt.table_id}",
        "previous_table_id": prev.table_id,
        "next_table_id": nxt.table_id,
        "decision": decision,
        "compared_dimensions": compared,
        "evidence_ids": evidence,
        "review_decision_ref": ref,
    }
    return ContinuationResult(continuation=continuation, reasons=reasons)
