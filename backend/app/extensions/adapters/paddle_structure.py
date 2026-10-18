"""PP-StructureV3 response adapter (SDD 8.1, ``PI-F04-02``).

The engine is **not** called here and no model is loaded: this module turns an
already-captured response into project objects, and refuses when the response
does not look like the locked version.

Three rules from SDD 8.1 are enforced rather than assumed:

* *"字段缺失、引擎升级或类型变化返回 E_ENGINE_SCHEMA_DRIFT，不静默兜成空表"* —
  a missing ``pred_html`` or a ``table_ocr_pred`` whose three arrays disagree in
  length is a drift, never an empty table.
* *"表格box和OCR框不能仅靠两个列表下标相同就对齐"* — no index zipping anywhere.
  Token/cell association is only reported when geometry proves it, and the
  HTML-cell ↔ ``cell_box_list`` association is reported as ``UNAVAILABLE`` unless
  the caller states the locked version's documented order, in which case that
  reliance is written into the output.
* *"版本固定后用golden raw response确认名字、空值和单位"* — nothing here has been
  confirmed against a real response yet; ``PADDLE_LOCK_NOTE`` records that.

Error codes stay inside the fixed table: ``E_ENGINE_SCHEMA_DRIFT`` (dependency),
``E_INPUT_INVALID``. No new codes are minted, because adding one would require
editing ``app/extensions/__init__.py``, which is outside this Loop's whitelist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError
from ..tables.ocr_alignment import align_tokens

#: Written into every output: this adapter is **not** validated against a live engine.
PADDLE_LOCK_NOTE = (
    "field names follow the official PP-StructureV3 description for the pinned "
    "PaddleOCR 3.3.3 line and have NOT been confirmed against a golden raw response "
    "in this repository; treat any binding as unverified until L04/T405 runs"
)

__all__ = [
    "PADDLE_LOCK_NOTE",
    "PaddleTable",
    "PaddleTableResult",
    "normalise_response",
]


def _drift(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_ENGINE_SCHEMA_DRIFT", message, detail=detail or None)


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class PaddleTable:
    """One recognised table: raw HTML kept, cells parsed, tokens unbound by default."""

    table_index: int
    pred_html: str
    cells: list[dict[str, Any]]
    tokens: list[dict[str, Any]]
    box_association: str
    binding: list[dict[str, Any]] | None = None
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_index": self.table_index,
            "pred_html": self.pred_html,
            "cells": [dict(cell) for cell in self.cells],
            "tokens": [dict(token) for token in self.tokens],
            "box_association": self.box_association,
            "binding": None if self.binding is None else [dict(b) for b in self.binding],
            "issues": list(self.issues),
            "engine_lock_note": PADDLE_LOCK_NOTE,
        }


@dataclass(frozen=True)
class PaddleTableResult:
    tables: list[PaddleTable] = field(default_factory=list)
    page_no: int = 1
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "tables": [table.to_dict() for table in self.tables],
            "issues": [dict(issue) for issue in self.issues],
            "engine_lock_note": PADDLE_LOCK_NOTE,
        }


def _polygon(value: Any, *, where: str) -> list[list[float]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) < 4:
        raise _drift(f"{where} must be four points, got {value!r}")
    points: list[list[float]] = []
    for point in value[:4]:
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
            raise _drift(f"{where} contains a non-point {point!r}")
        x, y = point
        for coordinate in (x, y):
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise _drift(f"{where} contains a non-numeric coordinate {coordinate!r}")
        points.append([float(x), float(y)])
    return points


def _html_cells(pred_html: str) -> list[dict[str, Any]]:
    """Parse ``td``/``th`` cells with ``rowspan``/``colspan`` from ``pred_html``.

    Only the minimum needed to give a cell a stable identity is extracted. The
    full grid validation belongs to ``tables.grid``; duplicating it here would
    create a second authority for the same rule.
    """
    import re

    cells: list[dict[str, Any]] = []
    row_index = -1
    for raw_row in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", pred_html, flags=re.S | re.I):
        row_index += 1
        column = 0
        for match in re.finditer(r"<(td|th)\b([^>]*)>(.*?)</\1>", raw_row.group(1), flags=re.S | re.I):
            attributes = match.group(2)
            text = re.sub(r"<[^>]+>", "", match.group(3))
            text = " ".join(text.replace("&nbsp;", " ").split())
            colspan = _span(attributes, "colspan")
            rowspan = _span(attributes, "rowspan")
            cells.append(
                {
                    "cell_id": f"P0-R{row_index:04d}C{column:04d}",
                    "row": row_index,
                    "col": column,
                    "rowspan": rowspan,
                    "colspan": colspan,
                    "text": text,
                    "text_origin": "ENGINE_OCR",
                }
            )
            column += colspan
    return cells


def _span(attributes: str, name: str) -> int:
    import re

    match = re.search(rf"""{name}\s*=\s*["']?(\d+)["']?""", attributes, flags=re.I)
    if match is None:
        return 1
    value = int(match.group(1))
    if value <= 0:
        raise _drift(f"{name} must be a positive integer, got {value}")
    return value


def normalise_response(
    response: Mapping[str, Any],
    *,
    page_no: int = 1,
    cell_box_order: str = "UNAVAILABLE",
) -> PaddleTableResult:
    """Turn one PP-StructureV3 page response into :class:`PaddleTable`.

    ``cell_box_order`` states what we know about ``cell_box_list`` ordering:
    ``"UNAVAILABLE"`` (default) means the boxes are carried but **not** attached
    to HTML cells; ``"DOCUMENTED"`` means the pinned engine documents that
    ``cell_box_list`` follows HTML cell order, and every table then records that
    it relies on the documentation rather than on geometry.
    """
    if not isinstance(response, Mapping):
        raise _input_error(f"response must be a mapping, got {type(response).__name__}")
    if isinstance(page_no, bool) or not isinstance(page_no, int) or page_no < 1:
        raise _input_error(f"page_no must be a 1-based integer, got {page_no!r}")
    if cell_box_order not in ("UNAVAILABLE", "DOCUMENTED"):
        raise _input_error(
            f"cell_box_order must be 'UNAVAILABLE' or 'DOCUMENTED', got {cell_box_order!r}"
        )

    tables_raw = response.get("table_res_list")
    if tables_raw is None:
        raise _drift(
            "response has no 'table_res_list'; an empty result and a renamed field look "
            "identical, so the field is required"
        )
    if isinstance(tables_raw, (str, bytes)) or not isinstance(tables_raw, Sequence):
        raise _drift(
            f"'table_res_list' must be a list, got {type(tables_raw).__name__}"
        )

    tables: list[PaddleTable] = []
    issues: list[dict[str, Any]] = []

    for table_index, raw in enumerate(tables_raw):
        if not isinstance(raw, Mapping):
            raise _drift(f"table_res_list[{table_index}] must be an object")

        pred_html = raw.get("pred_html")
        if not isinstance(pred_html, str) or not pred_html.strip():
            raise _drift(
                f"table_res_list[{table_index}] has no usable pred_html; a table without "
                "structure is a drift, not an empty table"
            )

        cells = _html_cells(pred_html)
        if not cells:
            issues.append(
                {
                    "code": "EMPTY_TABLE_HTML",
                    "table_index": table_index,
                    "message": "pred_html parsed to zero cells; kept as an issue, not dropped",
                }
            )

        boxes = raw.get("cell_box_list")
        box_polygons: list[list[list[float]]] = []
        if boxes is not None:
            if isinstance(boxes, (str, bytes)) or not isinstance(boxes, Sequence):
                raise _drift(f"table_res_list[{table_index}].cell_box_list must be a list")
            box_polygons = [
                _polygon(box, where=f"cell_box_list[{position}]")
                for position, box in enumerate(boxes)
            ]

        tokens = _tokens(raw, table_index=table_index)

        association = "UNAVAILABLE"
        binding: list[dict[str, Any]] | None = None
        if box_polygons and cells:
            if cell_box_order == "DOCUMENTED" and len(box_polygons) == len(cells):
                association = "DOCUMENTED_ORDER"
                for cell, polygon in zip(cells, box_polygons):
                    cell["polygon"] = polygon
            elif cell_box_order == "DOCUMENTED":
                association = "DOCUMENTED_COUNT_MISMATCH"
                issues.append(
                    {
                        "code": "CELL_BOX_COUNT_MISMATCH",
                        "table_index": table_index,
                        "message": (
                            f"{len(cells)} html cells vs {len(box_polygons)} boxes; the "
                            "documented order cannot be applied and geometry has no cell "
                            "polygons to fall back on"
                        ),
                    }
                )
            else:
                association = "UNAVAILABLE"
                issues.append(
                    {
                        "code": "CELL_BOX_ORDER_UNVERIFIED",
                        "table_index": table_index,
                        "message": (
                            "cell_box_list is carried but not attached to html cells: list "
                            "order is not evidence of identity"
                        ),
                    }
                )

            if tokens and all("polygon" in cell for cell in cells):
                aligned = align_tokens(
                    [
                        {
                            "cell_id": cell["cell_id"],
                            "polygon": cell["polygon"],
                            "text": cell.get("text") or None,
                        }
                        for cell in cells
                    ],
                    [
                        {
                            "token_id": token["token_id"],
                            "text": token["text"],
                            "polygon": token["polygon"],
                            "confidence": token["confidence"],
                        }
                        for token in tokens
                    ],
                )
                binding = [
                    {**update, "state": item["state"]}
                    for update, item in zip(aligned.to_cell_updates(), aligned.cells)
                ]
                issues.extend(
                    {
                        "code": issue.code,
                        "table_index": table_index,
                        "message": issue.message,
                    }
                    for issue in aligned.issues
                )

        tables.append(
            PaddleTable(
                table_index=table_index,
                pred_html=pred_html,
                cells=cells,
                tokens=tokens,
                box_association=association,
                binding=binding,
                issues=tuple(issue["code"] for issue in issues if issue.get("table_index") == table_index),
            )
        )

    return PaddleTableResult(tables=tables, page_no=page_no, issues=issues)


def _tokens(raw: Mapping[str, Any], *, table_index: int) -> list[dict[str, Any]]:
    """Read ``table_ocr_pred`` — three parallel arrays that must agree in length."""
    ocr = raw.get("table_ocr_pred")
    if ocr is None:
        return []
    if not isinstance(ocr, Mapping):
        raise _drift(
            f"table_res_list[{table_index}].table_ocr_pred must be an object, got "
            f"{type(ocr).__name__}"
        )
    polygons = ocr.get("rec_polys")
    texts = ocr.get("rec_texts")
    scores = ocr.get("rec_scores")
    for name, value in (("rec_polys", polygons), ("rec_texts", texts), ("rec_scores", scores)):
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise _drift(
                f"table_res_list[{table_index}].table_ocr_pred.{name} must be a list, got "
                f"{type(value).__name__}"
            )
    if not (len(polygons) == len(texts) == len(scores)):
        raise _drift(
            f"table_res_list[{table_index}].table_ocr_pred arrays disagree in length "
            f"({len(polygons)}/{len(texts)}/{len(scores)}); truncating to the shortest "
            "would silently drop recognised text"
        )

    tokens: list[dict[str, Any]] = []
    for position, (polygon, text, score) in enumerate(zip(polygons, texts, scores)):
        if not isinstance(text, str) or not text:
            raise _drift(
                f"table_ocr_pred.rec_texts[{position}] must be a non-empty string, got {text!r}"
            )
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise _drift(
                f"table_ocr_pred.rec_scores[{position}] must be numeric, got {score!r}"
            )
        if not 0.0 <= float(score) <= 1.0:
            raise _drift(
                f"table_ocr_pred.rec_scores[{position}] is {score!r}, outside [0, 1]"
            )
        tokens.append(
            {
                "token_id": f"T{table_index}-K{position:04d}",
                "text": text,
                "polygon": _polygon(polygon, where=f"rec_polys[{position}]"),
                "confidence": float(score),
            }
        )
    return tokens
