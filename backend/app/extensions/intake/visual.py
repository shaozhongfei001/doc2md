"""Turn a raw PP-StructureV3 page result into the same blocks/tables as a text reader.

The conversion is split from the engine call for one reason: the geometry check
already lives in ``adapters.paddle_structure.normalise_response`` and is the only
place that knows both ``cell_box_list`` shapes. Re-implementing cell pairing here
would give the batch path a second, weaker opinion about the same table.
"""

from __future__ import annotations

from typing import Any

from app.extensions import ExtensionError
from app.extensions.adapters.paddle_structure import normalise_response

_HEADING_LABELS = frozenset({"doc_title", "paragraph_title", "title", "figure_title"})
_IMAGE_LABELS = frozenset({"image", "figure", "chart", "seal", "header_image"})


def normalise_page(res: dict[str, Any], *, page_no: int = 1) -> dict[str, Any]:
    """Return ``{page_index, blocks, tables, issues}`` for one engine page."""
    blocks: list[dict[str, Any]] = []
    for item in res.get("parsing_res_list") or []:
        label = str(item.get("block_label") or "")
        if label == "table":
            continue  # carried by table_res_list, with geometry
        text = str(item.get("block_content") or "").strip()
        if not text and label not in _IMAGE_LABELS:
            continue
        if label in _HEADING_LABELS:
            kind = "heading"
        elif label in _IMAGE_LABELS:
            kind = "image"
        else:
            kind = "paragraph"
        blocks.append(
            {
                "kind": kind,
                "text": text,
                "bbox": item.get("block_bbox"),
                "label": label,
            }
        )

    tables: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    raw_tables = res.get("table_res_list") or []
    if raw_tables:
        try:
            result = normalise_response(
                {
                    "width": res.get("width"),
                    "height": res.get("height"),
                    "table_res_list": raw_tables,
                },
                page_no=page_no,
                cell_box_order="GEOMETRY",
            )
        except ExtensionError as exc:
            issues.append(
                {
                    "code": exc.code,
                    "message": exc.message,
                    "severity": "WARNING",
                    "detail": exc.detail if isinstance(exc.detail, dict) else None,
                }
            )
        else:
            issues.extend(result.issues)
            if not any(raw.get("cell_box_list") for raw in raw_tables):
                # Under GEOMETRY the adapter leaves the association UNAVAILABLE and
                # says nothing; silence here would read as "bound by default".
                issues.append(
                    {
                        "code": "CELL_BOX_GEOMETRY_MISSING",
                        "message": "engine returned no cell boxes; grid kept, geometry not bound",
                        "severity": "WARNING",
                    }
                )
            for table in result.tables:
                cells = table.cells
                n_rows = max(
                    (int(cell["row"]) + int(cell["rowspan"]) for cell in cells), default=0
                )
                n_cols = max(
                    (int(cell["col"]) + int(cell["colspan"]) for cell in cells), default=0
                )
                tables.append(
                    {
                        "n_rows": n_rows,
                        "n_cols": n_cols,
                        "cells": cells,
                        "html": table.pred_html,
                        "box_association": table.box_association,
                    }
                )

    return {
        "page_index": page_no - 1,
        "blocks": blocks,
        "tables": tables,
        "issues": issues,
    }
