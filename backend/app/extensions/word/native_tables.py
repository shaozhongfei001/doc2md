"""Native Word tables: ``w:tbl`` → contract-shaped ``Table`` payloads.

Why this module exists (SDD §7): iterating ``row.cells`` through a convenience
wrapper is not structure-preserving — a merged cell can be reported at several
grid addresses, and two different cells that happen to hold the same string look
identical. The only authority is the raw OOXML, so every value here is read from
``w:tblGrid``, ``w:tc``, ``w:gridSpan``, ``w:vMerge`` and ``w:trPr`` directly.

The rules that make the output trustworthy:

* ``slots[r][c]`` is an **anchor id matrix**; a covered position points back at
  the anchor that owns it and never repeats its text;
* a ``w:vMerge="continue"`` cell must attach to the anchor covering exactly the
  same column interval in the row above. No anchor, a shifted interval or a
  partially covered interval is ``E_DOCX_MERGE_INVALID`` — merging "to the
  nearest cell" is precisely the silent corruption the SDD forbids;
* ``w:trPr/w:gridBefore|gridAfter`` are the *only* accepted holes, and each one
  carries its own reproducible evidence id, so a hole can always be traced back
  to XML instead of being inferred from missing cells;
* an empty ``w:tc`` is a valid empty cell (``text=""``), never an omission;
* a nested ``w:tbl`` becomes its own ``table_id`` with ``parent_cell_id`` set and
  the parent cell listing it in ``nested_table_ids``; the child's text is not
  flattened into the parent;
* anything the final shape of which cannot be decided (``w:cellMerge``,
  ``w:cellDel``, ``w:trPr/w:del``, ``*PrChange``) is refused with
  ``E_DOCX_REVISION_UNRESOLVED`` rather than guessed.

Support matrix for merges (SDD §7 "复杂结构修订属于明确的支持矩阵")
------------------------------------------------------------------
* supported: ``w:gridSpan`` (horizontal), ``w:vMerge`` restart/continue
  (vertical), nested tables, ``w:gridBefore/w:gridAfter`` omissions;
* supported: text revisions inside cells (``w:ins`` counted, ``w:del`` dropped);
* **refused**: legacy ``w:tcPr/w:hMerge`` (Word 97-era horizontal merge). It is
  not a cell count: the merged *width* lives outside the cell, so reading it as a
  normal ``w:tc`` would make the table wider than Word renders and would leave no
  hole for ``tables.grid`` to catch. Both ``restart`` and ``continue`` — and an
  ambiguous value — raise ``E_DOCX_MERGE_INVALID`` so the table goes to review
  instead of being silently misread;
* **refused**: structural revisions (see above) — ``E_DOCX_REVISION_UNRESOLVED``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.extensions import ExtensionError
from app.extensions.tables.grid import build_slots, to_contract_table, validate_grid
from app.extensions.word.final_view import FinalView, structural_revision_notes, visible_text
from app.extensions.word.package import DocxPackage, parse_xml_part

__all__ = ["extract_tables", "TABLE_KEYS", "CELL_KEYS"]

MAIN_PART = "word/document.xml"

#: Contract ``Table`` key set (``document-enhancement.schema.json``).
TABLE_KEYS = (
    "table_id",
    "ir_ref",
    "parent_cell_id",
    "n_rows",
    "n_cols",
    "cells",
    "slots",
    "omitted_slots",
    "omission_evidence_ids",
    "cell_semantics",
    "notes",
    "source_html_ref",
    "structure_state",
    "review_decision_ref",
)

#: Contract ``Cell`` key set — fixed for every cell, no per-cell extras.
CELL_KEYS = (
    "cell_id",
    "row",
    "col",
    "rowspan",
    "colspan",
    "text",
    "text_origin",
    "text_alignment",
    "engine_confidence",
    "token_ids",
    "evidence_ids",
    "nested_table_ids",
)

_NONE, _RESTART, _CONTINUE = "NONE", "RESTART", "CONTINUE"
_UINT_RE = re.compile(r"^[0-9]+$")


def _localname(element: Any) -> str:
    tag = getattr(element, "tag", None)
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _first(element: Any, name: str) -> Any | None:
    if element is None:
        return None
    for child in element:
        if _localname(child) == name:
            return child
    return None


def _wattr(element: Any, name: str) -> str | None:
    for key, value in element.attrib.items():
        if key.rsplit("}", 1)[-1] == name:
            return value
    return None


def _span_invalid(message: str, **detail: Any) -> None:
    raise ExtensionError("E_TABLE_SPAN_INVALID", message, detail=detail)


def _merge_invalid(message: str, **detail: Any) -> None:
    raise ExtensionError("E_DOCX_MERGE_INVALID", message, detail=detail)


# ------------------------------------------------------------------ traversal
def _rows_in(element: Any):
    """Row elements in document order; nested tables are a separate object."""
    for child in element:
        name = _localname(child)
        if name == "tr":
            yield child
        elif name == "tbl":
            continue
        else:
            yield from _rows_in(child)


def _cells_in(row: Any):
    for child in row:
        name = _localname(child)
        if name == "tc":
            yield child
        elif name == "tbl":
            continue
        else:
            yield from _cells_in(child)


def _nested_tables(cell: Any) -> list[Any]:
    """``w:tbl`` children of a cell, one level (their own rows stay theirs)."""
    found: list[Any] = []

    def walk(element: Any) -> None:
        for child in element:
            name = _localname(child)
            if name == "tbl":
                found.append(child)
            elif name in ("tcPr", "pPr", "trPr", "tblPr"):
                continue
            else:
                walk(child)

    walk(cell)
    return found


def _top_level_tables(element: Any) -> list[Any]:
    found: list[Any] = []

    def walk(node: Any) -> None:
        for child in node:
            if _localname(child) == "tbl":
                found.append(child)
            else:
                walk(child)

    walk(element)
    return found


# ---------------------------------------------------------------- OOXML facts
@dataclass(frozen=True)
class _RawCell:
    element: Any
    span: int
    vmerge: str
    row: int


@dataclass(frozen=True)
class _RawRow:
    element: Any
    grid_before: int
    grid_after: int
    cells: tuple[_RawCell, ...]

    @property
    def extent(self) -> int:
        return self.grid_before + sum(cell.span for cell in self.cells) + self.grid_after


def _count_attr(element: Any, name: str, *, table_id: str, row: int) -> int:
    """Read ``w:gridBefore`` / ``w:gridAfter`` (a column count, not a width)."""
    node = _first(element, name)
    if node is None:
        return 0
    raw = _wattr(node, "val")
    if raw is None:
        _span_invalid(
            f"table {table_id} row {row}: w:{name} has no w:val",
            table_id=table_id,
            row=row,
        )
    text = raw.strip()
    if not _UINT_RE.match(text):
        _span_invalid(
            f"table {table_id} row {row}: w:{name}={raw!r} is not a grid count",
            table_id=table_id,
            row=row,
            value=raw,
        )
    return int(text)


def _cell_span(cell: Any, *, table_id: str, row: int, index: int) -> int:
    props = _first(cell, "tcPr")
    node = _first(props, "gridSpan") if props is not None else None
    if node is None:
        return 1
    raw = _wattr(node, "val")
    where = {"table_id": table_id, "row": row, "cell_index": index}
    if raw is None:
        _span_invalid(
            f"table {table_id} row {row} cell {index}: w:gridSpan has no w:val",
            **where,
        )
    text = raw.strip()
    if not _UINT_RE.match(text):
        _span_invalid(
            f"table {table_id} row {row} cell {index}: w:gridSpan={raw!r} is not "
            "a positive integer",
            **where,
            value=raw,
        )
    span = int(text)
    if span <= 0:
        _span_invalid(
            f"table {table_id} row {row} cell {index}: w:gridSpan must be >= 1",
            **where,
            value=span,
        )
    return span


def _cell_vmerge(cell: Any, *, table_id: str, row: int, index: int) -> str:
    props = _first(cell, "tcPr")
    node = _first(props, "vMerge") if props is not None else None
    if node is None:
        return _NONE
    raw = _wattr(node, "val")
    if raw is None or raw == "continue":
        # ``<w:vMerge/>`` means "continue" in OOXML.
        return _CONTINUE
    if raw == "restart":
        return _RESTART
    _merge_invalid(
        f"table {table_id} row {row} cell {index}: w:vMerge={raw!r} is not a "
        "known merge state",
        table_id=table_id,
        row=row,
        cell_index=index,
        value=raw,
    )


def _reject_hmerge(
    cell: Any, *, table_id: str, row: int, column: int, index: int
) -> None:
    """Legacy ``w:hMerge`` is fatal: it cannot be reconstructed from the cell.

    ``w:hMerge`` is the Word 97-era horizontal merge. Unlike ``w:gridSpan`` it does
    not carry the merged width, so treating such a ``w:tc`` as an ordinary cell
    would make the table wider than Word renders *and* leave no hole for
    ``tables.grid`` to detect — a silent structural misread. It is therefore
    refused, never mapped and never ignored.
    """
    props = _first(cell, "tcPr")
    node = _first(props, "hMerge") if props is not None else None
    if node is None:
        return
    raw = _wattr(node, "val")
    if raw is None or raw == "continue":
        state = "continue"
    elif raw == "restart":
        state = "restart"
    else:
        state = f"unknown value {raw!r}"
    _merge_invalid(
        f"table {table_id} row {row} column {column} (cell {index}): legacy "
        f"w:hMerge ({state}) is not supported; the merged width is not carried by "
        "the cell, so reading it as a normal cell would silently widen the table "
        "— re-parse after accepting/rejecting revisions in Word, or hand the "
        "table to human review",
        table_id=table_id,
        row=row,
        col=column,
        cell_index=index,
        hmerge=state,
    )


def _parse_row(row: Any, *, table_id: str, index: int) -> _RawRow:
    props = _first(row, "trPr")
    grid_before = _count_attr(props, "gridBefore", table_id=table_id, row=index)
    grid_after = _count_attr(props, "gridAfter", table_id=table_id, row=index)
    parsed: list[_RawCell] = []
    column = grid_before
    for position, cell in enumerate(_cells_in(row)):
        span = _cell_span(cell, table_id=table_id, row=index, index=position)
        vmerge = _cell_vmerge(cell, table_id=table_id, row=index, index=position)
        _reject_hmerge(
            cell,
            table_id=table_id,
            row=index,
            column=column,
            index=position,
        )
        parsed.append(_RawCell(element=cell, span=span, vmerge=vmerge, row=index))
        column += span
    return _RawRow(
        element=row,
        grid_before=grid_before,
        grid_after=grid_after,
        cells=tuple(parsed),
    )


def _grid_columns(table: Any) -> int:
    grid = _first(table, "tblGrid")
    if grid is None:
        return 0
    return sum(1 for child in grid if _localname(child) == "gridCol")


# ----------------------------------------------------------------- table build
@dataclass
class _Ids:
    """Deterministic, document-order ids (no randomness, no timestamps)."""

    tables: int = 0

    def table(self) -> str:
        self.tables += 1
        return f"TBL-{self.tables:04d}"


def _build_cell(
    raw: _RawCell,
    *,
    table_id: str,
    row: int,
    col: int,
    ids: _Ids,
    sink: list[dict[str, Any]],
) -> dict[str, Any]:
    cell_id = f"{table_id}-R{row:04d}C{col:04d}"
    nested = [
        _build_table(child, parent_cell_id=cell_id, ids=ids, sink=sink)
        for child in _nested_tables(raw.element)
    ]
    return {
        "cell_id": cell_id,
        "row": row,
        "col": col,
        "rowspan": 1,
        "colspan": raw.span,
        "text": visible_text(raw.element).rstrip("\n"),
        "text_origin": "SOURCE_XML",
        "text_alignment": "NATIVE",
        "engine_confidence": None,
        "token_ids": [],
        "evidence_ids": [],
        "nested_table_ids": [child["table_id"] for child in nested],
    }


def _build_table(
    table: Any,
    *,
    parent_cell_id: str | None,
    ids: _Ids,
    sink: list[dict[str, Any]],
) -> dict[str, Any]:
    table_id = ids.table()
    # Reserve the parent slot first so nested tables land *after* it (document order).
    slot_index = len(sink)
    sink.append({})
    rows = [_parse_row(row, table_id=table_id, index=index) for index, row in enumerate(_rows_in(table))]

    declared_cols = _grid_columns(table)
    derived = declared_cols == 0
    n_cols = declared_cols or max((row.extent for row in rows), default=0)
    n_rows = len(rows)

    # Anchor bounds / overlap / span-vs-grid are decided by ``tables.grid`` when the
    # anchors are submitted; the only rule that module cannot know is the *Word*
    # one: a row that describes fewer columns than ``w:tblGrid`` leaves trailing
    # holes, and Word only justifies those with an explicit ``w:gridAfter``.
    for index, row in enumerate(rows):
        if row.extent < n_cols:
            _span_invalid(
                f"table {table_id} row {index}: {row.extent} of {n_cols} grid "
                "columns are described; trailing columns need "
                "w:trPr/w:gridAfter rather than being assumed",
                table_id=table_id,
                row=index,
                extent=row.extent,
                n_cols=n_cols,
            )

    cells: list[dict[str, Any]] = []
    omitted: list[tuple[int, int]] = []
    evidence: list[str] = []
    #: Row → column → owning anchor. Used *only* to resolve ``w:vMerge``
    #: adjacency (the grid module cannot see ``vMerge``); the persisted matrix is
    #: always produced by ``build_slots``.
    previous: dict[int, dict[str, Any]] = {}
    #: Only a ``w:vMerge="restart"`` cell may absorb a ``continue`` cell. A plain
    #: cell sitting above a continue cell is *not* an anchor.
    merge_anchors: set[str] = set()

    for index, row in enumerate(rows):
        current: dict[int, dict[str, Any]] = {}
        for col in range(row.grid_before):
            omitted.append((index, col))
            evidence.append(f"docx:gridBefore:{index}:{col}")
        cursor = row.grid_before
        for cell_index, raw in enumerate(row.cells):
            col = cursor
            end = col + raw.span
            if raw.vmerge == _CONTINUE:
                anchor = previous.get(col)
                if anchor is None or anchor["cell_id"] not in merge_anchors:
                    _merge_invalid(
                        f"table {table_id} row {index} cell {cell_index}: vertical "
                        f"merge continue at column {col} has no anchor in the row above",
                        table_id=table_id,
                        row=index,
                        cell_index=cell_index,
                        col=col,
                    )
                if anchor["col"] != col or anchor["colspan"] != raw.span:
                    _merge_invalid(
                        f"table {table_id} row {index} cell {cell_index}: vertical "
                        f"merge continue covers columns {col}..{end - 1} but the "
                        f"anchor above covers {anchor['col']}.."
                        f"{anchor['col'] + anchor['colspan'] - 1}",
                        table_id=table_id,
                        row=index,
                        cell_index=cell_index,
                        col=col,
                        colspan=raw.span,
                        anchor_cell_id=anchor["cell_id"],
                    )
                if any(previous.get(column) is not anchor for column in range(col, end)):
                    _merge_invalid(
                        f"table {table_id} row {index} cell {cell_index}: vertical "
                        f"merge continue spans {col}..{end - 1}, which is not owned "
                        "by a single anchor above",
                        table_id=table_id,
                        row=index,
                        cell_index=cell_index,
                        col=col,
                        colspan=raw.span,
                    )
                anchor["rowspan"] += 1
                for column in range(col, end):
                    current[column] = anchor
            else:
                cell = _build_cell(
                    raw, table_id=table_id, row=index, col=col, ids=ids, sink=sink
                )
                if raw.vmerge == _RESTART:
                    merge_anchors.add(cell["cell_id"])
                cells.append(cell)
                for column in range(col, end):
                    current[column] = cell
            cursor = end
        for col in range(cursor, cursor + row.grid_after):
            omitted.append((index, col))
            evidence.append(f"docx:gridAfter:{index}:{col}")
        previous = current

    notes: list[dict[str, Any]] = []
    if derived and n_cols:
        notes.append(
            {
                "note_id": f"{table_id}-NOTE-GRID",
                "text": (
                    "w:tblGrid is absent or empty; the column count was derived "
                    "from the explicit cell spans of the widest row"
                ),
                "scope": "TABLE",
                "target_ids": [table_id],
                "evidence_ids": [f"docx:tblGrid:derived:{table_id}"],
                "state": "NEEDS_REVIEW",
                "review_decision_ref": None,
            }
        )

    # --- structure validation and payload shape are the grid module's job ----
    slots = build_slots(cells, n_rows=n_rows, n_cols=n_cols)
    validated = validate_grid(
        cells=cells,
        n_rows=n_rows,
        n_cols=n_cols,
        slots=slots,
        omitted_slots=omitted,
        omission_evidence_ids=evidence,
    )
    payload = to_contract_table(
        table_id,
        cells,
        parent_cell_id=parent_cell_id,
        ir_ref=None,
        omission_evidence_ids=evidence,
    )
    # ``to_contract_table`` sizes the grid from the outermost anchor edge. That is
    # right for a pure anchor grid, but a Word grid can be *wider or taller* than
    # its anchors: trailing ``w:gridAfter`` columns and a row whose cells are all
    # omitted leave no anchor there. The XML ``w:tblGrid`` is authoritative for the
    # document, so keep the validated dimensions and take the slots/omissions from
    # ``validate_grid`` instead of silently shrinking the table.
    if (payload["n_rows"], payload["n_cols"]) != (validated.n_rows, validated.n_cols):
        payload["n_rows"] = validated.n_rows
        payload["n_cols"] = validated.n_cols
        payload["slots"] = [list(row) for row in validated.slots]
        payload["omitted_slots"] = list(validated.omitted_slots)
        payload["omission_evidence_ids"] = evidence
    if notes:
        payload["notes"] = notes
    sink[slot_index] = payload
    return payload


# ------------------------------------------------------------------ public API
def extract_tables(package: DocxPackage, view: FinalView | None = None) -> list[dict]:
    """Return every native table of ``package`` in document order.

    ``view`` is optional; when a :class:`FinalView` is supplied its structural
    findings are authoritative and extraction stops before touching the grid.
    """
    if view is not None and view.unresolved_structural:
        raise ExtensionError(
            "E_DOCX_REVISION_UNRESOLVED",
            "final table shape is indeterminate; resolve the structural "
            "revisions recorded in the final view before extracting tables",
            detail=list(view.unresolved_structural),
        )

    document = parse_xml_part(package, MAIN_PART)
    body = _first(document, "body")
    if body is None:
        raise ExtensionError(
            "E_INPUT_INVALID",
            "docx main part has no w:body",
            detail={"part": MAIN_PART},
        )

    tables = _top_level_tables(body)
    if not tables:
        return []

    structural = structural_revision_notes(document, MAIN_PART)
    if structural:
        raise ExtensionError(
            "E_DOCX_REVISION_UNRESOLVED",
            "the document carries structural revisions whose final table shape "
            "cannot be decided from XML alone",
            detail=structural,
        )

    ids = _Ids()
    sink: list[dict[str, Any]] = []
    for table in tables:
        _build_table(table, parent_cell_id=None, ids=ids, sink=sink)
    return sink
