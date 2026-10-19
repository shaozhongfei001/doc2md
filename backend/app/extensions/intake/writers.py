"""Serialise a :class:`Document` to JSON and Markdown.

The Markdown is a *view*; the JSON is the record. Tables are rendered from the
grid rather than passed through the engine's HTML so that a DOCX table and an
OCR table look the same to a reader.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from .model import Document


def table_to_markdown(table: dict[str, Any]) -> str:
    n_rows, n_cols = int(table["n_rows"]), int(table["n_cols"])
    if n_rows <= 0 or n_cols <= 0:
        return ""
    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for cell in table["cells"]:
        row, col = int(cell["row"]), int(cell["col"])
        if 0 <= row < n_rows and 0 <= col < n_cols:
            grid[row][col] = str(cell.get("text", "")).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * n_cols]
    lines.extend("| " + " | ".join(row) + " |" for row in grid[1:])
    return "\n".join(lines)


def to_markdown(doc: Document) -> str:
    mapping = doc.to_mapping()
    parts = [f"# {pathlib.Path(doc.path).name}", ""]
    parts.append(
        f"- kind: `{doc.kind}`  engine: `{doc.engine}`  "
        f"size: {doc.size}  pages: {doc.page_count}"
    )
    parts.append(f"- sha256: `{doc.sha256}`")
    if doc.issues:
        parts.append("")
        parts.append("## Issues")
        for issue in doc.issues:
            parts.append(f"- **{issue.code}** ({issue.severity}): {issue.message}")
    parts.append("")

    table_by_position = {
        index: table.to_mapping() for index, table in enumerate(doc.tables)
    }
    table_cursor = 0
    for block in mapping["blocks"]:
        if block["kind"] == "heading":
            parts.append("#" * min(int(block.get("level") or 2), 6) + " " + block["text"])
        elif block["kind"] == "table":
            if table_cursor in table_by_position:
                parts.append(table_to_markdown(table_by_position[table_cursor]))
                table_cursor += 1
        elif block["kind"] == "image":
            parts.append(f"<!-- image: {block.get('text', '')[:80]} -->")
        else:
            parts.append(block.get("text", ""))
        parts.append("")

    while table_cursor < len(doc.tables):
        parts.append(table_to_markdown(doc.tables[table_cursor].to_mapping()))
        parts.append("")
        table_cursor += 1
    return "\n".join(parts).rstrip() + "\n"


def write_document(doc: Document, out_root: str | pathlib.Path) -> dict[str, str]:
    directory = pathlib.Path(out_root) / doc.sha256[:16]
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "document.json"
    md_path = directory / "document.md"
    json_path.write_text(
        json.dumps(doc.to_mapping(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    md_path.write_text(to_markdown(doc), encoding="utf-8")
    return {"document_json": str(json_path), "document_md": str(md_path)}
