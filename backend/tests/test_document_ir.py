"""017 Document IR —— P1 基座（schema 校验器 + fixture 构造器）。

范围（017 tasks.md T002 / T003 / T010 的 `parsed_paths` 部分）：

- fixture 全部**现场构造**：手写 MinerU 形状的 ``content_list.json``（heading /
  paragraph / image(+img_caption) / table(+table_body 含 rowspan/colspan) /
  equation / page header+footer / **未知 type** / **缺 bbox 块** 全覆盖）、
  ``parsed_text.md``、``parse_manifest.json``（含 ``asset_files[].sha256``，
  图片由 Pillow 现场生成），不提交任何二进制 fixture；
- 校验器每条分支「该拒时拒 + 不该拒时放行」成对用例；
- ``core/parsed_paths.py`` 的 017 additive 键（既有键零改动）。

本文件**不**覆盖 builder / renderer / service（P2+）。``content_list`` fixture
只被“fixture 自身完整性”断言消费，供 P2 builder 复用。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from PIL import Image

from app.core.parsed_paths import (
    ParsedArtifactPaths,
    build_parsed_artifact_paths,
    build_parsed_content_dir,
)
from app.services import document_ir_schema as ir_schema
from app.services.document_ir_schema import (
    BLOCK_TYPES,
    CONTENT_REF_KIND_KEYS,
    COORDINATE_UNITS,
    LOW_CONFIDENCE_THRESHOLD,
    SCHEMA_VERSION,
    DocumentIrValidationError,
    derive_stats,
    document_uid_for,
    validate_asset_alignment,
    validate_block_field_ranges,
    validate_block_ids,
    validate_block_required_keys,
    validate_block_types,
    validate_caption_links,
    validate_content_refs,
    validate_coordinate_system,
    validate_document_ir,
    validate_figure_caption_targets,
    validate_figures,
    validate_reading_order,
    validate_stats,
    validate_tables,
    validate_top_level,
)

CONTENT_UID = "cnt_p1_document_ir_fixture"
DOC_SHA256 = "3f" * 32

# rowspan=2 的“项目”格 + colspan=2 的“金额”格 → 3 行 3 列的经典虚格表。
TABLE_HTML = (
    "<table>"
    '<tr><td rowspan="2">项目</td><td colspan="2">金额</td></tr>'
    "<tr><td>Q1</td><td>Q2</td></tr>"
    "<tr><td>存款</td><td>10</td><td>20</td></tr>"
    "</table>"
)

# 表格 cells：只登记 7 个逻辑单元，(0,0) 的 rowspan=2 与 (0,1) 的 colspan=2
# 覆盖的虚格**不得**出现在 cells 中。
TABLE_CELLS: list[dict[str, Any]] = [
    {"row": 0, "col": 0, "rowspan": 2, "colspan": 1, "is_header": True, "text": "项目", "bbox": None},
    {"row": 0, "col": 1, "rowspan": 1, "colspan": 2, "is_header": True, "text": "金额", "bbox": None},
    {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "is_header": False, "text": "Q1", "bbox": None},
    {"row": 1, "col": 2, "rowspan": 1, "colspan": 1, "is_header": False, "text": "Q2", "bbox": None},
    {"row": 2, "col": 0, "rowspan": 1, "colspan": 1, "is_header": False, "text": "存款", "bbox": None},
    {"row": 2, "col": 1, "rowspan": 1, "colspan": 1, "is_header": False, "text": "10", "bbox": None},
    {"row": 2, "col": 2, "rowspan": 1, "colspan": 1, "is_header": False, "text": "20", "bbox": None},
]


# ---------------------------------------------------------------------------
# fixture 构造器（全部现场生成，无二进制入库）
# ---------------------------------------------------------------------------


def build_content_list_fixture() -> list[dict[str, Any]]:
    """MinerU ``content_list.json`` 形状的 fixture。

    ``text_type``/``page_header`` 类标记用于表达 middle.json 才有的页眉页脚区
    （MinerU 各版本差异大，P2 adapter 据此判别），content_list 原生不含该字段。
    """

    return [
        {
            "type": "text",
            "text": "第 1 章 总述",
            "text_level": 1,
            "page_idx": 0,
            "bbox": [72.0, 60.0, 523.0, 92.0],
        },
        {
            "type": "text",
            "text": "本季度营收同比增长 12%。",
            "page_idx": 0,
            "bbox": [72.0, 100.0, 523.0, 128.0],
        },
        {
            "type": "image",
            "img_path": "images/img_p1_1.png",
            "img_caption": ["图 1-1 系统架构"],
            "img_bbox": [100.0, 200.0, 480.0, 420.0],
            "page_idx": 0,
        },
        {
            "type": "table",
            "table_body": TABLE_HTML,
            "table_caption": ["表 1-1 关键指标"],
            "table_bbox": [60.0, 440.0, 540.0, 620.0],
            "page_idx": 1,
        },
        {
            "type": "equation",
            "text": "E = mc^{2}",
            "page_idx": 1,
            "bbox": [200.0, 640.0, 400.0, 668.0],
        },
        {
            "type": "unknown_chart",
            "text": "未知类型块必须归并为 paragraph 并记录 raw_type",
            "page_idx": 2,
            "bbox": [72.0, 700.0, 523.0, 728.0],
        },
        {
            "type": "text",
            "text": "本块没有 bbox，必须置 null 而不是估算填充",
            "page_idx": 2,
        },
        {
            "type": "text",
            "text": "2026 年度报告",
            "page_idx": 0,
            "bbox": [72.0, 20.0, 523.0, 40.0],
            "text_type": "page_header",
        },
        {
            "type": "text",
            "text": "内部资料 请勿外传",
            "page_idx": 1,
            "bbox": [72.0, 800.0, 523.0, 820.0],
            "text_type": "page_footer",
        },
    ]


def find_span(text: str, needle: str) -> tuple[int, int]:
    start = text.index(needle)
    return start, start + len(needle)


def build_parsed_text(content_list: list[dict[str, Any]]) -> str:
    """``parsed_text.md``：把每个带 text 的项按顺序拼成正文（供 span 回引）。"""

    lines = [str(item["text"]) for item in content_list if item.get("text")]
    return "\n\n".join(lines) + "\n"


def write_png_asset(path: Path, *, size: tuple[int, int] = (120, 80)) -> str:
    """现场生成 PNG 资产并返回其 sha256（对齐 parse_manifest.asset_files[]）。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 120, 40)).save(path, format="PNG")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ParsedFixture:
    parsed_dir: Path
    parsed_text: str
    content_list: list[dict[str, Any]]
    manifest: dict[str, Any]
    asset_relative_path: str
    asset_sha256: str


def build_parsed_fixture(root: Path) -> ParsedFixture:
    """在 ``root`` 下现场铺出 parsed_dir：三件套 + ir_source/ + Pillow 图片资产。"""

    parsed_dir = build_parsed_content_dir(root / "parsed", DOC_SHA256)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    content_list = build_content_list_fixture()
    parsed_text = build_parsed_text(content_list)

    paths = build_parsed_artifact_paths(parsed_dir)
    paths["parsed_text"].write_text(parsed_text, encoding="utf-8")
    paths["parsed_metadata"].write_text(
        json.dumps(
            {
                "parser_name": "mineru",
                "parser_profile": "mineru_ir_v2",
                "content_uid": CONTENT_UID,
                "sha256": DOC_SHA256,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    paths["ir_source"].mkdir(parents=True, exist_ok=True)
    (paths["ir_source"] / "content_list.json").write_text(
        json.dumps(content_list, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    image_path = paths["parsed_dir"] / "assets" / "images" / "img_p1_1.png"
    asset_sha256 = write_png_asset(image_path)
    asset_relative_path = "images/img_p1_1.png"

    manifest = {
        "content_uid": CONTENT_UID,
        "sha256": DOC_SHA256,
        "parser_name": "mineru",
        "parser_profile": "mineru_ir_v2",
        "parser_adapter_version": "017_p1_fixture",
        "parsed_text_path": paths["parsed_text"].as_posix(),
        "status": "SUCCESS",
        "asset_files": [
            {
                "relative_path": asset_relative_path,
                "source_name": "img_p1_1.png",
                "sha256": asset_sha256,
            }
        ],
    }
    paths["parse_manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return ParsedFixture(
        parsed_dir=parsed_dir,
        parsed_text=parsed_text,
        content_list=content_list,
        manifest=manifest,
        asset_relative_path=asset_relative_path,
        asset_sha256=asset_sha256,
    )


FIXTURE_ADAPTER_VERSION = "017_p1_fixture"


def _block(
    block_id: str,
    block_type: str,
    order: int,
    *,
    page_no: int | None,
    bbox: dict[str, float] | None,
    confidence: float | None,
    content_ref: dict[str, Any] | None = None,
    attrs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "block_id": block_id,
        "type": block_type,
        "page_no": page_no,
        "bbox": bbox,
        "reading_order": order,
        "confidence": confidence,
        "content_ref": content_ref or {"kind": "none", "reason": "fixture_default"},
        "attrs": attrs or {},
    }


def _span_ref(text: str, span: tuple[int, int]) -> dict[str, Any]:
    return {
        "kind": "text_span",
        "text": text,
        "source": "parsed_text.md",
        "start_offset": span[0],
        "end_offset": span[1],
    }


def build_document_ir_dict(fixture: ParsedFixture) -> dict[str, Any]:
    """手写的合法 ``document_ir.json`` fixture（覆盖 8 类块 + 各退化形态）。

    这是**测试夹具**，不是 adapter：块与 content_list 项的对应关系逐条手写，
    避免把 P2 的映射逻辑提前写进测试。
    """

    text = fixture.parsed_text
    heading_text = "第 1 章 总述"
    paragraph_text = "本季度营收同比增长 12%。"
    unknown_text = "未知类型块必须归并为 paragraph 并记录 raw_type"
    no_bbox_text = "本块没有 bbox，必须置 null 而不是估算填充"

    blocks = [
        _block(
            "b0001",
            "heading",
            0,
            page_no=1,
            bbox={"x0": 72.0, "y0": 60.0, "x1": 523.0, "y1": 92.0},
            confidence=0.98,
            content_ref=_span_ref(heading_text, find_span(text, heading_text)),
            attrs={"heading_level": 1, "caption_of": None},
        ),
        _block(
            "b0002",
            "paragraph",
            1,
            page_no=1,
            bbox={"x0": 72.0, "y0": 100.0, "x1": 523.0, "y1": 128.0},
            confidence=0.97,
            content_ref=_span_ref(paragraph_text, find_span(text, paragraph_text)),
        ),
        _block(
            "b0003",
            "figure",
            2,
            page_no=1,
            bbox={"x0": 100.0, "y0": 200.0, "x1": 480.0, "y1": 420.0},
            confidence=0.95,
            content_ref={"kind": "figure_ref", "figure_id": "f001"},
        ),
        _block(
            "b0004",
            "caption",
            3,
            page_no=1,
            bbox=None,
            confidence=None,
            content_ref={"kind": "inline_text", "text": "图 1-1 系统架构"},
            attrs={"caption_of": "b0003"},
        ),
        _block(
            "b0005",
            "table",
            4,
            page_no=2,
            bbox={"x0": 60.0, "y0": 440.0, "x1": 540.0, "y1": 620.0},
            confidence=0.91,
            content_ref={"kind": "table_ref", "table_id": "t001"},
        ),
        _block(
            "b0006",
            "caption",
            5,
            page_no=2,
            bbox=None,
            confidence=None,
            content_ref={"kind": "inline_text", "text": "表 1-1 关键指标"},
            attrs={"caption_of": "b0005"},
        ),
        _block(
            "b0007",
            "paragraph",
            6,
            page_no=3,
            bbox={"x0": 72.0, "y0": 700.0, "x1": 523.0, "y1": 728.0},
            confidence=0.62,
            content_ref={"kind": "inline_text", "text": unknown_text},
            attrs={"raw_type": "unknown_chart"},
        ),
        _block(
            "b0008",
            "paragraph",
            7,
            page_no=3,
            bbox=None,
            confidence=None,
            content_ref=_span_ref(no_bbox_text, find_span(text, no_bbox_text)),
        ),
        _block(
            "b0009",
            "formula",
            8,
            page_no=2,
            bbox={"x0": 200.0, "y0": 640.0, "x1": 400.0, "y1": 668.0},
            confidence=None,
            content_ref={"kind": "inline_text", "text": "E = mc^{2}"},
        ),
        _block(
            "b0010",
            "header",
            9,
            page_no=1,
            bbox={"x0": 72.0, "y0": 20.0, "x1": 523.0, "y1": 40.0},
            confidence=None,
            content_ref={"kind": "inline_text", "text": "2026 年度报告"},
        ),
        _block(
            "b0011",
            "footer",
            10,
            page_no=2,
            bbox={"x0": 72.0, "y0": 800.0, "x1": 523.0, "y1": 820.0},
            confidence=None,
            content_ref={"kind": "inline_text", "text": "内部资料 请勿外传"},
        ),
    ]

    tables = [
        {
            "table_id": "t001",
            "block_id": "b0005",
            "page_no": 2,
            "bbox": {"x0": 60.0, "y0": 440.0, "x1": 540.0, "y1": 620.0},
            "html": TABLE_HTML,
            "n_rows": 3,
            "n_cols": 3,
            "cells": copy.deepcopy(TABLE_CELLS),
            "caption_block_id": "b0006",
            "confidence": 0.91,
        }
    ]

    figures = [
        {
            "figure_id": "f001",
            "block_id": "b0003",
            "page_no": 1,
            "bbox": {"x0": 100.0, "y0": 200.0, "x1": 480.0, "y1": 420.0},
            "asset_path": f"assets/{fixture.asset_relative_path}",
            "asset_sha256": fixture.asset_sha256,
            "ocr_text": None,
            "caption": "图 1-1 系统架构",
            "caption_block_id": "b0004",
            "image_quality_score": 0.88,
            "confidence": 0.95,
        }
    ]

    document_ir: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        # R-03：document_uid 确定性 = doc_<sha256[:16]>_<adapter_version>
        "document_uid": document_uid_for(DOC_SHA256, FIXTURE_ADAPTER_VERSION),
        "content_uid": CONTENT_UID,
        "sha256": DOC_SHA256,
        "source_vault_path": f"raw_vault/by_hash/3f/3f/{DOC_SHA256}/original.bin",
        "provenance": {
            "parser_name": "mineru",
            "parser_profile": "mineru_ir_v2",
            "parser_adapter_version": FIXTURE_ADAPTER_VERSION,
            "route_type": "PDF_DIGITAL",
            "ir_source": "mineru_content_list",
            "generated_at": "2026-10-16T02:00:00Z",
        },
        "page_count": 3,
        "coordinate_system": {"origin": "top_left", "unit": "pdf_point", "page_ref": "per_page"},
        "blocks": blocks,
        "tables": tables,
        "figures": figures,
        "reading_order": [block["block_id"] for block in blocks],
        "stats": {
            "block_count": len(blocks),
            "table_count": len(tables),
            "figure_count": len(figures),
            "blocks_without_bbox": 3,
            "blocks_low_confidence": 0,
        },
    }
    return document_ir


@pytest.fixture()
def parsed_fixture(tmp_path: Path) -> ParsedFixture:
    return build_parsed_fixture(tmp_path)


@pytest.fixture()
def ir_doc(parsed_fixture: ParsedFixture) -> dict[str, Any]:
    return build_document_ir_dict(parsed_fixture)


def _manifest_assets(fixture: ParsedFixture) -> list[dict[str, str]]:
    return list(fixture.manifest["asset_files"])


def _assert_rejected(
    callable_: Any,
    *,
    code: str | None = None,
    path_fragment: str | None = None,
) -> DocumentIrValidationError:
    with pytest.raises(DocumentIrValidationError) as excinfo:
        callable_()
    error = excinfo.value
    if code is not None:
        assert error.code == code, f"expected code {code!r}, got {error.code!r}: {error}"
    if path_fragment is not None:
        assert path_fragment in error.path, f"expected path ~{path_fragment!r}, got {error.path!r}"
    assert error.reason, "validation error must carry a reason"
    return error


# ---------------------------------------------------------------------------
# fixture 自身完整性（确保后续断言建立在真实数据上，而不是空壳）
# ---------------------------------------------------------------------------


class TestFixtureConstruction:
    def test_content_list_covers_all_required_item_kinds(self, parsed_fixture: ParsedFixture) -> None:
        content_list = parsed_fixture.content_list
        types = {item["type"] for item in content_list}
        assert {"text", "image", "table", "equation"} <= types
        assert "unknown_chart" in types, "fixture 必须含未知 type 项（A017-01 否定断言）"

        heading_items = [item for item in content_list if item.get("text_level") == 1]
        assert heading_items, "fixture 必须含 text_level=1 的 heading 项"
        plain_text_items = [
            item for item in content_list if item["type"] == "text" and "text_level" not in item
        ]
        assert plain_text_items, "fixture 必须含无 text_level 的 paragraph 项"

        image_items = [item for item in content_list if item["type"] == "image"]
        assert image_items and image_items[0]["img_caption"] == ["图 1-1 系统架构"]
        table_items = [item for item in content_list if item["type"] == "table"]
        assert table_items and 'rowspan="2"' in table_items[0]["table_body"]
        assert 'colspan="2"' in table_items[0]["table_body"]
        assert table_items[0]["table_caption"] == ["表 1-1 关键指标"]

        # image/table 的 bbox 走 MinerU 的 img_bbox / table_bbox 字段，不能算“缺失”
        assert all("img_bbox" in item for item in image_items)
        assert all("table_bbox" in item for item in table_items)
        bbox_keys = ("bbox", "img_bbox", "table_bbox")
        missing_bbox_items = [
            item
            for item in content_list
            if not any(key in item for key in bbox_keys)
        ]
        assert len(missing_bbox_items) == 1, "fixture 必须且只能有一个缺 bbox 的块"
        assert missing_bbox_items[0]["type"] == "text"

        header_items = [item for item in content_list if item.get("text_type") == "page_header"]
        footer_items = [item for item in content_list if item.get("text_type") == "page_footer"]
        assert header_items and footer_items, "fixture 必须含页眉 + 页脚项"

    def test_manifest_asset_sha256_matches_real_pillow_file(
        self, parsed_fixture: ParsedFixture
    ) -> None:
        parsed_dir = parsed_fixture.parsed_dir
        asset_path = parsed_dir / "assets" / parsed_fixture.asset_relative_path
        assert asset_path.is_file(), "Pillow 必须现场产生真实图片资产"
        actual_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        recorded = parsed_fixture.manifest["asset_files"][0]
        assert recorded["sha256"] == actual_sha256
        assert recorded["relative_path"] == "images/img_p1_1.png"
        assert re.fullmatch(r"[0-9a-f]{64}", actual_sha256)

    def test_ir_source_and_parsed_text_artifacts_exist(self, parsed_fixture: ParsedFixture) -> None:
        paths = build_parsed_artifact_paths(parsed_fixture.parsed_dir)
        assert paths["parsed_text"].is_file()
        assert paths["parse_manifest"].is_file()
        assert (paths["ir_source"] / "content_list.json").is_file()
        assert paths["parsed_text"].read_text(encoding="utf-8") == parsed_fixture.parsed_text

    def test_fixture_spans_round_trip_parsed_text(self, parsed_fixture: ParsedFixture) -> None:
        """A017-03 正向：fixture 的每个 text_span 都能在 parsed_text 中取回原文。"""

        document_ir = build_document_ir_dict(parsed_fixture)
        text = parsed_fixture.parsed_text
        span_blocks = [
            block
            for block in document_ir["blocks"]
            if block["content_ref"]["kind"] == "text_span"
        ]
        assert len(span_blocks) == 3
        for block in span_blocks:
            ref = block["content_ref"]
            assert text[ref["start_offset"] : ref["end_offset"]] == ref["text"], block["block_id"]


# ---------------------------------------------------------------------------
# A017-01：必填键 / 封闭枚举 / 坐标声明
# ---------------------------------------------------------------------------


class TestBlockRequiredKeys:
    def test_valid_document_passes(self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture) -> None:
        validate_document_ir(
            ir_doc,
            manifest_assets=_manifest_assets(parsed_fixture),
            parsed_text=parsed_fixture.parsed_text,
        )

    def test_null_values_are_legal(self, ir_doc: dict[str, Any]) -> None:
        """值为 ``None`` 合法（缺数据不编造），只有缺键才失败。"""

        block = ir_doc["blocks"][7]
        assert block["bbox"] is None and block["confidence"] is None and block["page_no"] == 3
        validate_block_required_keys(ir_doc)

    def test_missing_confidence_key_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["blocks"][1]["confidence"]
        error = _assert_rejected(
            lambda: validate_block_required_keys(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="blocks[1]",
        )
        assert "confidence" in error.reason

    def test_missing_block_id_key_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["blocks"][0]["block_id"]
        _assert_rejected(
            lambda: validate_block_ids(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="blocks[0].block_id",
        )


class TestBlockTypes:
    def test_all_types_within_closed_enum(self, ir_doc: dict[str, Any]) -> None:
        types = {block["type"] for block in ir_doc["blocks"]}
        assert all(block["type"] in BLOCK_TYPES for block in ir_doc["blocks"])
        assert types == set(BLOCK_TYPES), "fixture 覆盖全部 8 类块"

    def test_unknown_type_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][6]["type"] = "unknown_chart"
        error = _assert_rejected(
            lambda: validate_block_types(ir_doc),
            code=ir_schema.CODE_BLOCK_TYPE_INVALID,
            path_fragment="blocks[6].type",
        )
        assert "unknown_chart" in error.reason

    def test_case_variant_type_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][1]["type"] = "Paragraph"
        _assert_rejected(
            lambda: validate_block_types(ir_doc),
            code=ir_schema.CODE_BLOCK_TYPE_INVALID,
        )

    def test_unknown_type_block_is_merged_with_raw_type_kept(
        self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture
    ) -> None:
        """A017-01 否定：未知 type 既不透传为块类型、也不丢弃块。"""

        unknown_source = [
            item
            for item in parsed_fixture.content_list
            if item["type"] == "unknown_chart"
        ]
        assert len(unknown_source) == 1
        merged = ir_doc["blocks"][6]
        assert merged["type"] == "paragraph"
        assert merged["attrs"]["raw_type"] == "unknown_chart"
        assert merged["content_ref"]["text"] == unknown_source[0]["text"]
        validate_block_types(ir_doc)


class TestCoordinateSystem:
    def test_pdf_point_and_pixel_are_accepted(self, ir_doc: dict[str, Any]) -> None:
        for unit in ("pdf_point", "pixel"):
            ir_doc["coordinate_system"]["unit"] = unit
            assert ir_doc["coordinate_system"]["unit"] in COORDINATE_UNITS
            validate_coordinate_system(ir_doc)

    def test_empty_unit_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["coordinate_system"]["unit"] = ""
        _assert_rejected(
            lambda: validate_coordinate_system(ir_doc),
            code=ir_schema.CODE_COORDINATE_SYSTEM_INVALID,
            path_fragment="coordinate_system.unit",
        )

    def test_unknown_unit_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["coordinate_system"]["unit"] = "inch"
        _assert_rejected(
            lambda: validate_coordinate_system(ir_doc),
            code=ir_schema.CODE_COORDINATE_SYSTEM_INVALID,
        )

    def test_missing_unit_key_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["coordinate_system"]["unit"]
        _assert_rejected(
            lambda: validate_coordinate_system(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="coordinate_system",
        )


class TestTopLevel:
    def test_missing_top_level_key_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["coordinate_system"]
        _assert_rejected(
            lambda: validate_top_level(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="document_ir",
        )

    def test_wrong_schema_version_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["schema_version"] = "document-ir/0.9"
        _assert_rejected(
            lambda: validate_top_level(ir_doc),
            code=ir_schema.CODE_TOP_LEVEL_INVALID,
            path_fragment="schema_version",
        )

    def test_ir_source_outside_enum_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["provenance"]["ir_source"] = "guess_from_pdf"
        _assert_rejected(
            lambda: validate_top_level(ir_doc),
            code=ir_schema.CODE_TOP_LEVEL_INVALID,
            path_fragment="provenance.ir_source",
        )


# ---------------------------------------------------------------------------
# A017-02：reading_order 置换 + block_id 唯一
# ---------------------------------------------------------------------------


class TestReadingOrder:
    def test_valid_permutation_passes(self, ir_doc: dict[str, Any]) -> None:
        orders = [block["reading_order"] for block in ir_doc["blocks"]]
        assert sorted(orders) == list(range(len(ir_doc["blocks"])))
        validate_reading_order(ir_doc)

    def test_duplicate_order_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][1]["reading_order"] = 0
        _assert_rejected(
            lambda: validate_reading_order(ir_doc),
            code=ir_schema.CODE_READING_ORDER_INVALID,
            path_fragment="reading_order",
        )

    def test_hole_in_sequence_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        """块数固定时“空洞”必然伴随“重号”，校验器必须两样都拦住。"""

        ir_doc["blocks"][2]["reading_order"] = 1  # 1 重号、2 空缺
        error = _assert_rejected(
            lambda: validate_reading_order(ir_doc),
            code=ir_schema.CODE_READING_ORDER_INVALID,
        )
        assert "duplicate" in error.reason

    def test_out_of_range_order_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["reading_order"] = len(ir_doc["blocks"])
        _assert_rejected(
            lambda: validate_reading_order(ir_doc),
            code=ir_schema.CODE_READING_ORDER_INVALID,
            path_fragment="blocks[0].reading_order",
        )

    def test_permutation_inconsistent_with_top_level_list_is_rejected(
        self, ir_doc: dict[str, Any]
    ) -> None:
        """块级置换合法但顶层序列被交换 → 双向一致性断言必须命中。"""

        ir_doc["blocks"][0]["reading_order"] = 1
        ir_doc["blocks"][1]["reading_order"] = 0
        _assert_rejected(
            lambda: validate_reading_order(ir_doc),
            code=ir_schema.CODE_READING_ORDER_INVALID,
            path_fragment="blocks[0].reading_order",
        )

    def test_duplicate_block_id_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][1]["block_id"] = "b0001"
        _assert_rejected(
            lambda: validate_block_ids(ir_doc),
            code=ir_schema.CODE_BLOCK_ID_DUPLICATE,
            path_fragment="blocks[1].block_id",
        )


# ---------------------------------------------------------------------------
# A017-03：content_ref / span 回引
# ---------------------------------------------------------------------------


class TestContentRefs:
    def test_spans_are_accepted(self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture) -> None:
        validate_content_refs(ir_doc, parsed_text=parsed_fixture.parsed_text)

    def test_offset_out_of_range_is_rejected(
        self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture
    ) -> None:
        ref = ir_doc["blocks"][1]["content_ref"]
        ref["end_offset"] = len(parsed_fixture.parsed_text) + 1
        _assert_rejected(
            lambda: validate_content_refs(ir_doc, parsed_text=parsed_fixture.parsed_text),
            code=ir_schema.CODE_CONTENT_REF_INVALID,
            path_fragment="end_offset",
        )

    def test_reversed_offsets_are_rejected(self, ir_doc: dict[str, Any]) -> None:
        ref = ir_doc["blocks"][1]["content_ref"]
        ref["start_offset"], ref["end_offset"] = ref["end_offset"], ref["start_offset"]
        _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_CONTENT_REF_INVALID,
        )

    def test_unknown_kind_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][1]["content_ref"]["kind"] = "vibe_ref"
        _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_CONTENT_REF_INVALID,
            path_fragment="kind",
        )

    def test_null_content_ref_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][1]["content_ref"] = None
        _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_CONTENT_REF_INVALID,
            path_fragment="blocks[1].content_ref",
        )


# ---------------------------------------------------------------------------
# A017-04：tables cells 矩阵
# ---------------------------------------------------------------------------


class TestTableCells:
    def test_rowspan_colspan_matrix_is_accepted(self, ir_doc: dict[str, Any]) -> None:
        table = ir_doc["tables"][0]
        anchors = {(cell["row"], cell["col"]) for cell in table["cells"]}
        assert (1, 0) not in anchors, "被 rowspan 覆盖的虚格不得出现在 cells[]"
        assert (0, 2) not in anchors, "被 colspan 覆盖的虚格不得出现在 cells[]"
        assert len(anchors) == 7
        validate_tables(ir_doc)

    def test_virtual_cell_present_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        table = ir_doc["tables"][0]
        table["cells"].append(
            {"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "is_header": False, "text": "虚格", "bbox": None}
        )
        error = _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_CELL_INVALID,
        )
        assert "virtual cell" in error.reason

    def test_overlapping_cells_are_rejected(self, ir_doc: dict[str, Any]) -> None:
        """锚点自由但 span 压到已占用格 → 报“重叠”（区别于虚格）。"""

        table = ir_doc["tables"][0]
        table["n_rows"] = 1
        table["n_cols"] = 3
        table["cells"] = [
            {"row": 0, "col": 2, "rowspan": 1, "colspan": 1, "is_header": False, "text": "尾列", "bbox": None},
            {"row": 0, "col": 0, "rowspan": 1, "colspan": 3, "is_header": False, "text": "整行", "bbox": None},
        ]
        error = _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_CELL_INVALID,
        )
        assert "overlaps" in error.reason

    def test_row_out_of_range_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["tables"][0]["cells"][4]["row"] = 3
        _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_CELL_INVALID,
            path_fragment="row",
        )

    def test_colspan_overflow_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["tables"][0]["cells"][0]["colspan"] = 4
        _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_CELL_INVALID,
            path_fragment="colspan",
        )

    def test_incomplete_coverage_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        """少一个逻辑格 → 矩阵不完整（HTML ↔ cells 无法双向重建）。"""

        ir_doc["tables"][0]["cells"].pop(4)
        error = _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_CELL_INVALID,
            path_fragment="cells",
        )
        assert "uncovered" in error.reason

    def test_empty_cells_with_zero_dims_are_accepted(self, ir_doc: dict[str, Any]) -> None:
        """A017-04 降级路径：HTML 解析失败 → cells=[] + html 原样保留，不伪造 cells。"""

        table = ir_doc["tables"][0]
        table["cells"] = []
        table["n_rows"] = 0
        table["n_cols"] = 0
        assert table["html"] == TABLE_HTML
        validate_tables(ir_doc)

    def test_caption_block_id_must_point_to_caption_block(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["tables"][0]["caption_block_id"] = "b0002"  # paragraph，不是 caption
        _assert_rejected(
            lambda: validate_caption_links(ir_doc),
            code=ir_schema.CODE_CAPTION_LINK_INVALID,
            path_fragment="tables[0].caption_block_id",
        )

    def test_table_block_without_tables_entry_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["tables"] = []
        _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_INVALID,
            path_fragment="blocks[4]",
        )


# ---------------------------------------------------------------------------
# A017-04 / A017-01：caption 关联
# ---------------------------------------------------------------------------


class TestCaptionLinks:
    def test_valid_caption_links_pass(self, ir_doc: dict[str, Any]) -> None:
        validate_caption_links(ir_doc)
        validate_figure_caption_targets(ir_doc)

    def test_dangling_caption_of_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][3]["attrs"]["caption_of"] = "b9999"
        _assert_rejected(
            lambda: validate_caption_links(ir_doc),
            code=ir_schema.CODE_CAPTION_LINK_INVALID,
            path_fragment="blocks[3].attrs.caption_of",
        )

    def test_self_referencing_caption_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][3]["attrs"]["caption_of"] = "b0004"
        _assert_rejected(
            lambda: validate_figure_caption_targets(ir_doc),
            code=ir_schema.CODE_CAPTION_LINK_INVALID,
            path_fragment="blocks[3].attrs.caption_of",
        )

    def test_caption_of_paragraph_target_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][3]["attrs"]["caption_of"] = "b0002"
        _assert_rejected(
            lambda: validate_figure_caption_targets(ir_doc),
            code=ir_schema.CODE_CAPTION_LINK_INVALID,
        )

    def test_dangling_caption_block_id_on_figure_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"][0]["caption_block_id"] = "b0404"
        _assert_rejected(
            lambda: validate_caption_links(ir_doc),
            code=ir_schema.CODE_CAPTION_LINK_INVALID,
            path_fragment="figures[0].caption_block_id",
        )


# ---------------------------------------------------------------------------
# A017-05：figure / asset 对齐（fail-closed）
# ---------------------------------------------------------------------------


class TestAssetAlignment:
    def test_aligned_assets_pass(self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture) -> None:
        validate_figures(ir_doc)
        validate_asset_alignment(ir_doc, _manifest_assets(parsed_fixture))

    def test_sha256_mismatch_is_rejected(
        self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture
    ) -> None:
        """R-07：对齐键 = sha256 → IR 侧 sha256 不在 manifest 注册集中即拒绝。"""

        ir_doc["figures"][0]["asset_sha256"] = "0" * 64
        error = _assert_rejected(
            lambda: validate_asset_alignment(ir_doc, _manifest_assets(parsed_fixture)),
            code=ir_schema.CODE_ASSET_ALIGNMENT_INVALID,
            path_fragment="figures[0].asset_sha256",
        )
        assert "not registered" in error.reason

    def test_path_not_derivable_from_manifest_is_rejected(
        self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture
    ) -> None:
        """sha256 注册了但 IR asset_path 与 R-07 基准转换结果不符 → 拒绝。"""

        ir_doc["figures"][0]["asset_path"] = "assets/images/renamed.png"
        entries = [
            {
                # 模拟 manifest 里该 sha256 对应的相对路径是另一个 basename
                "relative_path": "images/img_p1_1.png",
                "sha256": parsed_fixture.asset_sha256,
            }
        ]
        error = _assert_rejected(
            lambda: validate_asset_alignment(ir_doc, entries),
            code=ir_schema.CODE_ASSET_ALIGNMENT_INVALID,
            path_fragment="figures[0].asset_path",
        )
        assert "manifest-derived path" in error.reason

    def test_relative_path_already_prefixed_with_assets_is_accepted(
        self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture
    ) -> None:
        """R-07 转换：relative_path 已以 assets/ 开头 → 直用，不再重复加前缀。"""

        entries = [
            {
                "relative_path": "assets/images/img_p1_1.png",
                "sha256": parsed_fixture.asset_sha256,
            }
        ]
        validate_asset_alignment(ir_doc, entries)
        assert ir_doc["figures"][0]["asset_path"] == "assets/images/img_p1_1.png"

    def test_asset_registered_under_two_paths_matches_any(
        self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture
    ) -> None:
        entries = [
            {"relative_path": "images/other.png", "sha256": "b" * 64},
            {
                "relative_path": "images/img_p1_1.png",
                "sha256": parsed_fixture.asset_sha256,
            },
        ]
        validate_asset_alignment(ir_doc, entries)

    def test_missing_manifest_assets_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        """fail-closed：无法验证即拒绝，不允许默认放行。"""

        _assert_rejected(
            lambda: validate_asset_alignment(ir_doc, None),
            code=ir_schema.CODE_ASSET_ALIGNMENT_INVALID,
        )

    def test_absolute_asset_path_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"][0]["asset_path"] = "/tmp/staging/images/img_p1_1.png"
        _assert_rejected(
            lambda: validate_figures(ir_doc),
            code=ir_schema.CODE_FIGURE_INVALID,
            path_fragment="asset_path",
        )

    def test_asset_sha256_without_asset_path_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"][0]["asset_path"] = None
        _assert_rejected(
            lambda: validate_figures(ir_doc),
            code=ir_schema.CODE_FIGURE_INVALID,
            path_fragment="asset_sha256",
        )

    def test_figure_without_asset_passes_alignment(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"][0]["asset_path"] = None
        ir_doc["figures"][0]["asset_sha256"] = None
        validate_figures(ir_doc)
        validate_asset_alignment(ir_doc, None)

    def test_figure_block_without_figures_entry_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"] = []
        _assert_rejected(
            lambda: validate_figures(ir_doc),
            code=ir_schema.CODE_FIGURE_INVALID,
            path_fragment="blocks[2]",
        )


# ---------------------------------------------------------------------------
# bbox 结构（缺数据不编造的另一半：有值就必须是规范矩形）
# ---------------------------------------------------------------------------


class TestBBoxStructure:
    def test_null_bbox_is_accepted(self, ir_doc: dict[str, Any]) -> None:
        assert ir_doc["blocks"][7]["bbox"] is None
        validate_block_field_ranges(ir_doc)

    def test_missing_coordinate_key_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["blocks"][0]["bbox"]["y1"]
        _assert_rejected(
            lambda: validate_block_field_ranges(ir_doc),
            code=ir_schema.CODE_BBOX_INVALID,
            path_fragment="blocks[0].bbox",
        )

    def test_inverted_bbox_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["bbox"]["x0"] = 600.0
        _assert_rejected(
            lambda: validate_block_field_ranges(ir_doc),
            code=ir_schema.CODE_BBOX_INVALID,
            path_fragment="blocks[0].bbox",
        )

    def test_confidence_out_of_unit_range_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["confidence"] = 1.4
        _assert_rejected(
            lambda: validate_block_field_ranges(ir_doc),
            code=ir_schema.CODE_FIELD_VALUE_INVALID,
            path_fragment="confidence",
        )


# ---------------------------------------------------------------------------
# TL-RULINGS-P1：R-02 kind 键集 / R-03 顶层 13 键 / R-14 block_id
# ---------------------------------------------------------------------------


class TestContentRefKindSets:
    """R-02：kind 必填 + 各 kind 键集严格（缺 kind / 跨 kind 多键均失败）。"""

    def test_fixture_kinds_all_within_enum(self, ir_doc: dict[str, Any]) -> None:
        kinds = {block["content_ref"]["kind"] for block in ir_doc["blocks"]}
        assert kinds <= set(CONTENT_REF_KIND_KEYS)
        assert {"text_span", "inline_text", "table_ref", "figure_ref"} <= kinds
        validate_content_refs(ir_doc)

    def test_missing_kind_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["blocks"][1]["content_ref"]["kind"]
        _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="blocks[1].content_ref",
        )

    def test_text_span_missing_start_offset_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["blocks"][1]["content_ref"]["start_offset"]
        error = _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="blocks[1].content_ref",
        )
        assert "start_offset" in error.reason

    def test_inline_text_carrying_source_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        """跨 kind 键必须被拒（否则 inline_text 可伪装成 span）。"""

        ir_doc["blocks"][4]["content_ref"]["source"] = "parsed_text.md"
        error = _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_CONTENT_REF_INVALID,
            path_fragment="blocks[4].content_ref",
        )
        assert "must not carry" in error.reason

    def test_none_without_reason_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][1]["content_ref"] = {"kind": "none"}
        error = _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="blocks[1].content_ref",
        )
        assert "reason" in error.reason

    def test_none_with_reason_is_accepted(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][1]["content_ref"] = {"kind": "none", "reason": "empty_block"}
        validate_content_refs(ir_doc)

    def test_dangling_table_ref_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][4]["content_ref"] = {"kind": "table_ref", "table_id": "t999"}
        _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_CONTENT_REF_INVALID,
            path_fragment="table_id",
        )

    def test_dangling_figure_ref_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][2]["content_ref"] = {"kind": "figure_ref", "figure_id": "f999"}
        _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_CONTENT_REF_INVALID,
            path_fragment="figure_id",
        )

    def test_asset_kind_requires_assets_prefixed_path(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][1]["content_ref"] = {
            "kind": "asset",
            "asset_path": "images/img_p1_1.png",
        }
        _assert_rejected(
            lambda: validate_content_refs(ir_doc),
            code=ir_schema.CODE_FIGURE_INVALID,
            path_fragment="asset_path",
        )


class TestTopLevelThirteenKeys:
    """R-03：13 键全部必填；document_uid 确定性；page_count 可 null 不编造。"""

    def test_all_thirteen_keys_present(self, ir_doc: dict[str, Any]) -> None:
        assert set(ir_schema.REQUIRED_TOP_LEVEL_KEYS) == {
            "schema_version",
            "document_uid",
            "content_uid",
            "sha256",
            "source_vault_path",
            "provenance",
            "page_count",
            "coordinate_system",
            "blocks",
            "tables",
            "figures",
            "reading_order",
            "stats",
        }
        assert set(ir_doc) >= set(ir_schema.REQUIRED_TOP_LEVEL_KEYS)
        validate_top_level(ir_doc)

    def test_missing_document_uid_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["document_uid"]
        _assert_rejected(
            lambda: validate_top_level(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="document_ir",
        )

    def test_missing_source_vault_path_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["source_vault_path"]
        _assert_rejected(
            lambda: validate_top_level(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="document_ir",
        )

    def test_non_deterministic_document_uid_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["document_uid"] = "doc_random_uuid_thing"
        error = _assert_rejected(
            lambda: validate_top_level(ir_doc),
            code=ir_schema.CODE_TOP_LEVEL_INVALID,
            path_fragment="document_uid",
        )
        assert "deterministic" in error.reason

    def test_document_uid_formula(self) -> None:
        expected = document_uid_for(DOC_SHA256, "017_mvp_v1")
        assert expected == f"doc_{DOC_SHA256[:16]}_017_mvp_v1"
        assert expected == f"doc_{'3f' * 8}_017_mvp_v1"

    def test_nullable_keys_accept_null(self, ir_doc: dict[str, Any]) -> None:
        """page_count / source_vault_path / provenance 允许 null（markitdown 非分页）。"""

        ir_doc["page_count"] = None
        ir_doc["source_vault_path"] = None
        ir_doc["provenance"] = None
        validate_top_level(ir_doc)

    def test_identity_keys_must_not_be_null(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["document_uid"] = None
        _assert_rejected(
            lambda: validate_top_level(ir_doc),
            code=ir_schema.CODE_TOP_LEVEL_INVALID,
            path_fragment="document_uid",
        )

    def test_markitdown_shaped_document_passes(
        self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture
    ) -> None:
        """R-03 对照：非分页降级路径 page_count=null、全块 bbox=null、无表无图也必须过。"""

        blocks = [
            {
                "block_id": "b0001",
                "type": "heading",
                "page_no": None,
                "bbox": None,
                "reading_order": 0,
                "confidence": None,
                "content_ref": {"kind": "inline_text", "text": "标题"},
                "attrs": {"heading_level": 1},
            },
            {
                "block_id": "b0002",
                "type": "paragraph",
                "page_no": None,
                "bbox": None,
                "reading_order": 1,
                "confidence": None,
                "content_ref": {"kind": "inline_text", "text": "正文"},
                "attrs": {},
            },
        ]
        markitdown_doc = {
            "schema_version": SCHEMA_VERSION,
            "document_uid": document_uid_for(DOC_SHA256, "017_mvp_v1"),
            "content_uid": CONTENT_UID,
            "sha256": DOC_SHA256,
            "source_vault_path": None,
            "provenance": {
                "parser_name": "markitdown",
                "parser_profile": "markitdown_default_v1",
                "parser_adapter_version": "017_mvp_v1",
                "ir_source": "markitdown_text_only",
            },
            "page_count": None,
            "coordinate_system": {"origin": "top_left", "unit": "pdf_point", "page_ref": "per_page"},
            "blocks": blocks,
            "tables": [],
            "figures": [],
            "reading_order": ["b0001", "b0002"],
            "stats": {
                "block_count": 2,
                "table_count": 0,
                "figure_count": 0,
                "blocks_without_bbox": 2,
                "blocks_low_confidence": 0,
            },
        }
        validate_document_ir(markitdown_doc, manifest_assets=None)
        assert markitdown_doc["page_count"] is None
        assert all(block["bbox"] is None for block in markitdown_doc["blocks"])


class TestBlockIdFormat:
    """R-14：b + ≥4 位数字，按 reading_order 递增，>9999 自然进位不截断。"""

    def test_fixture_ids_match_four_digit_pattern(self, ir_doc: dict[str, Any]) -> None:
        for block in ir_doc["blocks"]:
            assert re.fullmatch(r"b\d{4,}", block["block_id"]), block["block_id"]
        validate_block_ids(ir_doc)

    def test_too_short_id_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["block_id"] = "b1"
        _assert_rejected(
            lambda: validate_block_ids(ir_doc),
            code=ir_schema.CODE_FIELD_VALUE_INVALID,
            path_fragment="blocks[0].block_id",
        )

    def test_uppercase_prefix_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["block_id"] = "B0001"
        _assert_rejected(
            lambda: validate_block_ids(ir_doc),
            code=ir_schema.CODE_FIELD_VALUE_INVALID,
        )

    def test_five_digit_id_is_accepted(self) -> None:
        payload = {"blocks": [{"block_id": "b10000", "reading_order": 0}]}
        validate_block_ids(payload)

    def test_id_not_following_reading_order_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        """把 b0001/b0002 对调 id → 数字随 reading_order 递减，必须失败。"""

        ir_doc["blocks"][0]["block_id"], ir_doc["blocks"][1]["block_id"] = (
            "b0002",
            "b0001",
        )
        error = _assert_rejected(
            lambda: validate_block_ids(ir_doc),
            code=ir_schema.CODE_FIELD_VALUE_INVALID,
            path_fragment="block_id",
        )
        assert "increase with reading_order" in error.reason


# ---------------------------------------------------------------------------
# TL-RULINGS-P1：R-04 bbox 严格 / R-05 cells 键集 / R-06 禁格级 confidence
# ---------------------------------------------------------------------------


class TestBBoxRuling:
    """R-04：bbox 为对象或 null；四值 number、>=0、x1>x0、y1>y0；数组形态拒绝。"""

    def test_array_bbox_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["bbox"] = [72.0, 60.0, 523.0, 92.0]
        _assert_rejected(
            lambda: validate_block_field_ranges(ir_doc),
            code=ir_schema.CODE_BBOX_INVALID,
            path_fragment="blocks[0].bbox",
        )

    def test_zero_width_bbox_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["bbox"]["x1"] = ir_doc["blocks"][0]["bbox"]["x0"]
        error = _assert_rejected(
            lambda: validate_block_field_ranges(ir_doc),
            code=ir_schema.CODE_BBOX_INVALID,
            path_fragment="blocks[0].bbox",
        )
        assert "x1 > x0" in error.reason

    def test_negative_coordinate_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["bbox"]["y0"] = -1.0
        error = _assert_rejected(
            lambda: validate_block_field_ranges(ir_doc),
            code=ir_schema.CODE_BBOX_INVALID,
            path_fragment="blocks[0].bbox.y0",
        )
        assert ">= 0" in error.reason

    def test_valid_bbox_after_rejection_still_passes(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["blocks"][0]["bbox"] = {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}
        validate_block_field_ranges(ir_doc)


class TestCellKeySet:
    """R-05 / R-06：cells 键集固定 7 键、bbox 恒 null、无条件禁 confidence。"""

    def test_fixture_cells_use_exact_key_set(self, ir_doc: dict[str, Any]) -> None:
        for cell in ir_doc["tables"][0]["cells"]:
            assert set(cell) == set(ir_schema.CELL_KEYS)
            assert cell["bbox"] is None
        validate_tables(ir_doc)

    def test_cell_confidence_is_rejected_even_when_table_confidence_set(
        self, ir_doc: dict[str, Any]
    ) -> None:
        assert ir_doc["tables"][0]["confidence"] is not None
        ir_doc["tables"][0]["cells"][0]["confidence"] = 0.9
        error = _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_CELL_INVALID,
            path_fragment="cells[0]",
        )
        assert "confidence" in error.reason

    def test_cell_bbox_must_be_null(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["tables"][0]["cells"][0]["bbox"] = {
            "x0": 1.0,
            "y0": 1.0,
            "x1": 2.0,
            "y1": 2.0,
        }
        _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_CELL_INVALID,
            path_fragment="cells[0].bbox",
        )

    def test_cell_missing_bbox_key_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["tables"][0]["cells"][0]["bbox"]
        _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="cells[0]",
        )

    def test_cell_extra_key_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["tables"][0]["cells"][0]["note"] = "x"
        _assert_rejected(
            lambda: validate_tables(ir_doc),
            code=ir_schema.CODE_TABLE_CELL_INVALID,
            path_fragment="cells[0]",
        )


class TestFigureRulings:
    """R-08 assets/images 固定目标 / R-11 figures 不得带 degraded。"""

    def test_degraded_key_on_figure_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"][0]["degraded"] = True
        _assert_rejected(
            lambda: validate_figures(ir_doc),
            code=ir_schema.CODE_FIGURE_INVALID,
            path_fragment="degraded",
        )

    def test_asset_path_outside_images_dir_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"][0]["asset_path"] = "assets/pages/page_1.png"
        _assert_rejected(
            lambda: validate_figures(ir_doc),
            code=ir_schema.CODE_FIGURE_INVALID,
            path_fragment="asset_path",
        )

    def test_nested_asset_subdirectory_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"][0]["asset_path"] = "assets/images/sub/img_p1_1.png"
        _assert_rejected(
            lambda: validate_figures(ir_doc),
            code=ir_schema.CODE_FIGURE_INVALID,
            path_fragment="asset_path",
        )

    def test_assets_images_basename_is_accepted(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["figures"][0]["asset_path"] = "assets/images/f001_img_p1_1.png"
        validate_figures(ir_doc)


# ---------------------------------------------------------------------------
# stats：必须由实际数据重算（D-2 第 4 条 / R-09）
# ---------------------------------------------------------------------------


class TestStats:
    def test_derived_stats_match_fixture(self, ir_doc: dict[str, Any]) -> None:
        derived = derive_stats(
            ir_doc["blocks"], ir_doc["tables"], ir_doc["figures"]
        )
        assert derived == ir_doc["stats"]
        assert derived["block_count"] == len(ir_doc["blocks"])
        assert derived["blocks_without_bbox"] == 3  # b0004/b0006 两个 caption 块 + b0008
        validate_stats(ir_doc)

    def test_hardcoded_block_count_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["stats"]["block_count"] = 128
        _assert_rejected(
            lambda: validate_stats(ir_doc),
            code=ir_schema.CODE_STATS_INVALID,
            path_fragment="stats.block_count",
        )

    def test_low_confidence_threshold_is_strictly_below_half(self, ir_doc: dict[str, Any]) -> None:
        """R-09 口径：confidence 非 null 且 < 0.5 才计数（0.5 本身不计）。"""

        assert LOW_CONFIDENCE_THRESHOLD == 0.5
        ir_doc["blocks"][0]["confidence"] = 0.5
        ir_doc["blocks"][1]["confidence"] = 0.49
        derived = derive_stats(ir_doc["blocks"], ir_doc["tables"], ir_doc["figures"])
        assert derived["blocks_low_confidence"] == 1
        ir_doc["stats"]["blocks_low_confidence"] = 1
        validate_stats(ir_doc)

    def test_blocks_without_bbox_must_match_recomputed(self, ir_doc: dict[str, Any]) -> None:
        ir_doc["stats"]["blocks_without_bbox"] = 0
        _assert_rejected(
            lambda: validate_stats(ir_doc),
            code=ir_schema.CODE_STATS_INVALID,
            path_fragment="stats.blocks_without_bbox",
        )

    def test_missing_stats_key_is_rejected(self, ir_doc: dict[str, Any]) -> None:
        del ir_doc["stats"]["blocks_low_confidence"]
        _assert_rejected(
            lambda: validate_stats(ir_doc),
            code=ir_schema.CODE_MISSING_REQUIRED_KEY,
            path_fragment="stats",
        )


# ---------------------------------------------------------------------------
# 纯函数约束（不写盘 / 不读 DB / 不改入参）
# ---------------------------------------------------------------------------


class TestValidatorPurity:
    def test_validator_does_not_mutate_input(
        self, ir_doc: dict[str, Any], parsed_fixture: ParsedFixture
    ) -> None:
        snapshot = copy.deepcopy(ir_doc)
        validate_document_ir(
            ir_doc,
            manifest_assets=_manifest_assets(parsed_fixture),
            parsed_text=parsed_fixture.parsed_text,
        )
        assert ir_doc == snapshot

    def test_schema_module_has_no_io_or_db_dependencies(self) -> None:
        source = Path(ir_schema.__file__).read_text(encoding="utf-8")
        assert "sqlalchemy" not in source.lower()
        assert "pathlib" not in source
        assert "open(" not in source
        assert ".write_text(" not in source

    def test_float_bbox_coerced_by_dataclass_roundtrip(self, ir_doc: dict[str, Any]) -> None:
        """dataclass → dict 的往返不改变语义（供 P2 builder 复用）。"""

        bbox = ir_schema.BBox.from_dict(ir_doc["blocks"][0]["bbox"])
        assert bbox.to_dict() == ir_doc["blocks"][0]["bbox"]
        block = ir_schema.Block(
            block_id="b0001",
            type="paragraph",
            page_no=None,
            bbox=None,
            reading_order=0,
            confidence=None,
            content_ref=ir_schema.ContentRef(kind="none", reason="fixture"),
        )
        assert set(block.to_dict()) >= set(ir_schema.REQUIRED_BLOCK_KEYS)

    def test_content_ref_dataclass_emits_kind_specific_keyset(self) -> None:
        """R-02：dataclass 只输出本 kind 的键集，不含跨 kind 键。"""

        span = ir_schema.ContentRef(
            kind="text_span",
            text="abc",
            source="parsed_text.md",
            start_offset=0,
            end_offset=3,
        )
        assert set(span.to_dict()) == {"kind"} | set(CONTENT_REF_KIND_KEYS["text_span"])
        figure_ref = ir_schema.ContentRef(kind="figure_ref", figure_id="f001")
        assert set(figure_ref.to_dict()) == {"kind"} | set(CONTENT_REF_KIND_KEYS["figure_ref"])
        none_ref = ir_schema.ContentRef(kind="none", reason="no_content")
        assert set(none_ref.to_dict()) == {"kind"} | set(CONTENT_REF_KIND_KEYS["none"])

    def test_unknown_kind_in_dataclass_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ir_schema.ContentRef(kind="bogus").to_dict()


# ---------------------------------------------------------------------------
# A017-09 / T010：parsed_paths 017 additive 键
# ---------------------------------------------------------------------------


class TestParsedPathsAdditive:
    def test_new_keys_are_derived_from_parsed_dir(self, tmp_path: Path) -> None:
        parsed_dir = build_parsed_content_dir(tmp_path / "parsed", DOC_SHA256)
        paths = build_parsed_artifact_paths(parsed_dir)
        assert paths["document_ir"] == parsed_dir / "document_ir.json"
        assert paths["rendered_md"] == parsed_dir / "rendered.md"
        assert paths["ir_source"] == parsed_dir / "ir_source"

    def test_legacy_keys_unchanged(self, tmp_path: Path) -> None:
        parsed_dir = build_parsed_content_dir(tmp_path / "parsed", DOC_SHA256)
        paths = build_parsed_artifact_paths(parsed_dir)
        assert paths["parsed_dir"] == parsed_dir
        assert paths["parsed_text"] == parsed_dir / "parsed_text.md"
        assert paths["parsed_metadata"] == parsed_dir / "parsed_metadata.json"
        assert paths["parse_manifest"] == parsed_dir / "parse_manifest.json"
        assert parsed_dir == (
            tmp_path / "parsed" / "by_hash" / DOC_SHA256[:2] / DOC_SHA256[2:4] / DOC_SHA256
        )

    def test_parsed_artifact_paths_annotations_cover_new_keys(self) -> None:
        hints = get_type_hints(ParsedArtifactPaths)
        assert {"document_ir", "rendered_md", "ir_source"} <= set(hints)
        assert {"parsed_dir", "parsed_text", "parsed_metadata", "parse_manifest"} <= set(hints)


# ======================================================================================
# P2：document_ir_builder（MinerU content_list → IR / MarkItDown 降级）
# 注意：本块的辅助函数统一用 `_b_` 前缀，避免与文件既有 helper 撞名（曾因此覆盖
# 既有 `_manifest_assets()` 并连带打红 4 个既有用例）。
# ======================================================================================
from app.services.document_ir_builder import (  # noqa: E402
    build_from_markitdown_text,
    build_from_mineru_content_list,
)


def _b_entries() -> list[dict[str, Any]]:
    """与真实 mineru content_list 同形的 4 类块（无 bbox，含表格 HTML）。"""

    return [
        {"type": "text", "text": "季度复核报告", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "本季度营收同比增长 12.4%。", "page_idx": 0},
        {
            "type": "table",
            "img_path": "images/t.jpg",
            "table_body": (
                "<table><tr><td colspan=\"2\">指标</td><td>同比</td></tr>"
                "<tr><td>营收</td><td>12480</td><td>+12.4%</td></tr></table>"
            ),
            "table_caption": ["表 1 主要指标"],
            "page_idx": 0,
        },
        {"type": "image", "img_path": "images/f.jpg", "img_caption": ["图 1 趋势"], "page_idx": 0},
    ]


def _b_manifest() -> list[dict[str, str]]:
    return [
        {"relative_path": "images/f.jpg", "sha256": "a" * 64},
        {"relative_path": "images/t.jpg", "sha256": "b" * 64},
    ]


def _b_build(entries=None, *, parsed_text="季度复核报告\n\n本季度营收同比增长 12.4%。"):
    return build_from_mineru_content_list(
        content_list=entries if entries is not None else _b_entries(),
        parsed_text=parsed_text,
        sha256="c" * 64,
        content_uid="cnt-demo",
        page_count=1,
        manifest_assets=_b_manifest(),
    )


class TestBuilderMineruMapping:
    def test_four_block_types_mapped(self) -> None:
        payload = _b_build().to_dict()
        assert [b["type"] for b in payload["blocks"]] == ["heading", "paragraph", "table", "figure"]

    def test_block_id_is_one_based_and_reading_order_is_permutation(self) -> None:
        """R-15：首块 b0001（1-based），reading_order 为 0..n-1 置换。"""

        payload = _b_build().to_dict()
        assert [b["block_id"] for b in payload["blocks"]] == ["b0001", "b0002", "b0003", "b0004"]
        assert [b["reading_order"] for b in payload["blocks"]] == [0, 1, 2, 3]
        assert payload["reading_order"] == ["b0001", "b0002", "b0003", "b0004"]

    def test_missing_bbox_stays_null_and_is_counted(self) -> None:
        """D-2：缺 bbox 一律 null，禁止估算；stats 由数据复算。"""

        payload = _b_build().to_dict()
        assert all(b["bbox"] is None for b in payload["blocks"])
        assert payload["stats"]["blocks_without_bbox"] == len(payload["blocks"])

    def test_unknown_type_falls_back_to_paragraph_with_raw_type(self) -> None:
        entries = [{"type": "unknown_chart", "text": "未知块内容", "page_idx": 0}]
        payload = _b_build(entries, parsed_text="未知块内容").to_dict()

        assert payload["blocks"][0]["type"] == "paragraph"
        assert payload["blocks"][0]["attrs"]["raw_type"] == "unknown_chart"

    def test_table_cells_expand_and_skip_virtual_cells(self) -> None:
        """R-05：colspan 覆盖的虚格不入 cells；展开尺寸计入 n_rows/n_cols。"""

        payload = _b_build().to_dict()
        table = payload["tables"][0]
        # 行0：colspan=2 的「指标」+ 「同比」；行1：营收/12480/+12.4% → 5 个逻辑单元
        assert (table["n_rows"], table["n_cols"]) == (2, 3)
        assert len(table["cells"]) == 5
        first = table["cells"][0]
        assert (first["row"], first["col"], first["colspan"]) == (0, 0, 2)
        assert all("confidence" not in cell for cell in table["cells"])
        assert all(cell["bbox"] is None for cell in table["cells"])

    def test_figure_asset_aligned_with_manifest(self) -> None:
        payload = _b_build().to_dict()
        figure = payload["figures"][0]
        assert figure["asset_path"] == "assets/images/f.jpg"
        assert figure["asset_sha256"] == "a" * 64
        assert figure["caption"] == "图 1 趋势"


class TestBuilderFailClosed:
    def test_unregistered_asset_is_rejected(self) -> None:
        """fail-closed：figure 有 asset_path 但 manifest 无法对齐 → 校验必须失败。"""

        assets = [{"relative_path": "images/other.jpg", "sha256": "d" * 64}]
        ir = build_from_mineru_content_list(
            content_list=_b_entries(),
            parsed_text="季度复核报告",
            sha256="c" * 64,
            content_uid="cnt-demo",
            page_count=1,
            manifest_assets=assets,
        )
        with pytest.raises(ir_schema.DocumentIrValidationError):
            ir.validate(manifest_assets=assets)

    def test_stats_are_recomputed_not_declared(self) -> None:
        payload = _b_build().to_dict()
        assert payload["stats"]["block_count"] == len(payload["blocks"])
        assert payload["stats"]["table_count"] == len(payload["tables"])
        assert payload["stats"]["figure_count"] == len(payload["figures"])


class TestBuilderMarkitdown:
    def test_degraded_ir_has_no_pages_and_no_bbox(self) -> None:
        ir = build_from_markitdown_text(
            text="第一段内容。\n\n第二段内容。", sha256="e" * 64, content_uid="cnt-md"
        )
        payload = ir.to_dict()

        assert payload["page_count"] is None
        assert all(b["bbox"] is None for b in payload["blocks"])
        assert all(b["page_no"] is None for b in payload["blocks"])
        assert payload["provenance"]["ir_source"] == "markitdown_text_only"
        assert [b["content_ref"]["kind"] for b in payload["blocks"]] == ["inline_text", "inline_text"]
        validate_document_ir(payload)
