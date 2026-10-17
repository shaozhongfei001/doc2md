"""Table grid matrix (``slots``) construction and strict validation.

Scope
-----
A table in ``document-enhancement/1.0`` is stored twice: once as a list of
**anchor cells** carrying ``row``/``col``/``rowspan``/``colspan`` and the text,
and once as a ``slots`` matrix mapping every covered grid position back to the
anchor that owns it. Per ``01_SDD.md`` §8.3 the covered positions *point at the
anchor* — text is never duplicated into each slot, because a duplicated string
would become an independent "fact" that can drift away from its anchor.

This module is the single place where that matrix is built and checked. It is
engine-agnostic: the native Word path, the OCR path and the reviewed-patch path
all funnel through :func:`validate_grid` before a table may be persisted as a
legal structure candidate.

Hard rules encoded here (all failures are ``E_TABLE_SPAN_INVALID``)
--------------------------------------------------------------------
* a span must be a positive integer; ``0``, negative and non-integer spans are
  **refused**, never repaired to ``1`` (repairing would invent a grid);
* an anchor must stay inside the declared grid, and no two anchors may overlap;
* a ``cell_id`` must be unique inside one table;
* every empty position in the matrix is a *hole* and must be declared in
  ``omitted_slots`` **and** backed by ``omission_evidence_ids``. A scanner that
  simply failed to see a cell therefore cannot hide the miss by marking the
  position "omitted": the omission needs source proof, the miss has none;
* a caller-supplied ``slots`` matrix that disagrees with the recomputed one is
  refused, so a stale or hand-edited matrix cannot outlive its anchors.

What passing this module does not prove
---------------------------------------
It proves the *structure* is internally consistent, nothing more: not that the
spans match the source document, not that the OCR aligned the right token to the
right cell, and not that an empty ``tc`` really is empty (a genuinely blank cell
is a valid cell with ``text=""``, not a hole). Those claims need source XML
evidence and, where ambiguous, human review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.extensions import ExtensionError

__all__ = [
    "GridResult",
    "build_slots",
    "validate_grid",
    "to_contract_table",
    "SPAN_FIELDS",
    "ANCHOR_KEYS",
    "TABLE_KEYS",
]

#: Span fields that must be positive integers (never defaulted).
SPAN_FIELDS: tuple[str, ...] = ("rowspan", "colspan")

#: Required keys of an input anchor cell.
ANCHOR_KEYS: tuple[str, ...] = ("cell_id", "row", "col", "rowspan", "colspan")

#: Exact key set of the contract ``Table`` object emitted by :func:`to_contract_table`.
TABLE_KEYS: tuple[str, ...] = (
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

_DEFAULT_TEXT_ORIGIN = "SOURCE_XML"
_DEFAULT_TEXT_ALIGNMENT = "NATIVE"

SPAN_CODE = "E_TABLE_SPAN_INVALID"
INPUT_CODE = "E_INPUT_INVALID"


# --------------------------------------------------------------------- result
@dataclass(frozen=True)
class GridResult:
    """A validated grid: the matrix plus the anchors that produced it.

    ``slots[r][c]`` is the ``cell_id`` of the anchor covering ``(r, c)`` or
    ``None`` for a hole; :func:`validate_grid` has already proven every hole is
    declared and evidenced. ``anchor_cells`` are contract-``Cell`` shaped dicts
    in caller order, so the result can be persisted without a second pass.
    """

    n_rows: int
    n_cols: int
    slots: list[list[str | None]]
    omitted_slots: list[tuple[int, int]]
    anchor_cells: tuple[dict, ...]


# -------------------------------------------------------------------- helpers
def _is_int(value: Any) -> bool:
    """``True`` only for real integers: ``bool`` is an ``int`` subclass and lies."""
    return isinstance(value, int) and not isinstance(value, bool)


def _span_error(message: str, *, cell_id: Any, row: Any, col: Any, **detail: Any) -> ExtensionError:
    payload: dict[str, Any] = {"cell_id": cell_id, "row": row, "col": col}
    payload.update(detail)
    return ExtensionError(SPAN_CODE, message, detail=payload)


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError(INPUT_CODE, message, detail=detail or None)


def _anchor_is_a_mapping(index: int, cell: Any) -> Mapping:
    if not isinstance(cell, Mapping):
        raise _input_error(
            f"cells[{index}] must be a mapping of anchor fields, got {type(cell).__name__}; "
            "a table anchor is never inferred from a bare string or number",
            index=index,
        )
    return cell


def _string_list(value: Any, *, field: str, cell_id: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _input_error(
            f"cell {cell_id!r} field {field!r} must be a sequence of id strings",
            cell_id=cell_id,
            field=field,
        )
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise _input_error(
                f"cell {cell_id!r} field {field!r} contains a non-string or empty id {item!r}",
                cell_id=cell_id,
                field=field,
            )
        items.append(item)
    return items


def _grid_dims(n_rows: Any, n_cols: Any) -> tuple[int, int]:
    for name, value in (("n_rows", n_rows), ("n_cols", n_cols)):
        if not _is_int(value):
            raise _input_error(
                f"{name} must be a non-negative integer, got {value!r} "
                f"({type(value).__name__})",
                field=name,
            )
        if value < 0:
            raise _input_error(
                f"{name} must be a non-negative integer, got {value}", field=name
            )
    return int(n_rows), int(n_cols)


def _normalise_anchor(
    index: int, raw: Mapping, *, n_rows: int, n_cols: int
) -> dict:
    """Validate one anchor and return it in contract-``Cell`` shape."""
    for key in ANCHOR_KEYS:
        if key not in raw:
            raise _input_error(
                f"cells[{index}] is missing required field {key!r}; every anchor needs "
                f"cell_id/row/col/rowspan/colspan (got {sorted(raw)})",
                index=index,
                field=key,
            )

    cell_id = raw["cell_id"]
    if not isinstance(cell_id, str) or not cell_id:
        raise _input_error(
            f"cells[{index}] has a non-string or empty cell_id {cell_id!r}",
            index=index,
        )

    row = raw["row"]
    col = raw["col"]
    for name, value in (("row", row), ("col", col)):
        if not _is_int(value):
            raise _input_error(
                f"cell {cell_id!r} has a non-integer {name} {value!r}",
                cell_id=cell_id,
            )
        if value < 0:
            raise _span_error(
                f"cell {cell_id!r} anchor at ({name}={value}) is outside the grid: "
                "anchor coordinates must be non-negative",
                cell_id=cell_id,
                row=row,
                col=col,
            )

    rowspan = raw["rowspan"]
    colspan = raw["colspan"]
    for name, value in (("rowspan", rowspan), ("colspan", colspan)):
        if not _is_int(value) or value <= 0:
            raise _span_error(
                f"cell {cell_id!r} at (row={row}, col={col}) has invalid {name}={value!r}; "
                f"{name} must be a positive integer and is never repaired to 1",
                cell_id=cell_id,
                row=row,
                col=col,
                field=name,
                value=value,
            )

    if row + rowspan > n_rows or col + colspan > n_cols:
        raise _span_error(
            f"cell {cell_id!r} at (row={row}, col={col}) with "
            f"rowspan={rowspan}, colspan={colspan} runs outside the "
            f"{n_rows}x{n_cols} grid",
            cell_id=cell_id,
            row=row,
            col=col,
            n_rows=n_rows,
            n_cols=n_cols,
        )

    text = raw.get("text")
    if text is not None and not isinstance(text, str):
        raise _input_error(
            f"cell {cell_id!r} text must be a string or null, got {type(text).__name__}; "
            "a blank cell is the empty string, an unknown cell is null",
            cell_id=cell_id,
        )

    confidence = raw.get("engine_confidence")
    if confidence is not None and not isinstance(confidence, (int, float)):
        raise _input_error(
            f"cell {cell_id!r} engine_confidence must be a number or null",
            cell_id=cell_id,
        )
    if isinstance(confidence, bool):
        raise _input_error(
            f"cell {cell_id!r} engine_confidence must be a number or null",
            cell_id=cell_id,
        )

    return {
        "cell_id": cell_id,
        "row": row,
        "col": col,
        "rowspan": rowspan,
        "colspan": colspan,
        "text": text,
        "text_origin": raw.get("text_origin", _DEFAULT_TEXT_ORIGIN),
        "text_alignment": raw.get("text_alignment", _DEFAULT_TEXT_ALIGNMENT),
        "engine_confidence": confidence,
        "token_ids": _string_list(raw.get("token_ids"), field="token_ids", cell_id=cell_id),
        "evidence_ids": _string_list(
            raw.get("evidence_ids"), field="evidence_ids", cell_id=cell_id
        ),
        "nested_table_ids": _string_list(
            raw.get("nested_table_ids"), field="nested_table_ids", cell_id=cell_id
        ),
    }


def _build(
    cells: Sequence[Mapping], *, n_rows: int, n_cols: int
) -> tuple[tuple[dict, ...], list[list[str | None]]]:
    """Validate every anchor and fill the matrix; returns anchors + matrix."""
    if isinstance(cells, (str, bytes)) or not isinstance(cells, Sequence):
        raise _input_error(
            f"cells must be a sequence of anchor mappings, got {type(cells).__name__}"
        )

    anchors: list[dict] = []
    seen: dict[str, int] = {}
    for index, cell in enumerate(cells):
        raw = _anchor_is_a_mapping(index, cell)
        anchor = _normalise_anchor(index, raw, n_rows=n_rows, n_cols=n_cols)
        cell_id = anchor["cell_id"]
        if cell_id in seen:
            raise _span_error(
                f"duplicate cell_id {cell_id!r} at cells[{index}] (first seen at "
                f"cells[{seen[cell_id]}]); ids key the slot matrix and must be unique",
                cell_id=cell_id,
                row=anchor["row"],
                col=anchor["col"],
                first_index=seen[cell_id],
                index=index,
            )
        seen[cell_id] = index
        anchors.append(anchor)

    matrix: list[list[str | None]] = [[None] * n_cols for _ in range(n_rows)]
    for anchor in anchors:
        cell_id = anchor["cell_id"]
        for r in range(anchor["row"], anchor["row"] + anchor["rowspan"]):
            for c in range(anchor["col"], anchor["col"] + anchor["colspan"]):
                occupant = matrix[r][c]
                if occupant is not None:
                    raise _span_error(
                        f"cell {cell_id!r} span covers (row={r}, col={c}) which is "
                        f"already owned by cell {occupant!r}; anchors may not overlap",
                        cell_id=cell_id,
                        row=r,
                        col=c,
                        occupant=occupant,
                    )
                matrix[r][c] = cell_id

    return tuple(anchors), matrix


def _normalise_slots(slots: Any, *, n_rows: int, n_cols: int) -> list[list[str | None]]:
    if isinstance(slots, (str, bytes)) or not isinstance(slots, Sequence):
        raise _input_error(
            f"slots must be a sequence of rows, got {type(slots).__name__}"
        )
    rows: list[list[str | None]] = []
    for index, row in enumerate(slots):
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            raise _input_error(
                f"slots[{index}] must be a sequence of cell ids / nulls, "
                f"got {type(row).__name__}"
            )
        values: list[str | None] = []
        for col, value in enumerate(row):
            if value is not None and (not isinstance(value, str) or not value):
                raise _input_error(
                    f"slots[{index}][{col}] must be a cell_id string or null, got {value!r}",
                    row=index,
                    col=col,
                )
            values.append(value)
        rows.append(values)
    return rows


def _normalise_omitted(omitted_slots: Any, *, n_rows: int, n_cols: int) -> list[tuple[int, int]]:
    if omitted_slots is None:
        return []
    if isinstance(omitted_slots, (str, bytes)) or not isinstance(omitted_slots, Sequence):
        raise _input_error(
            f"omitted_slots must be a sequence of (row, col) pairs, "
            f"got {type(omitted_slots).__name__}"
        )
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for item in omitted_slots:
        if isinstance(item, (str, bytes)) or not isinstance(item, Sequence) or len(item) != 2:
            raise _input_error(
                f"omitted slot {item!r} is not a (row, col) pair"
            )
        row, col = item[0], item[1]
        if not _is_int(row) or not _is_int(col):
            raise _input_error(
                f"omitted slot {item!r} has non-integer coordinates"
            )
        if not (0 <= row < n_rows) or not (0 <= col < n_cols):
            raise _span_error(
                f"omitted slot (row={row}, col={col}) is outside the "
                f"{n_rows}x{n_cols} grid",
                cell_id=None,
                row=row,
                col=col,
                n_rows=n_rows,
                n_cols=n_cols,
            )
        pair = (row, col)
        if pair in seen:
            raise _span_error(
                f"omitted slot (row={row}, col={col}) is declared twice",
                cell_id=None,
                row=row,
                col=col,
            )
        seen.add(pair)
        pairs.append(pair)
    return pairs


def _normalise_evidence(omission_evidence_ids: Any) -> list[str]:
    if omission_evidence_ids is None:
        return []
    if isinstance(omission_evidence_ids, (str, bytes)) or not isinstance(
        omission_evidence_ids, Sequence
    ):
        raise _input_error(
            "omission_evidence_ids must be a sequence of evidence id strings, "
            f"got {type(omission_evidence_ids).__name__}"
        )
    evidence: list[str] = []
    for item in omission_evidence_ids:
        if not isinstance(item, str) or not item:
            raise _input_error(
                f"omission_evidence_ids contains a non-string or empty id {item!r}"
            )
        evidence.append(item)
    return evidence


def _holes(matrix: list[list[str | None]]) -> list[tuple[int, int]]:
    return [
        (r, c)
        for r, row in enumerate(matrix)
        for c, value in enumerate(row)
        if value is None
    ]


def _first_difference(
    provided: list[list[str | None]], expected: list[list[str | None]]
) -> tuple[int, int] | None:
    for r, (prow, erow) in enumerate(zip(provided, expected)):
        for c, (pvalue, evalue) in enumerate(zip(prow, erow)):
            if pvalue != evalue:
                return (r, c)
    return None


# --------------------------------------------------------------- public API
def build_slots(
    cells: Sequence[Mapping], *, n_rows: int, n_cols: int
) -> list[list[str | None]]:
    """Build the ``slots`` matrix from anchor cells.

    Covered positions point at the owning ``cell_id``; positions no anchor
    claims stay ``None``. Holes are *reported as* ``None`` here — whether they
    are allowed is :func:`validate_grid`'s decision, because only that layer
    sees the omission evidence.

    Raises ``E_TABLE_SPAN_INVALID`` for a non-positive / non-integer span, an
    anchor outside the grid, overlapping anchors or a duplicate ``cell_id``, and
    ``E_INPUT_INVALID`` when the cells themselves are not anchor mappings.
    """
    rows, cols = _grid_dims(n_rows, n_cols)
    _, matrix = _build(cells, n_rows=rows, n_cols=cols)
    return matrix


def validate_grid(
    *,
    cells: Sequence[Mapping],
    n_rows: int,
    n_cols: int,
    slots: Sequence[Sequence[str | None]],
    omitted_slots: Sequence[tuple[int, int]] = (),
    omission_evidence_ids: Sequence[str] = (),
) -> GridResult:
    """Recompute the grid and refuse any disagreement with the caller's claims.

    The caller's ``slots``/``omitted_slots`` are treated as *claims to verify*,
    never as truth: the matrix is rebuilt from the anchors and compared. Every
    empty position must be declared in ``omitted_slots`` and covered by at least
    one ``omission_evidence_ids`` entry, which is what stops a scan miss from
    posing as a deliberate Word omission (``gridBefore``).
    """
    rows, cols = _grid_dims(n_rows, n_cols)
    evidence = _normalise_evidence(omission_evidence_ids)
    anchors, expected = _build(cells, n_rows=rows, n_cols=cols)
    provided = _normalise_slots(slots, n_rows=rows, n_cols=cols)

    if len(provided) != rows or any(len(row) != cols for row in provided):
        shape = f"{len(provided)} rows / {[len(row) for row in provided]} columns"
        raise _span_error(
            f"slot matrix shape mismatch: grid is {rows}x{cols}, provided matrix is "
            f"{shape}",
            cell_id=None,
            row=0,
            col=0,
            n_rows=rows,
            n_cols=cols,
        )

    difference = _first_difference(provided, expected)
    if difference is not None:
        r, c = difference
        raise _span_error(
            f"slot-to-cell map mismatch at (row={r}, col={c}): provided "
            f"{provided[r][c]!r}, recomputed from anchors {expected[r][c]!r}",
            cell_id=provided[r][c],
            row=r,
            col=c,
            provided=provided[r][c],
            expected=expected[r][c],
        )

    holes = _holes(expected)
    declared = _normalise_omitted(omitted_slots, n_rows=rows, n_cols=cols)
    hole_set = set(holes)
    declared_set = set(declared)
    missing = sorted(hole_set - declared_set)
    if missing:
        r, c = missing[0]
        raise _span_error(
            f"unexplained hole at (row={r}, col={c}): the position has no anchor and is "
            "absent from omitted_slots; a scanner miss must not be re-labelled as an "
            "omission",
            cell_id=None,
            row=r,
            col=c,
            holes=[list(pair) for pair in holes],
            omitted_slots=[list(pair) for pair in declared],
        )
    surplus = sorted(declared_set - hole_set)
    if surplus:
        r, c = surplus[0]
        raise _span_error(
            f"omitted slot (row={r}, col={c}) is declared but the position is covered "
            f"by cell {expected[r][c]!r}; a declared omission may not contradict the "
            "anchor grid",
            cell_id=expected[r][c],
            row=r,
            col=c,
            omitted_slots=[list(pair) for pair in declared],
        )

    if holes and not evidence:
        raise _span_error(
            f"grid has {len(holes)} omitted slot(s) starting at "
            f"(row={holes[0][0]}, col={holes[0][1]}) but omission_evidence_ids is "
            "empty; an omitted position requires source evidence",
            cell_id=None,
            row=holes[0][0],
            col=holes[0][1],
            holes=[list(pair) for pair in holes],
        )

    return GridResult(
        n_rows=rows,
        n_cols=cols,
        slots=expected,
        omitted_slots=holes,
        anchor_cells=anchors,
    )


def to_contract_table(
    table_id: str,
    cells: Sequence[Mapping],
    *,
    parent_cell_id: str | None = None,
    ir_ref: str | None = None,
    omission_evidence_ids: Sequence[str] = (),
) -> dict:
    """Turn anchors into a contract ``Table`` dict ready for ``validate_root``.

    ``n_rows``/``n_cols`` are derived from the outermost anchor edge, so the
    emitted grid is exactly as large as the anchors require — no trailing empty
    rows are invented. Semantic enrichment (headers, notes, continuation
    decisions) is deliberately left empty: it belongs to a later task and must
    not be guessed here. Structure is emitted as ``VALID_CANDIDATE``, never
    ``CONFIRMED``, because no review has happened at this point.
    """
    if not isinstance(table_id, str) or not table_id:
        raise _input_error(
            f"table_id must be a non-empty string, got {table_id!r}"
        )
    if parent_cell_id is not None and (
        not isinstance(parent_cell_id, str) or not parent_cell_id
    ):
        raise _input_error(
            f"parent_cell_id must be a non-empty string or null, got {parent_cell_id!r}"
        )
    if ir_ref is not None and not isinstance(ir_ref, str):
        raise _input_error(f"ir_ref must be a string or null, got {ir_ref!r}")

    if isinstance(cells, (str, bytes)) or not isinstance(cells, Sequence):
        raise _input_error(
            f"cells must be a sequence of anchor mappings, got {type(cells).__name__}"
        )
    raw_anchors = list(cells)

    n_rows = 0
    n_cols = 0
    for index, cell in enumerate(raw_anchors):
        raw = _anchor_is_a_mapping(index, cell)
        row = raw.get("row")
        col = raw.get("col")
        rowspan = raw.get("rowspan")
        colspan = raw.get("colspan")
        if not _is_int(row) or not _is_int(col):
            raise _input_error(
                f"cells[{index}] row/col must be integers; cannot size the grid "
                "from a non-integer anchor"
            )
        if not _is_int(rowspan) or not _is_int(colspan):
            # Non-integer spans are a span fault, not a sizing fault.
            raise _span_error(
                f"cells[{index}] cell {raw.get('cell_id')!r} at (row={row}, col={col}) "
                f"has non-integer rowspan={rowspan!r}/colspan={colspan!r}; spans must "
                "be positive integers and are never repaired to 1",
                cell_id=raw.get("cell_id"),
                row=row,
                col=col,
            )
        if row >= 0 and rowspan >= 0:
            n_rows = max(n_rows, row + rowspan)
        if col >= 0 and colspan >= 0:
            n_cols = max(n_cols, col + colspan)

    evidence = list(omission_evidence_ids or ())

    # Holes are derived from the anchors themselves, never taken from the
    # caller: an anchor grid that leaves a position empty is the only source of
    # `omitted_slots`, and `validate_grid` still requires evidence for them.
    _, matrix = _build(raw_anchors, n_rows=n_rows, n_cols=n_cols)
    holes = _holes(matrix)
    validated = validate_grid(
        cells=raw_anchors,
        n_rows=n_rows,
        n_cols=n_cols,
        slots=matrix,
        omitted_slots=holes,
        omission_evidence_ids=evidence,
    )

    return {
        "table_id": table_id,
        "ir_ref": ir_ref,
        "parent_cell_id": parent_cell_id,
        "n_rows": validated.n_rows,
        "n_cols": validated.n_cols,
        "cells": list(validated.anchor_cells),
        "slots": [list(row) for row in validated.slots],
        "omitted_slots": list(validated.omitted_slots),
        "omission_evidence_ids": evidence,
        "cell_semantics": [],
        "notes": [],
        "source_html_ref": None,
        "structure_state": "VALID_CANDIDATE",
        "review_decision_ref": None,
    }
