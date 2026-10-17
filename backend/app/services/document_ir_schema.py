"""017 Document IR（统一文档中间表示）schema 常量、纯数据结构与校验器。

本模块只承担两件事：

1. 用 dataclass 描述 ``document_ir.json``（``schema_version="document-ir/1.0"``）
   的结构：``DocumentIr`` / ``Block`` / ``IrTable`` / ``IrFigure`` 等；
2. 提供**纯函数**校验器：输入 JSON 形状的 ``dict``，通过则返回 ``None``，
   不通过则抛 :class:`DocumentIrValidationError`（带路径 + 原因 + 错误码）。

判据优先级：``specs/017-unified-document-ir/evidence/TL-RULINGS-P1.md``
（R-01 ~ R-14）＞ design 文档 ＞ 本模块注释。本模块已按裁定落实：
R-01 span 字段名 / R-02 kind 与各 kind 键集 / R-03 顶层 13 键 + document_uid
确定性 / R-04 bbox 严格矩形 / R-05 cells 键集与 bbox 恒 null / R-06 无条件禁
格级 confidence / R-07 对齐键 = sha256 + 路径基准转换 / R-08 assets/images
固定重写目标 / R-09 stats 口径（<0.5）/ R-11 figures 不得带 degraded /
R-13 Q12 只读校验 / R-14 block_id 位数与递增。

红线（plan §5.2 / §5.3）：

- **纯函数**：不写盘、不读数据库、不做任何文件系统访问。需要的外部事实
  （``parse_manifest.asset_files[]``、``parsed_text.md`` 正文）一律由调用方
  显式传入；无法验证时**fail-closed**（拒绝），而不是默认通过。
- **fail-closed**：任一校验项不通过立即抛异常，绝不返回布尔值静默吞掉。
- **缺数据不编造**：可空字段用显式 ``None``；但键必须存在（缺键与值为
  ``None`` 是两种不同语义，前者失败后者合法）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, NoReturn, Sequence

SCHEMA_VERSION = "document-ir/1.0"

# 块类型封闭枚举（A017-01）。枚举外的值一律校验失败，adapter 必须先归并。
BLOCK_TYPES: frozenset[str] = frozenset(
    {
        "heading",
        "paragraph",
        "table",
        "figure",
        "formula",
        "caption",
        "header",
        "footer",
    }
)

# 坐标单位封闭枚举（A017-01 / OQ-7）。MinerU 实测后冻结，不允许静默混用。
COORDINATE_UNITS: frozenset[str] = frozenset({"pdf_point", "pixel"})
COORDINATE_ORIGINS: frozenset[str] = frozenset({"top_left"})

# content_ref.kind 封闭枚举 + 各 kind 键集（TL-RULINGS-P1 R-02）。
# kind 必填；文档块只允许携带本 kind 的键，跨 kind 的键一律校验失败。
CONTENT_REF_KIND_KEYS: dict[str, tuple[str, ...]] = {
    "text_span": ("text", "source", "start_offset", "end_offset"),
    "inline_text": ("text",),
    "table_ref": ("table_id",),
    "figure_ref": ("figure_id",),
    "asset": ("asset_path",),
    "none": ("reason",),
}
CONTENT_REF_KINDS: frozenset[str] = frozenset(CONTENT_REF_KIND_KEYS)

# IR 生成路径标识（设计文档 §3.1）。
IR_SOURCES: frozenset[str] = frozenset(
    {
        "mineru_content_list",
        "mineru_middle_json",
        "docling",
        "vlm_fallback",
        "markitdown_text_only",
    }
)

# caption 块只允许指向表格/图像块（设计文档 §3.2 attrs.caption_of）。
CAPTION_TARGET_TYPES: frozenset[str] = frozenset({"table", "figure"})

# TL-RULINGS-P1 R-03：13 键全部必填（键不得缺）；本模块对「值可 null」的读法：
# page_count / source_vault_path / provenance 允许 null（markitdown 非分页路径
# 必须 page_count=null，禁编造页数），其余结构性/身份字段不得为 null。
REQUIRED_TOP_LEVEL_KEYS: tuple[str, ...] = (
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
)
NULLABLE_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"page_count", "source_vault_path", "provenance"}
)

REQUIRED_BLOCK_KEYS: tuple[str, ...] = (
    "block_id",
    "type",
    "page_no",
    "bbox",
    "reading_order",
    "confidence",
    "content_ref",
)

REQUIRED_TABLE_KEYS: tuple[str, ...] = (
    "table_id",
    "block_id",
    "page_no",
    "bbox",
    "html",
    "n_rows",
    "n_cols",
    "cells",
    "caption_block_id",
    "confidence",
)

REQUIRED_FIGURE_KEYS: tuple[str, ...] = (
    "figure_id",
    "block_id",
    "page_no",
    "bbox",
    "asset_path",
    "asset_sha256",
    "ocr_text",
    "caption",
    "caption_block_id",
    "image_quality_score",
    "confidence",
)

REQUIRED_STATS_KEYS: tuple[str, ...] = (
    "block_count",
    "table_count",
    "figure_count",
    "blocks_without_bbox",
    "blocks_low_confidence",
)

# TL-RULINGS-P1 R-05 / R-06：cells 元素键集固定；bbox 恒 null；无条件禁 confidence。
CELL_KEYS: tuple[str, ...] = (
    "row",
    "col",
    "rowspan",
    "colspan",
    "is_header",
    "text",
    "bbox",
)

# TL-RULINGS-P1 R-09：confidence 非 null 且 < 0.5 计入 blocks_low_confidence。
LOW_CONFIDENCE_THRESHOLD = 0.5

# TL-RULINGS-P1 R-14：block_id = "b" + 顺序号，零填充至少 4 位（>9999 进位不截断）。
BLOCK_ID_PATTERN = re.compile(r"^b\d{4,}$")

# TL-RULINGS-P1 R-08：figure asset 固定落在 assets/images/<basename>。
ASSET_DIR_PREFIX = "assets/"
ASSET_IMAGES_PREFIX = "assets/images/"

# 错误码（测试与报告按码断言，避免依赖自然语言措辞）。
CODE_TOP_LEVEL_INVALID = "TOP_LEVEL_INVALID"
CODE_MISSING_REQUIRED_KEY = "MISSING_REQUIRED_KEY"
CODE_FIELD_VALUE_INVALID = "FIELD_VALUE_INVALID"
CODE_BLOCK_TYPE_INVALID = "BLOCK_TYPE_INVALID"
CODE_BLOCK_ID_DUPLICATE = "BLOCK_ID_DUPLICATE"
CODE_BBOX_INVALID = "BBOX_INVALID"
CODE_READING_ORDER_INVALID = "READING_ORDER_INVALID"
CODE_COORDINATE_SYSTEM_INVALID = "COORDINATE_SYSTEM_INVALID"
CODE_CONTENT_REF_INVALID = "CONTENT_REF_INVALID"
CODE_TABLE_INVALID = "TABLE_INVALID"
CODE_TABLE_CELL_INVALID = "TABLE_CELL_INVALID"
CODE_FIGURE_INVALID = "FIGURE_INVALID"
CODE_CAPTION_LINK_INVALID = "CAPTION_LINK_INVALID"
CODE_ASSET_ALIGNMENT_INVALID = "ASSET_ALIGNMENT_INVALID"
CODE_STATS_INVALID = "STATS_INVALID"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class DocumentIrValidationError(ValueError):
    """IR schema 校验失败。``path`` 指向字段位置，``reason`` 说明原因。"""

    def __init__(self, code: str, path: str, reason: str) -> None:
        self.code = code
        self.path = path
        self.reason = reason
        super().__init__(f"[{code}] {path}: {reason}")


def _fail(code: str, path: str, reason: str) -> NoReturn:
    raise DocumentIrValidationError(code=code, path=path, reason=reason)


# ---------------------------------------------------------------------------
# 基础取值/断言工具（全部纯函数）
# ---------------------------------------------------------------------------


def _as_mapping(value: Any, path: str, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code, path, f"expected mapping, got {type(value).__name__}")
    return value


def _as_list(value: Any, path: str, code: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(code, path, f"expected list, got {type(value).__name__}")
    return list(value)


def _as_int(value: Any, path: str, code: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code, path, f"expected integer, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        _fail(code, path, f"expected integer >= {minimum}, got {value}")
    return value


def _as_number(value: Any, path: str, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code, path, f"expected number, got {type(value).__name__}")
    return float(value)


def _as_str(
    value: Any,
    path: str,
    code: str,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        _fail(code, path, f"expected string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        _fail(code, path, "expected non-empty string")
    return value


def _opt_str(value: Any, path: str, code: str) -> str | None:
    if value is None:
        return None
    return _as_str(value, path, code, allow_empty=True)


def _as_unit_interval(value: Any, path: str, code: str) -> float | None:
    if value is None:
        return None
    number = _as_number(value, path, code)
    if number < 0.0 or number > 1.0:
        _fail(code, path, f"expected value in [0, 1], got {number}")
    return number


def _require_keys(
    mapping: Mapping[str, Any],
    keys: Sequence[str],
    path: str,
    code: str,
) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        _fail(code, path, "missing required keys: " + ", ".join(missing))


def _blocks_of(payload: Any) -> list[Mapping[str, Any]]:
    payload_map = _as_mapping(payload, "document_ir", CODE_TOP_LEVEL_INVALID)
    raw_blocks = _as_list(
        payload_map.get("blocks"), "document_ir.blocks", CODE_TOP_LEVEL_INVALID
    )
    blocks: list[Mapping[str, Any]] = []
    for index, block in enumerate(raw_blocks):
        blocks.append(
            _as_mapping(block, f"document_ir.blocks[{index}]", CODE_TOP_LEVEL_INVALID)
        )
    return blocks


def _entries_of(payload: Any, key: str) -> list[Mapping[str, Any]]:
    payload_map = _as_mapping(payload, "document_ir", CODE_TOP_LEVEL_INVALID)
    raw = _as_list(
        payload_map.get(key), f"document_ir.{key}", CODE_TOP_LEVEL_INVALID
    )
    entries: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw):
        entries.append(
            _as_mapping(item, f"document_ir.{key}[{index}]", CODE_TOP_LEVEL_INVALID)
        )
    return entries


def _block_id_map(
    blocks: Sequence[Mapping[str, Any]],
    *,
    code: str = CODE_TOP_LEVEL_INVALID,
) -> dict[str, Mapping[str, Any]]:
    mapping: dict[str, Mapping[str, Any]] = {}
    for index, block in enumerate(blocks):
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            _fail(code, f"document_ir.blocks[{index}].block_id", "expected non-empty string")
        mapping[block_id] = block
    return mapping


def validate_bbox(value: Any, path: str, code: str = CODE_BBOX_INVALID) -> None:
    """bbox 合法性（TL-RULINGS-P1 R-04）。

    必须是**对象** ``{x0, y0, x1, y1}`` 或 ``null``（数组形态直接拒绝）：
    四值均为 number、均 ``>= 0``、``x1 > x0`` 且 ``y1 > y0``。
    ``null`` 合法（缺数据不编造，禁估算填充）。
    """

    if value is None:
        return
    bbox = _as_mapping(value, path, code)
    for key in ("x0", "y0", "x1", "y1"):
        if key not in bbox:
            _fail(code, path, f"bbox missing required key: {key}")
        number = _as_number(bbox[key], f"{path}.{key}", code)
        if number < 0:
            _fail(code, f"{path}.{key}", f"bbox coordinate must be >= 0, got {number}")
    if bbox["x1"] <= bbox["x0"]:
        _fail(
            code,
            path,
            f"bbox requires x1 > x0, got x0={bbox['x0']} x1={bbox['x1']}",
        )
    if bbox["y1"] <= bbox["y0"]:
        _fail(
            code,
            path,
            f"bbox requires y1 > y0, got y0={bbox['y0']} y1={bbox['y1']}",
        )


# ---------------------------------------------------------------------------
# 逐项校验器（每项独立可调，便于定点变异复验）
# ---------------------------------------------------------------------------


def document_uid_for(sha256: str, parser_adapter_version: str) -> str:
    """TL-RULINGS-P1 R-03 的确定性 document_uid：``doc_<sha256[:16]>_<adapter>``。"""

    return f"doc_{sha256[:16]}_{parser_adapter_version}"


def validate_top_level(payload: Any) -> None:
    """顶层 13 键必填（R-03）+ 版本 + 身份字段 + document_uid 确定性。"""

    payload_map = _as_mapping(payload, "document_ir", CODE_TOP_LEVEL_INVALID)
    _require_keys(
        payload_map, REQUIRED_TOP_LEVEL_KEYS, "document_ir", CODE_MISSING_REQUIRED_KEY
    )
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key in NULLABLE_TOP_LEVEL_KEYS:
            continue
        if payload_map[key] is None:
            _fail(
                CODE_TOP_LEVEL_INVALID,
                f"document_ir.{key}",
                "key is required and must not be null",
            )
    if payload_map["schema_version"] != SCHEMA_VERSION:
        _fail(
            CODE_TOP_LEVEL_INVALID,
            "document_ir.schema_version",
            f"expected {SCHEMA_VERSION!r}, got {payload_map['schema_version']!r}",
        )
    _as_str(
        payload_map["content_uid"], "document_ir.content_uid", CODE_TOP_LEVEL_INVALID
    )
    sha256 = _as_str(payload_map["sha256"], "document_ir.sha256", CODE_TOP_LEVEL_INVALID)
    if _HEX64.match(sha256) is None:
        _fail(
            CODE_TOP_LEVEL_INVALID,
            "document_ir.sha256",
            "expected 64 lowercase hex characters",
        )
    page_count = payload_map["page_count"]
    if page_count is not None:
        _as_int(page_count, "document_ir.page_count", CODE_TOP_LEVEL_INVALID, minimum=0)
    source_vault_path = payload_map["source_vault_path"]
    if source_vault_path is not None:
        _as_str(
            source_vault_path, "document_ir.source_vault_path", CODE_TOP_LEVEL_INVALID
        )

    provenance = payload_map["provenance"]
    adapter_version: str | None = None
    if provenance is not None:
        provenance_map = _as_mapping(
            provenance, "document_ir.provenance", CODE_TOP_LEVEL_INVALID
        )
        for key in (
            "parser_name",
            "parser_profile",
            "parser_adapter_version",
            "ir_source",
        ):
            if key not in provenance_map:
                _fail(
                    CODE_MISSING_REQUIRED_KEY,
                    "document_ir.provenance",
                    f"missing required keys: {key}",
                )
            _as_str(
                provenance_map[key],
                f"document_ir.provenance.{key}",
                CODE_TOP_LEVEL_INVALID,
            )
        if provenance_map["ir_source"] not in IR_SOURCES:
            _fail(
                CODE_TOP_LEVEL_INVALID,
                "document_ir.provenance.ir_source",
                f"expected one of {sorted(IR_SOURCES)},"
                f" got {provenance_map['ir_source']!r}",
            )
        adapter_version = str(provenance_map["parser_adapter_version"])

    document_uid = _as_str(
        payload_map["document_uid"], "document_ir.document_uid", CODE_TOP_LEVEL_INVALID
    )
    if adapter_version is not None:
        expected_uid = document_uid_for(sha256, adapter_version)
        if document_uid != expected_uid:
            _fail(
                CODE_TOP_LEVEL_INVALID,
                "document_ir.document_uid",
                f"expected deterministic {expected_uid!r} (R-03),"
                f" got {document_uid!r}",
            )


def validate_block_required_keys(payload: Any) -> None:
    """每块 7 个必填键必须**都在**；值为 ``None`` 合法，缺键失败。"""

    for index, block in enumerate(_blocks_of(payload)):
        _require_keys(
            block,
            REQUIRED_BLOCK_KEYS,
            f"document_ir.blocks[{index}]",
            CODE_MISSING_REQUIRED_KEY,
        )


def validate_block_ids(payload: Any) -> None:
    """block_id 非空、（R-14）形如 ``b`` + ≥4 位数字、唯一、且按 reading_order 递增。"""

    seen: set[str] = set()
    ordered: list[tuple[int, int]] = []
    for index, block in enumerate(_blocks_of(payload)):
        path = f"document_ir.blocks[{index}].block_id"
        if "block_id" not in block:
            _fail(CODE_MISSING_REQUIRED_KEY, path, "missing required keys: block_id")
        block_id = _as_str(block["block_id"], path, CODE_FIELD_VALUE_INVALID)
        if BLOCK_ID_PATTERN.match(block_id) is None:
            _fail(
                CODE_FIELD_VALUE_INVALID,
                path,
                f"expected 'b' + at least 4 digits (R-14), got {block_id!r}",
            )
        if block_id in seen:
            _fail(CODE_BLOCK_ID_DUPLICATE, path, f"duplicate block_id {block_id!r}")
        seen.add(block_id)
        order = block.get("reading_order")
        if isinstance(order, int) and not isinstance(order, bool):
            ordered.append((order, int(block_id[1:])))

    ordered.sort()
    for (prev_order, prev_number), (order, number) in zip(ordered, ordered[1:]):
        if number <= prev_number:
            _fail(
                CODE_FIELD_VALUE_INVALID,
                "document_ir.blocks[].block_id",
                "block_id number must increase with reading_order"
                f" (R-14): reading_order {prev_order} -> {prev_number},"
                f" reading_order {order} -> {number}",
            )


def validate_block_types(payload: Any) -> None:
    """``type`` 必须落在 8 类封闭枚举内（枚举外值必须由 adapter 归并）。"""

    for index, block in enumerate(_blocks_of(payload)):
        path = f"document_ir.blocks[{index}].type"
        if "type" not in block:
            _fail(CODE_MISSING_REQUIRED_KEY, path, "missing required keys: type")
        block_type = _as_str(block["type"], path, CODE_BLOCK_TYPE_INVALID)
        if block_type not in BLOCK_TYPES:
            _fail(
                CODE_BLOCK_TYPE_INVALID,
                path,
                f"{block_type!r} not in closed enum {sorted(BLOCK_TYPES)}",
            )


def validate_block_field_ranges(payload: Any) -> None:
    """``page_no`` / ``confidence`` / ``reading_order`` / bbox 的值域与结构。"""

    for index, block in enumerate(_blocks_of(payload)):
        base = f"document_ir.blocks[{index}]"
        _validate_block_scalar_fields(block, base)


def _validate_block_scalar_fields(block: Mapping[str, Any], base: str) -> None:
    for key in ("page_no", "reading_order", "confidence", "bbox"):
        if key not in block:
            _fail(CODE_MISSING_REQUIRED_KEY, base, f"missing required keys: {key}")
    page_no = block["page_no"]
    if page_no is not None:
        _as_int(page_no, f"{base}.page_no", CODE_FIELD_VALUE_INVALID, minimum=1)
    _as_int(
        block["reading_order"],
        f"{base}.reading_order",
        CODE_FIELD_VALUE_INVALID,
        minimum=0,
    )
    _as_unit_interval(block["confidence"], f"{base}.confidence", CODE_FIELD_VALUE_INVALID)
    validate_bbox(block["bbox"], f"{base}.bbox")


def validate_reading_order(payload: Any) -> None:
    """``reading_order`` 必须是 ``0..len(blocks)-1`` 的置换，且与顶层序列一致。

    三步全查，缺一即失败（D-3 fail-closed）：
    1. 块级 ``reading_order`` 无重号、无越界、无空洞（置换性）；
    2. 顶层 ``reading_order`` 是 block_id 的置换（长度与集合一致）；
    3. 块 ``reading_order=i`` 必须等于顶层序列第 ``i`` 位（双向一致）。
    """

    blocks = _blocks_of(payload)
    payload_map = _as_mapping(payload, "document_ir", CODE_TOP_LEVEL_INVALID)
    total = len(blocks)

    orders: list[int] = []
    for index, block in enumerate(blocks):
        path = f"document_ir.blocks[{index}].reading_order"
        if "reading_order" not in block:
            _fail(CODE_MISSING_REQUIRED_KEY, path, "missing required keys: reading_order")
        order = _as_int(block["reading_order"], path, CODE_READING_ORDER_INVALID, minimum=0)
        if order >= total:
            _fail(
                CODE_READING_ORDER_INVALID,
                path,
                f"reading_order {order} out of range 0..{total - 1}",
            )
        orders.append(order)

    duplicates = sorted({order for order in orders if orders.count(order) > 1})
    if duplicates:
        _fail(
            CODE_READING_ORDER_INVALID,
            "document_ir.blocks[].reading_order",
            f"duplicate reading_order values: {duplicates}",
        )
    if sorted(orders) != list(range(total)):
        missing = sorted(set(range(total)) - set(orders))
        _fail(
            CODE_READING_ORDER_INVALID,
            "document_ir.blocks[].reading_order",
            f"not a permutation of 0..{total - 1}; missing {missing}",
        )

    top = _as_list(
        payload_map.get("reading_order"),
        "document_ir.reading_order",
        CODE_READING_ORDER_INVALID,
    )
    block_ids = [str(block.get("block_id")) for block in blocks]
    top_ids = [
        _as_str(item, f"document_ir.reading_order[{index}]", CODE_READING_ORDER_INVALID)
        for index, item in enumerate(top)
    ]
    if len(top_ids) != total or sorted(top_ids) != sorted(block_ids):
        _fail(
            CODE_READING_ORDER_INVALID,
            "document_ir.reading_order",
            "top-level reading_order must be a permutation of blocks[].block_id",
        )
    if len(set(top_ids)) != len(top_ids):
        _fail(
            CODE_READING_ORDER_INVALID,
            "document_ir.reading_order",
            "duplicate block_id entries in top-level reading_order",
        )
    for index, block in enumerate(blocks):
        order = orders[index]
        expected = block.get("block_id")
        if top_ids[order] != expected:
            _fail(
                CODE_READING_ORDER_INVALID,
                f"document_ir.blocks[{index}].reading_order",
                f"top-level reading_order[{order}] is {top_ids[order]!r},"
                f" expected {expected!r}",
            )


def validate_coordinate_system(payload: Any) -> None:
    """``coordinate_system.unit`` 必须非空且 ∈ ``{pdf_point, pixel}``。"""

    payload_map = _as_mapping(payload, "document_ir", CODE_TOP_LEVEL_INVALID)
    if "coordinate_system" not in payload_map:
        _fail(
            CODE_MISSING_REQUIRED_KEY,
            "document_ir",
            "missing required keys: coordinate_system",
        )
    coordinate_system = _as_mapping(
        payload_map["coordinate_system"],
        "document_ir.coordinate_system",
        CODE_COORDINATE_SYSTEM_INVALID,
    )
    if "unit" not in coordinate_system:
        _fail(
            CODE_MISSING_REQUIRED_KEY,
            "document_ir.coordinate_system",
            "missing required keys: unit",
        )
    unit = _as_str(
        coordinate_system["unit"],
        "document_ir.coordinate_system.unit",
        CODE_COORDINATE_SYSTEM_INVALID,
    )
    if unit not in COORDINATE_UNITS:
        _fail(
            CODE_COORDINATE_SYSTEM_INVALID,
            "document_ir.coordinate_system.unit",
            f"expected one of {sorted(COORDINATE_UNITS)}, got {unit!r}",
        )
    if "origin" in coordinate_system:
        origin = _as_str(
            coordinate_system["origin"],
            "document_ir.coordinate_system.origin",
            CODE_COORDINATE_SYSTEM_INVALID,
        )
        if origin not in COORDINATE_ORIGINS:
            _fail(
                CODE_COORDINATE_SYSTEM_INVALID,
                "document_ir.coordinate_system.origin",
                f"expected one of {sorted(COORDINATE_ORIGINS)}, got {origin!r}",
            )
    if "page_ref" in coordinate_system:
        _as_str(
            coordinate_system["page_ref"],
            "document_ir.coordinate_system.page_ref",
            CODE_COORDINATE_SYSTEM_INVALID,
        )


def validate_content_refs(payload: Any, *, parsed_text: str | None = None) -> None:
    """``content_ref`` 校验（TL-RULINGS-P1 R-02 + R-01）。

    - ``kind`` 必填且 ∈ 6 值封闭枚举；缺 kind / 非法 → 失败。
    - 各 kind 键集严格：只允许 ``kind`` + 该 kind 的键，多键/缺键均失败
      （``text_span{text, source, start_offset, end_offset}`` /
      ``inline_text{text}`` / ``table_ref{table_id}`` / ``figure_ref{figure_id}`` /
      ``asset{asset_path}`` / ``none{reason}``）。
    - ``text_span`` 为半开区间 ``[start_offset, end_offset)``，字段名固定
      ``start_offset`` / ``end_offset``（R-01）；``parsed_text`` 由调用方传入时
      ``end_offset`` 越界即失败（A017-03 否定断言）。
    - ``table_ref`` / ``figure_ref`` 的目标 id 必须真实存在（fail-closed）。
    """

    if parsed_text is not None and not isinstance(parsed_text, str):
        _fail(
            CODE_CONTENT_REF_INVALID,
            "parsed_text",
            f"expected string, got {type(parsed_text).__name__}",
        )
    text_length = None if parsed_text is None else len(parsed_text)
    table_ids = {
        str(entry.get("table_id")) for entry in _entries_of(payload, "tables")
    }
    figure_ids = {
        str(entry.get("figure_id")) for entry in _entries_of(payload, "figures")
    }

    for index, block in enumerate(_blocks_of(payload)):
        path = f"document_ir.blocks[{index}].content_ref"
        if "content_ref" not in block:
            _fail(
                CODE_MISSING_REQUIRED_KEY, path, "missing required keys: content_ref"
            )
        content_ref = block["content_ref"]
        if content_ref is None:
            _fail(
                CODE_CONTENT_REF_INVALID,
                path,
                "content_ref must be explicit object; use kind='none' with reason",
            )
        ref = _as_mapping(content_ref, path, CODE_CONTENT_REF_INVALID)
        if "kind" not in ref:
            _fail(CODE_MISSING_REQUIRED_KEY, path, "missing required keys: kind")
        kind = _as_str(ref["kind"], f"{path}.kind", CODE_CONTENT_REF_INVALID)
        if kind not in CONTENT_REF_KINDS:
            _fail(
                CODE_CONTENT_REF_INVALID,
                f"{path}.kind",
                f"expected one of {sorted(CONTENT_REF_KINDS)}, got {kind!r}",
            )

        required = CONTENT_REF_KIND_KEYS[kind]
        missing = [key for key in required if key not in ref]
        if missing:
            _fail(
                CODE_MISSING_REQUIRED_KEY,
                path,
                f"kind {kind!r} requires keys: {', '.join(missing)}",
            )
        unexpected = sorted(set(ref) - {"kind"} - set(required))
        if unexpected:
            _fail(
                CODE_CONTENT_REF_INVALID,
                path,
                f"kind {kind!r} must not carry keys: {', '.join(unexpected)}",
            )

        if kind == "text_span":
            _as_str(ref["text"], f"{path}.text", CODE_CONTENT_REF_INVALID, allow_empty=True)
            _as_str(ref["source"], f"{path}.source", CODE_CONTENT_REF_INVALID)
            start = _as_int(
                ref["start_offset"],
                f"{path}.start_offset",
                CODE_CONTENT_REF_INVALID,
                minimum=0,
            )
            end = _as_int(
                ref["end_offset"],
                f"{path}.end_offset",
                CODE_CONTENT_REF_INVALID,
                minimum=0,
            )
            if end < start:
                _fail(
                    CODE_CONTENT_REF_INVALID,
                    path,
                    f"end_offset ({end}) < start_offset ({start})",
                )
            if text_length is not None and end > text_length:
                _fail(
                    CODE_CONTENT_REF_INVALID,
                    f"{path}.end_offset",
                    f"offset {end} out of range of parsed_text length {text_length}",
                )
        elif kind == "inline_text":
            _as_str(ref["text"], f"{path}.text", CODE_CONTENT_REF_INVALID)
        elif kind == "table_ref":
            table_id = _as_str(ref["table_id"], f"{path}.table_id", CODE_CONTENT_REF_INVALID)
            if table_id not in table_ids:
                _fail(
                    CODE_CONTENT_REF_INVALID,
                    f"{path}.table_id",
                    f"references unknown table {table_id!r}",
                )
        elif kind == "figure_ref":
            figure_id = _as_str(
                ref["figure_id"], f"{path}.figure_id", CODE_CONTENT_REF_INVALID
            )
            if figure_id not in figure_ids:
                _fail(
                    CODE_CONTENT_REF_INVALID,
                    f"{path}.figure_id",
                    f"references unknown figure {figure_id!r}",
                )
        elif kind == "asset":
            _validate_relative_asset_path(
                _as_str(ref["asset_path"], f"{path}.asset_path", CODE_CONTENT_REF_INVALID),
                f"{path}.asset_path",
            )
        elif kind == "none":
            _as_str(ref["reason"], f"{path}.reason", CODE_CONTENT_REF_INVALID)


def _validate_relative_asset_path(asset_path: str, path: str) -> None:
    """R-07 / R-08：``asset_path`` 基准 = ``parsed_dir``，且必须落在 assets/ 下。"""

    if asset_path.startswith("/") or ":" in asset_path.split("/")[0]:
        _fail(
            CODE_FIGURE_INVALID,
            path,
            "asset_path must be relative to parsed_dir (absolute path detected)",
        )
    if ".." in asset_path.split("/"):
        _fail(
            CODE_FIGURE_INVALID,
            path,
            "asset_path must not escape parsed_dir with '..'",
        )
    if not asset_path.startswith(ASSET_DIR_PREFIX):
        _fail(
            CODE_FIGURE_INVALID,
            path,
            f"asset_path must start with {ASSET_DIR_PREFIX!r}",
        )


def validate_tables(payload: Any) -> None:
    """``tables[]`` 矩阵合法性：坐标无越界、无重叠、虚格不入 cells、可完整覆盖。

    ``cells=[]`` 且 ``n_rows=n_cols=0`` 是合法降级形态（HTML 解析失败时
    ``cells`` 为空、``html`` 原样保留，见 A017-04），不得伪造 cells。
    """

    blocks = _blocks_of(payload)
    block_ids = _block_id_map(blocks)
    tables = _entries_of(payload, "tables")
    seen_table_ids: set[str] = set()

    for index, table in enumerate(tables):
        base = f"document_ir.tables[{index}]"
        _require_keys(table, REQUIRED_TABLE_KEYS, base, CODE_MISSING_REQUIRED_KEY)
        table_id = _as_str(table["table_id"], f"{base}.table_id", CODE_TABLE_INVALID)
        if table_id in seen_table_ids:
            _fail(CODE_TABLE_INVALID, f"{base}.table_id", f"duplicate table_id {table_id!r}")
        seen_table_ids.add(table_id)

        block_id = _as_str(table["block_id"], f"{base}.block_id", CODE_TABLE_INVALID)
        if block_id not in block_ids:
            _fail(
                CODE_TABLE_INVALID,
                f"{base}.block_id",
                f"references unknown block {block_id!r}",
            )
        if block_ids[block_id].get("type") != "table":
            _fail(
                CODE_TABLE_INVALID,
                f"{base}.block_id",
                f"block {block_id!r} type is {block_ids[block_id].get('type')!r},"
                " expected 'table'",
            )

        page_no = table["page_no"]
        if page_no is not None:
            _as_int(page_no, f"{base}.page_no", CODE_TABLE_INVALID, minimum=1)
        validate_bbox(table["bbox"], f"{base}.bbox", CODE_BBOX_INVALID)
        if table["html"] is not None:
            _as_str(table["html"], f"{base}.html", CODE_TABLE_INVALID, allow_empty=True)
        _as_unit_interval(table["confidence"], f"{base}.confidence", CODE_TABLE_INVALID)
        n_rows = _as_int(table["n_rows"], f"{base}.n_rows", CODE_TABLE_INVALID, minimum=0)
        n_cols = _as_int(table["n_cols"], f"{base}.n_cols", CODE_TABLE_INVALID, minimum=0)
        _validate_table_cells(table, base, n_rows=n_rows, n_cols=n_cols)

    table_block_ids = {table["block_id"] for table in tables}
    for index, block in enumerate(blocks):
        if block.get("type") != "table":
            continue
        if block.get("block_id") not in table_block_ids:
            _fail(
                CODE_TABLE_INVALID,
                f"document_ir.blocks[{index}]",
                f"table block {block.get('block_id')!r} has no entry in tables[]",
            )


def _validate_table_cells(
    table: Mapping[str, Any],
    base: str,
    *,
    n_rows: int,
    n_cols: int,
) -> None:
    cells = _as_list(table["cells"], f"{base}.cells", CODE_TABLE_CELL_INVALID)
    occupied: dict[tuple[int, int], tuple[int, int]] = {}

    for index, raw_cell in enumerate(cells):
        cell_path = f"{base}.cells[{index}]"
        cell = _as_mapping(raw_cell, cell_path, CODE_TABLE_CELL_INVALID)
        # R-05：键集固定为 7 键；R-06：confidence 无条件禁止（不是「表级为 null 才禁」）
        missing = [key for key in CELL_KEYS if key not in cell]
        if missing:
            _fail(
                CODE_MISSING_REQUIRED_KEY,
                cell_path,
                f"missing required keys: {', '.join(missing)}",
            )
        unexpected = sorted(set(cell) - set(CELL_KEYS))
        if unexpected:
            _fail(
                CODE_TABLE_CELL_INVALID,
                cell_path,
                f"cells[] must not carry keys: {', '.join(unexpected)}"
                " (R-05/R-06: fixed key set, no cell-level confidence)",
            )
        row = _as_int(cell["row"], f"{cell_path}.row", CODE_TABLE_CELL_INVALID, minimum=0)
        col = _as_int(cell["col"], f"{cell_path}.col", CODE_TABLE_CELL_INVALID, minimum=0)
        rowspan = _as_int(
            cell["rowspan"], f"{cell_path}.rowspan", CODE_TABLE_CELL_INVALID, minimum=1
        )
        colspan = _as_int(
            cell["colspan"], f"{cell_path}.colspan", CODE_TABLE_CELL_INVALID, minimum=1
        )
        if not isinstance(cell["is_header"], bool):
            _fail(
                CODE_TABLE_CELL_INVALID,
                f"{cell_path}.is_header",
                f"expected boolean, got {type(cell['is_header']).__name__}",
            )
        _as_str(cell["text"], f"{cell_path}.text", CODE_TABLE_CELL_INVALID, allow_empty=True)
        if cell["bbox"] is not None:
            _fail(
                CODE_TABLE_CELL_INVALID,
                f"{cell_path}.bbox",
                "cells[].bbox must be null in V1 (R-05: no cell-level bbox)",
            )

        if row >= n_rows:
            _fail(
                CODE_TABLE_CELL_INVALID,
                f"{cell_path}.row",
                f"row {row} out of range 0..{n_rows - 1}",
            )
        if col >= n_cols:
            _fail(
                CODE_TABLE_CELL_INVALID,
                f"{cell_path}.col",
                f"col {col} out of range 0..{n_cols - 1}",
            )
        if row + rowspan > n_rows:
            _fail(
                CODE_TABLE_CELL_INVALID,
                f"{cell_path}.rowspan",
                f"row {row} + rowspan {rowspan} exceeds n_rows {n_rows}",
            )
        if col + colspan > n_cols:
            _fail(
                CODE_TABLE_CELL_INVALID,
                f"{cell_path}.colspan",
                f"col {col} + colspan {colspan} exceeds n_cols {n_cols}",
            )

        for row_index in range(row, row + rowspan):
            for col_index in range(col, col + colspan):
                position = (row_index, col_index)
                if position in occupied:
                    owner = occupied[position]
                    if position == (row, col) and owner != (row, col):
                        reason = (
                            f"virtual cell at {position} is already covered by"
                            f" cell anchored at {owner}; virtual cells must not"
                            " appear in cells[]"
                        )
                    else:
                        reason = (
                            f"cell anchored at ({row}, {col}) overlaps cell"
                            f" anchored at {owner} at {position}"
                        )
                    _fail(CODE_TABLE_CELL_INVALID, cell_path, reason)
                occupied[position] = (row, col)

    expected = n_rows * n_cols
    if len(occupied) != expected:
        uncovered = [
            (row, col)
            for row in range(n_rows)
            for col in range(n_cols)
            if (row, col) not in occupied
        ]
        _fail(
            CODE_TABLE_CELL_INVALID,
            f"{base}.cells",
            f"cells cover {len(occupied)} of {expected} grid positions;"
            f" uncovered (first 5): {uncovered[:5]}",
        )


def validate_figures(payload: Any) -> None:
    """``figures[]`` 结构与 block 回链、asset_path 相对性（R-07 / R-08 / R-11）。"""

    blocks = _blocks_of(payload)
    block_ids = _block_id_map(blocks)
    figures = _entries_of(payload, "figures")
    seen_figure_ids: set[str] = set()

    for index, figure in enumerate(figures):
        base = f"document_ir.figures[{index}]"
        _require_keys(figure, REQUIRED_FIGURE_KEYS, base, CODE_MISSING_REQUIRED_KEY)
        if "degraded" in figure:
            _fail(
                CODE_FIGURE_INVALID,
                f"{base}.degraded",
                "IR figures[] must not carry 'degraded' (R-11: render-side only)",
            )
        figure_id = _as_str(figure["figure_id"], f"{base}.figure_id", CODE_FIGURE_INVALID)
        if figure_id in seen_figure_ids:
            _fail(
                CODE_FIGURE_INVALID, f"{base}.figure_id", f"duplicate figure_id {figure_id!r}"
            )
        seen_figure_ids.add(figure_id)

        block_id = _as_str(figure["block_id"], f"{base}.block_id", CODE_FIGURE_INVALID)
        if block_id not in block_ids:
            _fail(
                CODE_FIGURE_INVALID,
                f"{base}.block_id",
                f"references unknown block {block_id!r}",
            )
        if block_ids[block_id].get("type") != "figure":
            _fail(
                CODE_FIGURE_INVALID,
                f"{base}.block_id",
                f"block {block_id!r} type is {block_ids[block_id].get('type')!r},"
                " expected 'figure'",
            )

        page_no = figure["page_no"]
        if page_no is not None:
            _as_int(page_no, f"{base}.page_no", CODE_FIGURE_INVALID, minimum=1)
        validate_bbox(figure["bbox"], f"{base}.bbox", CODE_BBOX_INVALID)

        asset_path = figure["asset_path"]
        asset_sha256 = figure["asset_sha256"]
        if asset_path is None:
            if asset_sha256 is not None:
                _fail(
                    CODE_FIGURE_INVALID,
                    f"{base}.asset_sha256",
                    "asset_sha256 requires a non-null asset_path",
                )
        else:
            asset_path = _as_str(asset_path, f"{base}.asset_path", CODE_FIGURE_INVALID)
            _validate_relative_asset_path(asset_path, f"{base}.asset_path")
            # R-08：img_path 重写目标固定 assets/images/<basename>（跨页同名加 figure_id 前缀）
            if not asset_path.startswith(ASSET_IMAGES_PREFIX):
                _fail(
                    CODE_FIGURE_INVALID,
                    f"{base}.asset_path",
                    f"asset_path must be under {ASSET_IMAGES_PREFIX!r} (R-08 rewrite target)",
                )
            if "/" in asset_path[len(ASSET_IMAGES_PREFIX) :]:
                _fail(
                    CODE_FIGURE_INVALID,
                    f"{base}.asset_path",
                    "asset_path must be assets/images/<basename> (no nested sub-directory)",
                )
            if asset_sha256 is None:
                _fail(
                    CODE_FIGURE_INVALID,
                    f"{base}.asset_sha256",
                    "figure with asset_path must record asset_sha256 (fail-closed)",
                )
            sha = _as_str(asset_sha256, f"{base}.asset_sha256", CODE_FIGURE_INVALID)
            if _HEX64.match(sha) is None:
                _fail(
                    CODE_FIGURE_INVALID,
                    f"{base}.asset_sha256",
                    "expected 64 lowercase hex characters",
                )

        _opt_str(figure["ocr_text"], f"{base}.ocr_text", CODE_FIGURE_INVALID)
        _opt_str(figure["caption"], f"{base}.caption", CODE_FIGURE_INVALID)
        _as_unit_interval(
            figure["image_quality_score"], f"{base}.image_quality_score", CODE_FIGURE_INVALID
        )
        _as_unit_interval(figure["confidence"], f"{base}.confidence", CODE_FIGURE_INVALID)
        if figure["caption_block_id"] is not None:
            _as_str(figure["caption_block_id"], f"{base}.caption_block_id", CODE_FIGURE_INVALID)

    figure_block_ids = {figure["block_id"] for figure in figures}
    for index, block in enumerate(blocks):
        if block.get("type") != "figure":
            continue
        if block.get("block_id") not in figure_block_ids:
            _fail(
                CODE_FIGURE_INVALID,
                f"document_ir.blocks[{index}]",
                f"figure block {block.get('block_id')!r} has no entry in figures[]",
            )


def validate_caption_links(payload: Any) -> None:
    """``caption_of`` / ``caption_block_id`` 必须指向存在且类型正确的 caption 块。"""

    blocks = _blocks_of(payload)
    block_ids = _block_id_map(blocks)

    for index, block in enumerate(blocks):
        base = f"document_ir.blocks[{index}]"
        attrs = block.get("attrs")
        if attrs is None:
            continue
        attrs_map = _as_mapping(attrs, f"{base}.attrs", CODE_CAPTION_LINK_INVALID)
        if "caption_of" not in attrs_map:
            continue
        target = attrs_map["caption_of"]
        if target is None:
            continue
        target_id = _as_str(target, f"{base}.attrs.caption_of", CODE_CAPTION_LINK_INVALID)
        _require_existing_block(
            block_ids, target_id, path=f"{base}.attrs.caption_of"
        )

    for index, table in enumerate(_entries_of(payload, "tables")):
        _check_caption_block_id(
            block_ids,
            table.get("caption_block_id"),
            path=f"document_ir.tables[{index}].caption_block_id",
        )
    for index, figure in enumerate(_entries_of(payload, "figures")):
        _check_caption_block_id(
            block_ids,
            figure.get("caption_block_id"),
            path=f"document_ir.figures[{index}].caption_block_id",
        )


def _check_caption_block_id(
    block_ids: Mapping[str, Mapping[str, Any]],
    caption_block_id: Any,
    *,
    path: str,
) -> None:
    """``caption_block_id``（表/图侧回指）：必须指向存在且类型为 caption 的块。"""

    if caption_block_id is None:
        return
    target_id = _as_str(caption_block_id, path, CODE_CAPTION_LINK_INVALID)
    target = _require_existing_block(block_ids, target_id, path=path)
    if target.get("type") != "caption":
        _fail(
            CODE_CAPTION_LINK_INVALID,
            path,
            f"block {target_id!r} type is {target.get('type')!r}, expected 'caption'",
        )


def _require_existing_block(
    block_ids: Mapping[str, Mapping[str, Any]],
    target_id: str,
    *,
    path: str,
) -> Mapping[str, Any]:
    if target_id not in block_ids:
        _fail(
            CODE_CAPTION_LINK_INVALID,
            path,
            f"references unknown block {target_id!r}",
        )
    return block_ids[target_id]


def validate_figure_caption_targets(payload: Any) -> None:
    """``attrs.caption_of`` 只能指向 table / figure 块（图题 / 表题语义）。"""

    blocks = _blocks_of(payload)
    block_ids = _block_id_map(blocks)
    for index, block in enumerate(blocks):
        attrs = block.get("attrs")
        if not isinstance(attrs, Mapping):
            continue
        target = attrs.get("caption_of")
        if target is None:
            continue
        base = f"document_ir.blocks[{index}].attrs.caption_of"
        target_id = _as_str(target, base, CODE_CAPTION_LINK_INVALID)
        if block.get("type") != "caption":
            _fail(
                CODE_CAPTION_LINK_INVALID,
                f"document_ir.blocks[{index}].attrs.caption_of",
                "attrs.caption_of is only meaningful on type='caption' blocks",
            )
        if target_id == block.get("block_id"):
            _fail(
                CODE_CAPTION_LINK_INVALID,
                base,
                "caption block must not reference itself",
            )
        if target_id not in block_ids:
            _fail(
                CODE_CAPTION_LINK_INVALID,
                base,
                f"references unknown block {target_id!r}",
            )
        target_type = block_ids[target_id].get("type")
        if target_type not in CAPTION_TARGET_TYPES:
            _fail(
                CODE_CAPTION_LINK_INVALID,
                base,
                f"caption_of target type is {target_type!r},"
                f" expected one of {sorted(CAPTION_TARGET_TYPES)}",
            )


def asset_path_from_relative_path(relative_path: str) -> str:
    """R-07 基准转换：``relative_path``（基 assets_dir）→ ``asset_path``（基 parsed_dir）。"""

    if relative_path.startswith(ASSET_DIR_PREFIX):
        return relative_path
    return ASSET_DIR_PREFIX + relative_path


def validate_asset_alignment(payload: Any, manifest_assets: Any) -> None:
    """figure ``asset_sha256`` 与 ``parse_manifest.asset_files[]`` 对齐（fail-closed）。

    TL-RULINGS-P1 R-07：**对齐键 = ``sha256``**，不靠路径字符串匹配；
    路径只做基准转换校验（``asset_path == "assets/" + relative_path``）。

    ``manifest_assets`` 支持两种形态：

    - ``Sequence[Mapping]``：``parse_manifest.asset_files`` 原样（每项含
      ``relative_path``（基 ``parsed_dir/assets``）与 ``sha256``）；
    - ``Mapping[str, str]``：``{relative_path: sha256}``。

    存在带 ``asset_path`` 的 figure 而 ``manifest_assets is None`` 时**直接失败**：
    无法验证即拒绝，不允许默认放行。
    """

    figures = _entries_of(payload, "figures")
    targets = [
        (index, figure)
        for index, figure in enumerate(figures)
        if figure.get("asset_path") is not None
    ]

    if manifest_assets is None:
        if targets:
            _fail(
                CODE_ASSET_ALIGNMENT_INVALID,
                "document_ir.figures[].asset_sha256",
                "parse_manifest asset_files required to verify figure assets (fail-closed)",
            )
        return

    if isinstance(manifest_assets, Mapping):
        items: Sequence[Any] = [
            {"relative_path": key, "sha256": value}
            for key, value in manifest_assets.items()
        ]
    else:
        items = _as_list(
            manifest_assets, "parse_manifest.asset_files", CODE_ASSET_ALIGNMENT_INVALID
        )

    sha_to_paths: dict[str, set[str]] = {}
    for position, raw_entry in enumerate(items):
        path = f"parse_manifest.asset_files[{position}]"
        entry = _as_mapping(raw_entry, path, CODE_ASSET_ALIGNMENT_INVALID)
        _require_keys(entry, ("relative_path", "sha256"), path, CODE_MISSING_REQUIRED_KEY)
        relative_path = _as_str(
            entry["relative_path"], f"{path}.relative_path", CODE_ASSET_ALIGNMENT_INVALID
        )
        sha = _as_str(entry["sha256"], f"{path}.sha256", CODE_ASSET_ALIGNMENT_INVALID)
        if _HEX64.match(sha) is None:
            _fail(
                CODE_ASSET_ALIGNMENT_INVALID,
                f"{path}.sha256",
                "expected 64 lowercase hex characters",
            )
        sha_to_paths.setdefault(sha, set()).add(asset_path_from_relative_path(relative_path))

    for index, figure in targets:
        base = f"document_ir.figures[{index}]"
        recorded = figure.get("asset_sha256")
        if recorded is None:
            _fail(
                CODE_ASSET_ALIGNMENT_INVALID,
                f"{base}.asset_sha256",
                "figure with asset_path must record asset_sha256 (fail-closed)",
            )
        expected_paths = sha_to_paths.get(str(recorded))
        if not expected_paths:
            _fail(
                CODE_ASSET_ALIGNMENT_INVALID,
                f"{base}.asset_sha256",
                f"sha256 {recorded!r} is not registered in"
                " parse_manifest.asset_files (R-07 key = sha256)",
            )
        asset_path = str(figure["asset_path"])
        if asset_path not in expected_paths:
            _fail(
                CODE_ASSET_ALIGNMENT_INVALID,
                f"{base}.asset_path",
                f"asset_path {asset_path!r} != manifest-derived path(s)"
                f" {sorted(expected_paths)} (R-07: assets_dir -> parsed_dir)",
            )


def derive_stats(
    blocks: Sequence[Any],
    tables: Sequence[Any],
    figures: Sequence[Any],
) -> dict[str, int]:
    """由实际数据重算 ``stats``（R-09 / D-2 第 4 条：禁止硬编码）。

    口径：前三个计数 = 三个数组的 ``len()``；``blocks_without_bbox`` =
    ``bbox is None`` 计数；``blocks_low_confidence`` = ``confidence`` 非 null
    且 ``< LOW_CONFIDENCE_THRESHOLD (0.5)`` 计数。
    """

    block_list = _as_list(blocks, "blocks", CODE_STATS_INVALID)
    block_count = 0
    without_bbox = 0
    low_confidence = 0
    for index, raw_block in enumerate(block_list):
        block = _as_mapping(raw_block, f"blocks[{index}]", CODE_STATS_INVALID)
        block_count += 1
        if block.get("bbox") is None:
            without_bbox += 1
        confidence = block.get("confidence")
        if confidence is not None:
            score = _as_number(confidence, f"blocks[{index}].confidence", CODE_STATS_INVALID)
            if score < LOW_CONFIDENCE_THRESHOLD:
                low_confidence += 1
    return {
        "block_count": block_count,
        "table_count": len(_as_list(tables, "tables", CODE_STATS_INVALID)),
        "figure_count": len(_as_list(figures, "figures", CODE_STATS_INVALID)),
        "blocks_without_bbox": without_bbox,
        "blocks_low_confidence": low_confidence,
    }


def validate_stats(payload: Any) -> None:
    """``stats.*`` 必须等于由 blocks / tables / figures 重算的值（R-09：禁硬编码）。"""

    payload_map = _as_mapping(payload, "document_ir", CODE_TOP_LEVEL_INVALID)
    if "stats" not in payload_map:
        _fail(CODE_MISSING_REQUIRED_KEY, "document_ir", "missing required keys: stats")
    stats = _as_mapping(payload_map["stats"], "document_ir.stats", CODE_STATS_INVALID)
    _require_keys(stats, REQUIRED_STATS_KEYS, "document_ir.stats", CODE_MISSING_REQUIRED_KEY)
    for key in REQUIRED_STATS_KEYS:
        _as_int(stats[key], f"document_ir.stats.{key}", CODE_STATS_INVALID, minimum=0)

    expected = derive_stats(
        _blocks_of(payload),
        _entries_of(payload, "tables"),
        _entries_of(payload, "figures"),
    )
    for key, expected_value in expected.items():
        if stats[key] != expected_value:
            _fail(
                CODE_STATS_INVALID,
                f"document_ir.stats.{key}",
                f"declared {stats[key]} != recomputed {expected_value}",
            )


def validate_document_ir(
    payload: Any,
    *,
    manifest_assets: Any = None,
    parsed_text: str | None = None,
) -> None:
    """完整校验：按依赖顺序执行全部校验器，首个失败即抛出（Q12：只读，不写盘/不读 DB）。"""

    if isinstance(payload, DocumentIr):
        payload = payload.to_dict()
    validate_top_level(payload)
    validate_coordinate_system(payload)
    validate_block_required_keys(payload)
    validate_block_ids(payload)
    validate_block_types(payload)
    validate_block_field_ranges(payload)
    validate_reading_order(payload)
    validate_content_refs(payload, parsed_text=parsed_text)
    validate_tables(payload)
    validate_figures(payload)
    validate_caption_links(payload)
    validate_figure_caption_targets(payload)
    validate_asset_alignment(payload, manifest_assets)
    validate_stats(payload)


# ---------------------------------------------------------------------------
# 纯数据结构（builder 侧装配用；校验仍由上面的纯函数负责）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BBox":
        return cls(
            x0=float(data["x0"]),
            y0=float(data["y0"]),
            x1=float(data["x1"]),
            y1=float(data["y1"]),
        )


@dataclass(frozen=True)
class ContentRef:
    kind: str
    text: str | None = None
    source: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    table_id: str | None = None
    figure_id: str | None = None
    asset_path: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """只输出本 kind 的键集（R-02：跨 kind 的键一律非法）。"""

        keys = CONTENT_REF_KIND_KEYS.get(self.kind)
        if keys is None:
            raise ValueError(f"unknown content_ref kind: {self.kind!r}")
        payload: dict[str, Any] = {"kind": self.kind}
        for key in keys:
            payload[key] = getattr(self, key)
        return payload


@dataclass(frozen=True)
class Block:
    block_id: str
    type: str
    page_no: int | None
    bbox: BBox | None
    reading_order: int
    confidence: float | None
    content_ref: ContentRef
    attrs: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "type": self.type,
            "page_no": self.page_no,
            "bbox": None if self.bbox is None else self.bbox.to_dict(),
            "reading_order": self.reading_order,
            "confidence": self.confidence,
            "content_ref": self.content_ref.to_dict(),
            "attrs": dict(self.attrs),
        }


@dataclass(frozen=True)
class TableCell:
    """R-05：键集固定 7 键，``bbox`` 恒 null（V1 不建模格级 bbox），无 confidence。"""

    row: int
    col: int
    text: str
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "is_header": self.is_header,
            "text": self.text,
            "bbox": None,
        }


@dataclass(frozen=True)
class IrTable:
    table_id: str
    block_id: str
    page_no: int | None
    bbox: BBox | None
    html: str | None
    n_rows: int
    n_cols: int
    cells: tuple[TableCell, ...] = ()
    caption_block_id: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "block_id": self.block_id,
            "page_no": self.page_no,
            "bbox": None if self.bbox is None else self.bbox.to_dict(),
            "html": self.html,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "cells": [cell.to_dict() for cell in self.cells],
            "caption_block_id": self.caption_block_id,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class IrFigure:
    figure_id: str
    block_id: str
    page_no: int | None
    bbox: BBox | None
    asset_path: str | None = None
    asset_sha256: str | None = None
    ocr_text: str | None = None
    caption: str | None = None
    caption_block_id: str | None = None
    image_quality_score: float | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "block_id": self.block_id,
            "page_no": self.page_no,
            "bbox": None if self.bbox is None else self.bbox.to_dict(),
            "asset_path": self.asset_path,
            "asset_sha256": self.asset_sha256,
            "ocr_text": self.ocr_text,
            "caption": self.caption,
            "caption_block_id": self.caption_block_id,
            "image_quality_score": self.image_quality_score,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class CoordinateSystem:
    unit: str
    origin: str = "top_left"
    page_ref: str = "per_page"

    def to_dict(self) -> dict[str, Any]:
        return {"origin": self.origin, "unit": self.unit, "page_ref": self.page_ref}


@dataclass(frozen=True)
class DocumentStats:
    block_count: int
    table_count: int
    figure_count: int
    blocks_without_bbox: int
    blocks_low_confidence: int

    def to_dict(self) -> dict[str, int]:
        return {
            "block_count": self.block_count,
            "table_count": self.table_count,
            "figure_count": self.figure_count,
            "blocks_without_bbox": self.blocks_without_bbox,
            "blocks_low_confidence": self.blocks_low_confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DocumentStats":
        return cls(
            block_count=int(data["block_count"]),
            table_count=int(data["table_count"]),
            figure_count=int(data["figure_count"]),
            blocks_without_bbox=int(data["blocks_without_bbox"]),
            blocks_low_confidence=int(data["blocks_low_confidence"]),
        )


@dataclass(frozen=True)
class DocumentIr:
    content_uid: str
    sha256: str
    blocks: tuple[Block, ...]
    stats: DocumentStats
    tables: tuple[IrTable, ...] = ()
    figures: tuple[IrFigure, ...] = ()
    coordinate_system: CoordinateSystem = field(
        default_factory=lambda: CoordinateSystem(unit="pdf_point")
    )
    provenance: Mapping[str, Any] | None = field(default_factory=dict)
    page_count: int | None = None
    source_vault_path: str | None = None
    schema_version: str = SCHEMA_VERSION

    def document_uid(self) -> str:
        """R-03 确定性 document_uid；adapter 版本缺失时无法确定性派生。"""

        provenance = self.provenance or {}
        adapter_version = str(provenance.get("parser_adapter_version", ""))
        return document_uid_for(self.sha256, adapter_version)

    def reading_order_ids(self) -> list[str]:
        return [block.block_id for block in sorted(self.blocks, key=lambda b: b.reading_order)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document_uid": self.document_uid(),
            "content_uid": self.content_uid,
            "sha256": self.sha256,
            "source_vault_path": self.source_vault_path,
            "provenance": None if self.provenance is None else dict(self.provenance),
            "page_count": self.page_count,
            "coordinate_system": self.coordinate_system.to_dict(),
            "blocks": [block.to_dict() for block in self.blocks],
            "tables": [table.to_dict() for table in self.tables],
            "figures": [figure.to_dict() for figure in self.figures],
            "reading_order": self.reading_order_ids(),
            "stats": self.stats.to_dict(),
        }

    def validate(
        self,
        *,
        manifest_assets: Any = None,
        parsed_text: str | None = None,
    ) -> None:
        validate_document_ir(
            self.to_dict(),
            manifest_assets=manifest_assets,
            parsed_text=parsed_text,
        )
