"""原生 Office（DOCX / XLSX）结构 → Document IR。

为什么需要（实测结论）
----------------------
markitdown 只能给"文本降级 IR"：block 全是 ``inline_text``，表格矩阵、合并格、表头全部丢失。
但 DOCX/XLSX 的表格结构本来就在文件里（OOXML ``gridSpan``/``vMerge``、xlsx 合并区域），
不需要等 OCR 或 ``magic_pdf``。

本模块只做**结构映射**：
- 正文仍以 markitdown 产出的 ``parsed_text.md`` 为坐标基准，``text_span`` 必须是真实切片；
- 表格矩阵来自原生读取器（``extensions.word.native_tables`` / ``openpyxl``），
  以 ``table_ref`` 进入 IR，不塞进 ``cells`` 之外的字段，也不改旧 IR 合同；
- 不重写 MD、不猜偏移：定位不到的内容保持 ``inline_text``，不伪造精确引用。

公式与缓存
----------
xlsx 用 ``data_only=False`` 取公式、``data_only=True`` 取缓存值：有缓存用缓存，没有就如实
保留公式文本（如 ``=SUM(B2:B3)``），绝不猜算；缺缓存的数量写进 provenance。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from app.extensions.word.final_view import build_final_view
from app.extensions.word.native_tables import extract_tables
from app.extensions.word.package import open_docx
from app.services.document_ir_builder import ADAPTER_VERSION, PARSER_NAME_MARKITDOWN
from app.services.document_ir_schema import (
    Block,
    ContentRef,
    CoordinateSystem,
    DocumentIr,
    DocumentStats,
    IrTable,
    TableCell,
    derive_stats,
)

IR_SOURCE = "native_office_tables"
PROFILE = "native_office_tables_v1"
_PIPE_LINE = re.compile(r"^\s*\|")
_HEADING = re.compile(r"^(#{1,6})\s+")


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """按空行切分段落，返回 MD 码点区间（左闭右开）。"""
    spans: list[tuple[int, int]] = []
    pos, n = 0, len(text)
    while pos < n:
        while pos < n and text[pos] in "\r\n":
            pos += 1
        if pos >= n:
            break
        end = text.find("\n\n", pos)
        end = n if end == -1 else end
        spans.append((pos, end))
        pos = end
    return spans


def _table_cells(raw: dict[str, Any], n_rows: int) -> tuple[TableCell, ...]:
    cells = []
    for cell in raw.get("cells") or []:
        cells.append(
            TableCell(
                row=int(cell["row"]),
                col=int(cell["col"]),
                text=str(cell.get("text") or ""),
                rowspan=int(cell.get("rowspan") or 1),
                colspan=int(cell.get("colspan") or 1),
                # 原生读取器目前不给 tblHeader；只在多行表里把首行标表头，且不据此丢数据
                is_header=bool(cell["row"] == 0 and n_rows > 1),
            )
        )
    return tuple(cells)


def _signature(first_row: Iterable[str]) -> str:
    return "| " + " | ".join(first_row) + " |"


def _first_row_texts(raw: dict[str, Any]) -> list[str]:
    row0 = [c for c in raw.get("cells") or [] if int(c["row"]) == 0]
    row0.sort(key=lambda c: int(c["col"]))
    return [str(c.get("text") or "") for c in row0]


def _locate_table(text: str, raw: dict[str, Any], cursor: int) -> int | None:
    """在 MD 里定位这张表的渲染位置；找不到返回 None（不猜）。"""
    texts = _first_row_texts(raw)
    for candidate in (_signature(texts), " | ".join(texts)):
        if not candidate.strip(" |"):
            continue
        found = text.find(candidate, cursor)
        if found != -1:
            return found
    first = next((t for t in texts if t.strip()), "")
    if first:
        found = text.find(first, cursor)
        if found != -1:
            return found
    return None


def _assemble(
    *,
    text: str,
    raw_tables: list[dict[str, Any]],
    sha256: str,
    content_uid: str,
    source_vault_path: str | None,
    adapter_version: str,
    page_count: int | None,
    extra_provenance: dict[str, Any] | None = None,
) -> DocumentIr:
    """正文块 + 表格块按 MD 位置合并，统一编号。"""
    entries: list[tuple[int, str, Any]] = []
    cursor = 0
    table_spans: list[tuple[int, int]] = []
    for index, raw in enumerate(raw_tables):
        pos = _locate_table(text, raw, cursor)
        slot = len(text) + index if pos is None else pos
        if pos is not None:
            cursor = pos
            table_spans.append((pos, pos + 1))
            entries.append((slot, "table", raw))
        else:
            entries.append((slot, "table", raw))

    for start, end in _paragraph_spans(text):
        body = text[start:end]
        if _PIPE_LINE.match(body):
            continue  # 该段是 Markdown 表格行，结构已在 table 块里
        if any(s <= start < e for s, e in table_spans):
            continue
        entries.append((start, "paragraph", (start, end)))

    entries.sort(key=lambda item: item[0])

    blocks: list[Block] = []
    tables: list[IrTable] = []
    for order, (_pos, kind, payload) in enumerate(entries):
        block_id = f"b{order + 1:04d}"
        if kind == "table":
            raw = payload
            n_rows, n_cols = int(raw["n_rows"]), int(raw["n_cols"])
            table_id = f"t{len(tables) + 1:03d}"
            tables.append(
                IrTable(
                    table_id=table_id,
                    block_id=block_id,
                    page_no=None,
                    bbox=None,
                    html=None,
                    n_rows=n_rows,
                    n_cols=n_cols,
                    cells=_table_cells(raw, n_rows),
                    caption_block_id=None,
                    confidence=None,
                )
            )
            blocks.append(
                Block(
                    block_id=block_id,
                    type="table",
                    page_no=None,
                    bbox=None,
                    reading_order=order,
                    confidence=None,
                    content_ref=ContentRef(kind="table_ref", table_id=table_id),
                    attrs={},
                )
            )
            continue

        start, end = payload
        body = text[start:end]
        heading = _HEADING.match(body)
        attrs = {"heading_level": len(heading.group(1))} if heading else {}
        blocks.append(
            Block(
                block_id=block_id,
                type="heading" if heading else "paragraph",
                page_no=None,
                bbox=None,
                reading_order=order,
                confidence=None,
                content_ref=ContentRef(kind="text_span", text=body,
                                       source="parsed_text.md",
                                       start_offset=start, end_offset=end),
                attrs=attrs,
            )
        )

    if not blocks:
        # 空文档：保持空的 blocks/reading_order，stats 全 0（合法空，不编造）
        blocks = []

    stats_dict = derive_stats([b.to_dict() for b in blocks],
                              [t.to_dict() for t in tables], [])
    provenance = {
        "parser_name": PARSER_NAME_MARKITDOWN,
        "parser_profile": PROFILE,
        "parser_adapter_version": adapter_version,
        "ir_source": IR_SOURCE,
        "generated_at": _now(),
    }
    if extra_provenance:
        provenance.update(extra_provenance)
    return DocumentIr(
        content_uid=content_uid,
        sha256=sha256,
        blocks=tuple(blocks),
        stats=DocumentStats(
            block_count=stats_dict["block_count"],
            table_count=stats_dict["table_count"],
            figure_count=stats_dict["figure_count"],
            blocks_without_bbox=stats_dict["blocks_without_bbox"],
            blocks_low_confidence=stats_dict["blocks_low_confidence"],
        ),
        tables=tuple(tables),
        figures=(),
        coordinate_system=CoordinateSystem(unit="pixel"),
        provenance=provenance,
        page_count=page_count,
        source_vault_path=source_vault_path,
    )


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def build_from_native_docx(
    *,
    path: str | Path,
    text: str,
    sha256: str,
    content_uid: str,
    source_vault_path: str | None = None,
    adapter_version: str = ADAPTER_VERSION,
) -> DocumentIr:
    """DOCX：正文取自 MD（真实偏移），表格矩阵取自 OOXML（gridSpan/vMerge）。"""
    package = open_docx(str(path))
    view = build_final_view(package)
    raw_tables = extract_tables(package, view)
    return _assemble(text=text, raw_tables=raw_tables, sha256=sha256,
                     content_uid=content_uid, source_vault_path=source_vault_path,
                     adapter_version=adapter_version, page_count=None,
                     extra_provenance={"native_tables": len(raw_tables)})


def _xlsx_tables(path: str | Path, suffix: str = ".xlsx") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """读取工作簿。vault 里原件常叫 ``original.bin``，openpyxl 按后缀判格式，
    所以按真实扩展名复制到临时文件再读（不改原件、不改产物字节）。"""
    import os
    import shutil
    import tempfile

    from openpyxl import load_workbook

    suffix = suffix if suffix.startswith(".") else "." + suffix
    if str(path).lower().endswith(suffix.lower()):
        work_path = str(path)
        cleanup = None
    else:
        fd, work_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        shutil.copyfile(str(path), work_path)
        cleanup = work_path
    try:
        formulas = load_workbook(work_path, data_only=False, read_only=False)
        cached = load_workbook(work_path, data_only=True, read_only=False)
    finally:
        if cleanup:
            os.unlink(cleanup)
    tables: list[dict[str, Any]] = []
    skipped: list[str] = []
    missing_cache = 0
    for name in formulas.sheetnames:
        ws, wv = formulas[name], cached[name]
        n_rows, n_cols = int(ws.max_row or 0), int(ws.max_column or 0)
        if n_rows == 0 or n_cols == 0:
            skipped.append(f"{name}:empty")
            continue
        if n_rows * n_cols > 20000:
            # 不截断、不猜：整表跳过并如实登记原因
            skipped.append(f"{name}:too_large({n_rows}x{n_cols})")
            continue
        anchors: dict[tuple[int, int], tuple[int, int]] = {}
        covered: set[tuple[int, int]] = set()
        for rng in ws.merged_cells.ranges:
            anchors[(rng.min_row, rng.min_col)] = (rng.max_row - rng.min_row + 1,
                                                  rng.max_col - rng.min_col + 1)
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    if (r, c) != (rng.min_row, rng.min_col):
                        covered.add((r, c))
        cells: list[dict[str, Any]] = []
        for r in range(1, n_rows + 1):
            for c in range(1, n_cols + 1):
                if (r, c) in covered:
                    continue
                value = ws.cell(row=r, column=c).value
                if isinstance(value, str) and value.startswith("="):
                    cached_value = wv.cell(row=r, column=c).value
                    if cached_value is None:
                        missing_cache += 1
                        text_value = value          # 如实保留公式，不猜结果
                    else:
                        text_value = str(cached_value)
                else:
                    text_value = "" if value is None else str(value)
                rowspan, colspan = anchors.get((r, c), (1, 1))
                cells.append({
                    "row": r - 1, "col": c - 1, "rowspan": rowspan, "colspan": colspan,
                    "text": text_value,
                })
        tables.append({"n_rows": n_rows, "n_cols": n_cols, "cells": cells, "sheet": name})
    return tables, {"skipped_sheets": skipped,
                    "formulas_without_cache": missing_cache}


def build_from_native_xlsx(
    *,
    path: str | Path,
    text: str,
    sha256: str,
    content_uid: str,
    source_vault_path: str | None = None,
    adapter_version: str = ADAPTER_VERSION,
    suffix: str = ".xlsx",
) -> DocumentIr:
    """XLSX：每个 sheet 一张 IR 表（合并区域→rowspan/colspan；公式不猜算）。"""
    raw_tables, extra = _xlsx_tables(path, suffix=suffix)
    if not raw_tables:
        raise ValueError("xlsx: no non-empty sheet")
    return _assemble(text=text, raw_tables=raw_tables, sha256=sha256,
                     content_uid=content_uid, source_vault_path=source_vault_path,
                     adapter_version=adapter_version, page_count=None,
                     extra_provenance=extra)
