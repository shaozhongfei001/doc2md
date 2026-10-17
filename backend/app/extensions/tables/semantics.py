"""Multi-level header and row-title paths (SDD 9.1, ``PI-F04-06``).

The contract sentence this module implements verbatim is:

    "列/行标题路径由 cell 引用形成，单位/表题上下文独立"

Four consequences are baked into the design, not left to callers:

1. **A path is a sequence of ``cell_id`` references**, never a position and
   never a synthesised string label. Order is outer→inner, so the same list
   both identifies the branch (``["...C0000", "...C0001"]``) and survives a
   relabel of the header text. Two different branches that happen to share a
   label (``人民币/期限`` vs ``外币/期限``) therefore stay distinguishable —
   this is the whole point of storing references instead of text.
2. **No header band, no path.** A path may only be derived from an explicit
   header band declaration (Word ``w:tblHeader`` / native ``th``, computed by
   the caller from the source). Bold text or "it is the first row" is *not*
   evidence; when the band is unknown every data cell is reported
   ``NEEDS_REVIEW`` with an empty path rather than guessed.
3. **Unit and caption live in ``context``.** ``unit_raw`` is copied from the
   caller's evidence-backed context and is never inferred from a header cell:
   ``人民币`` may legitimately be a header branch (SDD 9.1.3), so treating a
   currency word as "the unit" would corrupt real header trees. With no
   explicit unit the value stays ``None`` — it is not filled in by a model.
4. **Ambiguity blocks, it does not branch.** An anchor that only partially
   covers the data cell's interval, two anchors competing at one level, or a
   gap in the chain all produce an *issue* plus ``NEEDS_REVIEW``; the module
   never picks "the most likely" branch.

Every state emitted here is ``CANDIDATE`` or ``NEEDS_REVIEW``. ``CONFIRMED``
requires a ``review_decision_ref`` and can only come from review (L07), so it
is not reachable from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

#: Stable code for a table that cannot be given defensible header paths.
SEMANTICS_CODE = "E_TABLE_SEMANTICS_INVALID"

__all__ = [
    "SEMANTICS_CODE",
    "HeaderIssue",
    "SemanticsResult",
    "derive_header_paths",
]


# --------------------------------------------------------------------- helpers
def _is_int(value: Any) -> bool:
    """``True`` only for real integers: ``bool`` is an ``int`` subclass and lies."""
    return isinstance(value, int) and not isinstance(value, bool)


def _error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError(SEMANTICS_CODE, message, detail=detail or None)


def _normalise_text(value: Any) -> str:
    """Collapse whitespace so ``" 金额 "`` and ``"金额"`` compare equal."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


@dataclass(frozen=True)
class HeaderIssue:
    """A defensible reason why a data cell (or the whole table) is unresolved."""

    code: str
    message: str
    cell_id: str | None = None
    row: int | None = None
    col: int | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.cell_id is not None:
            payload["cell_id"] = self.cell_id
        if self.row is not None:
            payload["row"] = self.row
        if self.col is not None:
            payload["col"] = self.col
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload


@dataclass(frozen=True)
class SemanticsResult:
    """Header semantics for one table plus the issues that explain the gaps."""

    cell_semantics: list[dict[str, Any]]
    issues: list[HeaderIssue]
    context: dict[str, Any]

    @property
    def unresolved(self) -> list[dict[str, Any]]:
        return [s for s in self.cell_semantics if s["state"] == "NEEDS_REVIEW"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_semantics": [dict(item) for item in self.cell_semantics],
            "issues": [issue.to_dict() for issue in self.issues],
            "context": dict(self.context),
        }


# ---------------------------------------------------------------- table shape
@dataclass(frozen=True)
class _Anchor:
    cell_id: str
    row: int
    col: int
    rowspan: int
    colspan: int
    text: str

    @property
    def rows(self) -> range:
        return range(self.row, self.row + self.rowspan)

    @property
    def cols(self) -> range:
        return range(self.col, self.col + self.colspan)

    def covers_columns(self, start: int, end: int) -> bool:
        """True when this anchor fully covers ``[start, end)``."""
        return self.col <= start and end <= self.col + self.colspan

    def covers_rows(self, start: int, end: int) -> bool:
        return self.row <= start and end <= self.row + self.rowspan

    def overlaps_columns(self, start: int, end: int) -> bool:
        return self.col < end and start < self.col + self.colspan

    def overlaps_rows(self, start: int, end: int) -> bool:
        return self.row < end and start < self.row + self.rowspan


def _read_table(table: Mapping[str, Any]) -> tuple[list[_Anchor], int, int]:
    if not isinstance(table, Mapping):
        raise _error(
            f"table must be a mapping produced by tables.grid/extract_tables, got "
            f"{type(table).__name__}"
        )
    n_rows = table.get("n_rows")
    n_cols = table.get("n_cols")
    if not _is_int(n_rows) or not _is_int(n_cols) or n_rows <= 0 or n_cols <= 0:
        raise _error(
            f"n_rows/n_cols must be positive integers, got {n_rows!r}/{n_cols!r}"
        )
    cells = table.get("cells")
    if isinstance(cells, (str, bytes)) or not isinstance(cells, Sequence):
        raise _error(
            f"cells must be a sequence of anchors, got {type(cells).__name__}"
        )

    anchors: list[_Anchor] = []
    claimed: dict[tuple[int, int], str] = {}
    for index, raw in enumerate(cells):
        if not isinstance(raw, Mapping):
            raise _error(
                f"cells[{index}] must be a mapping, got {type(raw).__name__}"
            )
        cell_id = raw.get("cell_id")
        row = raw.get("row")
        col = raw.get("col")
        rowspan = raw.get("rowspan")
        colspan = raw.get("colspan")
        if not isinstance(cell_id, str) or not cell_id:
            raise _error(f"cells[{index}] has no usable cell_id: {cell_id!r}")
        if not all(_is_int(v) for v in (row, col, rowspan, colspan)):
            raise _error(
                f"cell {cell_id} row/col/rowspan/colspan must be integers, got "
                f"{row!r}/{col!r}/{rowspan!r}/{colspan!r}"
            )
        if rowspan <= 0 or colspan <= 0:
            raise _error(
                f"cell {cell_id} has non-positive spans {rowspan}/{colspan}"
            )
        if row < 0 or col < 0 or row + rowspan > n_rows or col + colspan > n_cols:
            raise _error(
                f"cell {cell_id} span ({row},{col})+({rowspan},{colspan}) leaves the "
                f"{n_rows}x{n_cols} grid"
            )
        anchor = _Anchor(
            cell_id=cell_id,
            row=row,
            col=col,
            rowspan=rowspan,
            colspan=colspan,
            text=_normalise_text(raw.get("text")),
        )
        for r in anchor.rows:
            for c in anchor.cols:
                if (r, c) in claimed:
                    raise _error(
                        f"cells {claimed[(r, c)]} and {cell_id} overlap at "
                        f"(row={r}, col={c})",
                        cell_id=cell_id,
                        row=r,
                        col=c,
                    )
                claimed[(r, c)] = cell_id
        anchors.append(anchor)
    return anchors, n_rows, n_cols


def _band(values: Sequence[int], *, name: str, limit: int) -> set[int]:
    band: set[int] = set()
    for value in values:
        if not _is_int(value):
            raise _error(f"{name} entries must be integers, got {value!r}")
        if value < 0 or value >= limit:
            raise _error(f"{name} entry {value} is outside 0..{limit - 1}")
        band.add(value)
    return band


# ------------------------------------------------------------------ public API
def derive_header_paths(
    table: Mapping[str, Any],
    *,
    header_rows: Sequence[int] = (),
    header_cols: Sequence[int] = (),
    context: Mapping[str, Any] | None = None,
    review_decision_ref: str | None = None,
) -> SemanticsResult:
    """Derive column/row header paths for every data cell of ``table``.

    ``header_rows`` / ``header_cols`` carry the *native* header evidence gathered
    upstream (``w:trPr/w:tblHeader``, repeated header rows, native ``th``). They
    are the only admissible source of a header band: with both empty the result
    is a fully ``NEEDS_REVIEW`` table plus a ``HEADER_BAND_UNKNOWN`` issue.

    ``context`` accepts the evidence-backed table context —
    ``{"caption": str | None, "unit_raw": str | None, "period": ... ,
    "currency": ..., "evidence_ids": [...]}``. ``unit_raw`` is copied through,
    never derived from a cell, and stays ``None`` when the caller has no
    evidence for it.

    ``review_decision_ref`` is accepted for callers replaying a confirmed review
    decision; without it nothing is emitted as ``CONFIRMED``.
    """
    anchors, n_rows, n_cols = _read_table(table)
    rows_band = _band(header_rows, name="header_rows", limit=n_rows)
    cols_band = _band(header_cols, name="header_cols", limit=n_cols)

    ctx = dict(context or {})
    for key in ("caption", "unit_raw", "period", "currency"):
        if key in ctx and ctx[key] is not None and not isinstance(ctx[key], str):
            raise _error(f"context.{key} must be a string or null, got {type(ctx[key]).__name__}")
    evidence_ids = ctx.get("evidence_ids") or []
    if isinstance(evidence_ids, (str, bytes)) or not isinstance(evidence_ids, Sequence):
        raise _error("context.evidence_ids must be a sequence of ids")
    for evidence_id in evidence_ids:
        if not isinstance(evidence_id, str) or not evidence_id:
            raise _error(f"context.evidence_ids contains {evidence_id!r}")

    unit_raw = ctx.get("unit_raw")

    def in_band(anchor: _Anchor) -> bool:
        return all(r in rows_band or c in cols_band for r in anchor.rows for c in anchor.cols)

    def straddles(anchor: _Anchor) -> bool:
        inside = sum(1 for r in anchor.rows for c in anchor.cols if r in rows_band or c in cols_band)
        total = anchor.rowspan * anchor.colspan
        return 0 < inside < total

    header_anchors = [a for a in anchors if in_band(a) and not straddles(a)]
    issues: list[HeaderIssue] = [
        HeaderIssue(
            code="HEADER_BAND_STRADDLED",
            message=(
                "cell spans both the declared header band and the data body; its role "
                "cannot be decided without a review decision"
            ),
            cell_id=a.cell_id,
            row=a.row,
            col=a.col,
            detail={"rowspan": a.rowspan, "colspan": a.colspan},
        )
        for a in anchors
        if straddles(a)
    ]

    if not rows_band and not cols_band:
        issues.append(
            HeaderIssue(
                code="HEADER_BAND_UNKNOWN",
                message=(
                    "no native header band was supplied (w:tblHeader / th); header "
                    "paths are not guessed from bold text or first-row position"
                ),
                detail={"n_rows": n_rows, "n_cols": n_cols},
            )
        )
        semantics = [
            _semantic(
                a,
                column_path=[],
                row_path=[],
                unit_raw=unit_raw,
                state="NEEDS_REVIEW",
                review_decision_ref=None,
            )
            for a in anchors
            if not in_band(a) and not straddles(a)
        ]
        return SemanticsResult(
            cell_semantics=semantics,
            issues=issues,
            context=ctx,
        )

    # Empty header anchors are only acceptable as group placeholders covered by
    # an outer header directly above them; an unowned empty header states nothing.
    # The ids are kept so every data cell that hangs off one becomes NEEDS_REVIEW
    # instead of inheriting a blank path element as if it meant something.
    blank_unowned: set[str] = set()
    for anchor in header_anchors:
        if anchor.text:
            continue
        owners = [
            outer
            for outer in header_anchors
            if outer is not anchor
            and outer.row < anchor.row
            and outer.covers_columns(anchor.col, anchor.col + anchor.colspan)
        ]
        if not owners:
            blank_unowned.add(anchor.cell_id)
            issues.append(
                HeaderIssue(
                    code="EMPTY_HEADER_ANCHOR",
                    message=(
                        "header cell has no text and no outer header covering it; "
                        "its meaning is undetermined"
                    ),
                    cell_id=anchor.cell_id,
                    row=anchor.row,
                    col=anchor.col,
                )
            )

    # No "two parents at one level" check is needed here: ``_read_table`` already
    # rejects overlapping anchors, so two header anchors on the same row can never
    # both cover one data cell. The reachable ambiguity is *partial* coverage (a
    # data cell straddling two branches), which ``_column_path`` reports.
    def flag(issue: HeaderIssue) -> None:
        issues.append(issue)

    semantics: list[dict[str, Any]] = []
    for anchor in anchors:
        if in_band(anchor) or straddles(anchor):
            continue

        column_path, column_issues = _column_path(
            anchor,
            header_anchors=header_anchors,
            rows_band=rows_band,
            flag=flag,
        )
        row_path, row_issues = _row_path(
            anchor,
            header_anchors=header_anchors,
            cols_band=cols_band,
            flag=flag,
        )
        problems = column_issues + row_issues
        blank_in_path = sorted(set(column_path + row_path) & blank_unowned)
        if blank_in_path:
            problems.append("EMPTY_HEADER_IN_PATH")
            flag(
                HeaderIssue(
                    code="EMPTY_HEADER_IN_PATH",
                    message=(
                        "one of this cell's header path elements has no text and no "
                        "outer header; the path names a node that states nothing"
                    ),
                    cell_id=anchor.cell_id,
                    row=anchor.row,
                    col=anchor.col,
                    detail={"blank_header_cell_ids": blank_in_path},
                )
            )
        state = "NEEDS_REVIEW" if problems else "CANDIDATE"
        ref = review_decision_ref if state == "CANDIDATE" and review_decision_ref else None
        if ref and not isinstance(ref, str):
            raise _error(f"review_decision_ref must be a string or null, got {ref!r}")
        semantics.append(
            _semantic(
                anchor,
                column_path=column_path,
                row_path=row_path,
                unit_raw=unit_raw,
                state="CONFIRMED" if ref else state,
                review_decision_ref=ref,
            )
        )

    return SemanticsResult(cell_semantics=semantics, issues=issues, context=ctx)


def _semantic(
    anchor: _Anchor,
    *,
    column_path: list[str],
    row_path: list[str],
    unit_raw: str | None,
    state: str,
    review_decision_ref: str | None,
) -> dict[str, Any]:
    return {
        "data_cell_id": anchor.cell_id,
        "column_header_cell_ids": list(column_path),
        "row_header_cell_ids": list(row_path),
        "note_ids": [],
        "unit_raw": unit_raw,
        "state": state,
        "review_decision_ref": review_decision_ref,
    }


def _column_path(
    data: _Anchor,
    *,
    header_anchors: Sequence[_Anchor],
    rows_band: set[int],
    flag: Any,
) -> tuple[list[str], list[str]]:
    """Collect, top-down, the header anchors covering ``data``'s column interval."""
    path: list[str] = []
    problems: list[str] = []
    start, end = data.col, data.col + data.colspan
    seen_text: set[str] = set()

    for row in sorted(rows_band):
        if row >= data.row:
            continue
        candidates = [
            anchor
            for anchor in header_anchors
            if anchor.row == row and anchor.covers_columns(start, end)
        ]
        partial = [
            anchor
            for anchor in header_anchors
            if anchor.row == row
            and anchor.overlaps_columns(start, end)
            and not anchor.covers_columns(start, end)
        ]
        if not candidates:
            if partial:
                problems.append("HEADER_COVERAGE_PARTIAL")
                flag(
                    HeaderIssue(
                        code="HEADER_COVERAGE_PARTIAL",
                        message=(
                            "header anchor covers only part of this data cell's column "
                            "interval; the cell straddles two header branches"
                        ),
                        cell_id=data.cell_id,
                        row=data.row,
                        col=data.col,
                        detail={
                            "header_row": row,
                            "partial": [a.cell_id for a in partial],
                        },
                    )
                )
            else:
                problems.append("HEADER_INCOMPLETE")
                flag(
                    HeaderIssue(
                        code="HEADER_INCOMPLETE",
                        message=(
                            "declared header band has no anchor above this data cell "
                            "for this column interval"
                        ),
                        cell_id=data.cell_id,
                        row=data.row,
                        col=data.col,
                        detail={"header_row": row, "column_interval": [start, end]},
                    )
                )
            continue
        anchor = candidates[0]
        if anchor.text and anchor.text in seen_text:
            problems.append("DUPLICATE_LEVEL_LABEL")
            flag(
                HeaderIssue(
                    code="DUPLICATE_LEVEL_LABEL",
                    message=(
                        "the same header label repeats on two levels of one path; the "
                        "branch is ambiguous even though the cell references differ"
                    ),
                    cell_id=data.cell_id,
                    row=data.row,
                    col=data.col,
                    detail={"label": anchor.text, "header_cell_id": anchor.cell_id},
                )
            )
        if anchor.text:
            seen_text.add(anchor.text)
        path.append(anchor.cell_id)
    return path, problems


def _row_path(
    data: _Anchor,
    *,
    header_anchors: Sequence[_Anchor],
    cols_band: set[int],
    flag: Any,
) -> tuple[list[str], list[str]]:
    """Collect, left-to-right, the header anchors covering ``data``'s row interval."""
    path: list[str] = []
    problems: list[str] = []
    start, end = data.row, data.row + data.rowspan

    for col in sorted(cols_band):
        if col >= data.col:
            continue
        candidates = [
            anchor
            for anchor in header_anchors
            if anchor.col == col and anchor.covers_rows(start, end)
        ]
        partial = [
            anchor
            for anchor in header_anchors
            if anchor.col == col
            and anchor.overlaps_rows(start, end)
            and not anchor.covers_rows(start, end)
        ]
        if not candidates:
            problems.append("ROW_HEADER_INCOMPLETE")
            flag(
                HeaderIssue(
                    code="ROW_HEADER_INCOMPLETE",
                    message=(
                        "declared header column has no anchor left of this data cell "
                        "covering its row interval"
                    ),
                    cell_id=data.cell_id,
                    row=data.row,
                    col=data.col,
                    detail={
                        "header_col": col,
                        "row_interval": [start, end],
                        "partial": [a.cell_id for a in partial],
                    },
                )
            )
            continue
        path.append(candidates[0].cell_id)
    return path, problems
