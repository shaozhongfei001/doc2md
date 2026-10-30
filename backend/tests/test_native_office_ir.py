"""原生 Office 结构 → IR：表格矩阵、合并格、公式与正文定位。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.document_ir_schema import validate_document_ir
from app.services.native_office_ir import _assemble, _xlsx_tables, build_from_native_xlsx

SHA = "a" * 64


def _payload(raw_tables, text):
    ir = _assemble(text=text, raw_tables=raw_tables, sha256=SHA, content_uid=SHA,
                   source_vault_path=None, adapter_version="test", page_count=None)
    payload = ir.to_dict()
    validate_document_ir(payload, parsed_text=text)
    return payload


def _table(cells, n_rows, n_cols):
    return {"n_rows": n_rows, "n_cols": n_cols,
            "cells": [{"row": r, "col": c, "text": t, "rowspan": 1, "colspan": 1}
                      for (r, c, t) in cells]}


def test_table_matrix_enters_ir_and_pipe_lines_are_not_duplicated():
    text = "# 标题\n\n正文。\n\n| 指标 | 值 |\n| --- | --- |\n| 日均存款 | 100 |\n"
    raw = [_table([(0, 0, "指标"), (0, 1, "值"), (1, 0, "日均存款"), (1, 1, "100")], 2, 2)]
    payload = _payload(raw, text)

    assert payload["provenance"]["ir_source"] == "native_office_tables"
    assert len(payload["tables"]) == 1
    assert payload["tables"][0]["n_rows"] == 2 and payload["tables"][0]["n_cols"] == 2
    assert [c["text"] for c in payload["tables"][0]["cells"]] == ["指标", "值", "日均存款", "100"]
    assert payload["tables"][0]["cells"][0]["is_header"] is True
    # Markdown 表格行不再作为正文块重复出现
    kinds = [b["content_ref"]["kind"] for b in payload["blocks"]]
    assert kinds.count("table_ref") == 1
    assert "| 指标 |" not in str([b for b in payload["blocks"]
                                 if b["content_ref"]["kind"] == "text_span"])


def test_table_block_follows_the_paragraph_it_belongs_to():
    text = "前言。\n\n| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |\n\n后记。\n"
    raw = [_table([(0, 0, "甲"), (0, 1, "乙"), (1, 0, "1"), (1, 1, "2")], 2, 2)]
    blocks = _payload(raw, text)["blocks"]
    assert blocks[0]["type"] == "paragraph"
    assert blocks[1]["type"] == "table"
    assert blocks[-1]["type"] == "paragraph"
    assert blocks[-1]["content_ref"]["text"].strip() == "后记。"


def test_text_span_numbers_are_exact_slices():
    text = "甲段。\n\n乙段。\n"
    blocks = _payload([], text)["blocks"]
    assert blocks
    for block in blocks:
        ref = block["content_ref"]
        if ref["kind"] == "text_span":
            assert text[ref["start_offset"]:ref["end_offset"]] == ref["text"]


def test_xlsx_merged_cells_and_formula_without_cache(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "汇总"
    ws.merge_cells("A1:C1")          # 1×3 合并 → colspan=3
    ws["A2"], ws["B2"], ws["C2"] = "客户", "额度", "余额"
    ws["A3"], ws["B3"], ws["C3"] = "甲", 100, "=B3*2"
    wb.save(path)

    tables, extra = _xlsx_tables(path)
    assert len(tables) == 1
    table = tables[0]
    assert (table["n_rows"], table["n_cols"]) == (3, 3)
    anchor = next(c for c in table["cells"] if (c["row"], c["col"]) == (0, 0))
    assert anchor["colspan"] == 3
    # 合并区域里的非锚点位置不得作为独立 cell 出现（否则矩阵重叠）
    assert not any((c["row"], c["col"]) in {(0, 1), (0, 2)} for c in table["cells"])
    formula_cell = next(c for c in table["cells"] if (c["row"], c["col"]) == (2, 2))
    assert formula_cell["text"] == "=B3*2"   # 缓存缺失：保留公式，不猜算
    assert extra["formulas_without_cache"] == 1


def test_xlsx_ir_passes_contract(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "b.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["B1"] = "指标", "值"
    ws["A2"], ws["B2"] = "日均存款", 100
    wb.save(path)

    ir = build_from_native_xlsx(path=path, text="指标与数值。\n", sha256=SHA,
                                content_uid=SHA, adapter_version="test")
    payload = ir.to_dict()
    validate_document_ir(payload, parsed_text="指标与数值。\n")
    assert payload["tables"][0]["n_rows"] == 2
    assert payload["provenance"]["ir_source"] == "native_office_tables"
