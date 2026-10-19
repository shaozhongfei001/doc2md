"""Header rows for OCR tables: the engine suggests, a human decides (Owner 2026-10-19).

The native ``.docx`` path reads ``w:tblHeader`` — a declaration, not an inference. OCR
tables have no such markup, and the project's rule since L05 has been **do not guess the
header band**. So the two roles are separated here:

* **suggestion** — taken only from the engine's own ``<th>`` markup. That is the engine
  asserting "these cells are headers", not us deciding that bold or "first row" looks
  like a header. When there is no ``<th>``, the suggestion is **empty**: no answer is
  more honest than a guess.
* **declaration** — a human (with a review decision) says which rows are headers. Only a
  declaration makes the table usable; the suggestion cannot stand in for one.

A disagreement is allowed and is recorded: the human may correct the engine, and the
audit keeps both so a later reader can see what was overridden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError
from ..review.authority import ReviewerPolicy
from ..tables.grid import to_contract_table

__all__ = [
    "HeaderSuggestion",
    "HeaderDeclaration",
    "OcrTable",
    "suggest_header_rows",
    "declare_header_rows",
    "build_ocr_table",
]

NATIVE = "ENGINE_TH_MARKUP"
HUMAN = "HUMAN_DECLARATION"


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _review_required(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_REVIEW_REQUIRED", message, detail=detail or None)


@dataclass(frozen=True)
class HeaderSuggestion:
    rows: tuple[int, ...]
    source: str = NATIVE

    @property
    def available(self) -> bool:
        return bool(self.rows)

    def to_mapping(self) -> dict[str, Any]:
        return {"rows": list(self.rows), "source": self.source}


@dataclass(frozen=True)
class HeaderDeclaration:
    rows: tuple[int, ...]
    reviewer: str
    review_decision_ref: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "rows": list(self.rows),
            "reviewer": self.reviewer,
            "review_decision_ref": self.review_decision_ref,
            "source": HUMAN,
        }


@dataclass(frozen=True)
class OcrTable:
    """One OCR table with a decided header, ready for a contract payload."""

    table_id: str
    n_rows: int
    n_cols: int
    cells: list[dict[str, Any]]
    slots: list[list[str | None]]
    header_rows: tuple[int, ...]
    header_source: str
    suggestion: HeaderSuggestion
    overridden: bool
    issues: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "cells": [dict(cell) for cell in self.cells],
            "slots": [list(row) for row in self.slots],
            "header_rows": list(self.header_rows),
            "header_source": self.header_source,
            "header_suggestion": self.suggestion.to_mapping(),
            "header_overridden": self.overridden,
            "issues": [dict(issue) for issue in self.issues],
        }


def suggest_header_rows(cells: Sequence[Mapping[str, Any]]) -> HeaderSuggestion:
    """Rows the **engine** marked as header, contiguous from row 0.

    Returns an empty suggestion when the markup does not support one. A partial ``<th>``
    row is treated as no suggestion: half a header band is not a header band, and
    inventing the rest is exactly the guess this module refuses to make.
    """
    by_row: dict[int, list[bool]] = {}
    for cell in cells:
        row = int(cell["row"])
        span = int(cell.get("rowspan", 1))
        is_header = str(cell.get("html_tag", "")).lower() == "th"
        for offset in range(span):
            by_row.setdefault(row + offset, []).append(is_header)

    rows: list[int] = []
    for row in range(max(by_row) + 1 if by_row else 0):
        flags = by_row.get(row) or []
        if not flags or not all(flags):
            break
        rows.append(row)
    return HeaderSuggestion(rows=tuple(rows))


def declare_header_rows(
    cells: Sequence[Mapping[str, Any]],
    *,
    rows: Sequence[int],
    reviewer: str,
    review_decision_ref: str,
    policy: ReviewerPolicy | None = None,
) -> HeaderDeclaration:
    """A human's header decision. Must be a contiguous prefix.

    Contiguity is not bureaucracy: the grid assumes rows above the header band are
    headers for every column below them. A non-prefix declaration would produce a table
    whose semantics cannot be derived from a band at all.
    """
    if not isinstance(review_decision_ref, str) or not review_decision_ref.strip():
        raise _review_required(
            "declaring header rows changes what every data cell means; it needs a "
            "review decision"
        )
    reviewer = (policy or ReviewerPolicy()).check(reviewer)

    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise _input_error("rows must be a sequence of row indexes")
    ordered = list(rows)
    if not ordered:
        raise _input_error("declaring zero header rows is not a decision; omit the call instead")
    for value in ordered:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _input_error(f"row indexes must be non-negative integers, got {value!r}")
    if ordered != sorted(set(ordered)):
        raise _input_error(f"rows must be strictly increasing, got {ordered!r}")
    if ordered[0] != 0:
        raise _input_error("the header band must start at row 0")
    if ordered != list(range(len(ordered))):
        raise _input_error(
            f"the header band must be contiguous from 0, got {ordered!r}"
        )

    n_rows = max((int(cell["row"]) + int(cell.get("rowspan", 1)) for cell in cells), default=0)
    if len(ordered) >= n_rows:
        raise _input_error(
            f"{len(ordered)} header rows leaves no data rows in a {n_rows}-row table"
        )
    return HeaderDeclaration(
        rows=tuple(ordered),
        reviewer=reviewer,
        review_decision_ref=review_decision_ref.strip(),
    )


def build_ocr_table(
    table_id: str,
    cells: Sequence[Mapping[str, Any]],
    *,
    declaration: HeaderDeclaration | None,
    suggestion: HeaderSuggestion | None = None,
) -> OcrTable:
    """Assemble the contract-shaped table once a human has declared the header."""
    if declaration is None:
        raise _review_required(
            "an OCR table has no native header markup; without a human declaration its "
            "header band is unknown and every data cell's meaning is undefined",
            table_id=table_id,
        )

    suggested = suggestion if suggestion is not None else suggest_header_rows(cells)
    overridden = suggested.rows != declaration.rows

    payload = to_contract_table(table_id, [dict(cell) for cell in cells])
    issues: list[dict[str, Any]] = []
    if overridden:
        # Not an error: the human is allowed to correct the engine. It is, however,
        # something a later reader must be able to see without replaying the session.
        issues.append(
            {
                "code": "HEADER_OVERRIDES_ENGINE",
                "table_id": table_id,
                "message": "the declared header band differs from the engine's <th> markup",
                "suggested": list(suggested.rows),
                "declared": list(declaration.rows),
            }
        )

    return OcrTable(
        table_id=payload["table_id"],
        n_rows=payload["n_rows"],
        n_cols=payload["n_cols"],
        cells=payload["cells"],
        slots=payload["slots"],
        header_rows=declaration.rows,
        header_source=HUMAN,
        suggestion=suggested,
        overridden=overridden,
        issues=tuple(issues),
    )
