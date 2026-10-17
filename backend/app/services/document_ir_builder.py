"""017 P2：把解析产物装配为 Document IR（`document_ir.json` 的纯数据层）。

两条映射路径（design §6.1 / §4.4）：

- ``mineru_content_list``：MinerU ``content_list.json`` → IR。8 类块全覆盖；未知 ``type``
  归并为 ``paragraph`` 并把原值写入 ``attrs.raw_type``；缺 ``bbox`` 一律 ``None`` 并计入
  ``stats.blocks_without_bbox``（**禁止估算填充**，D-2）。
- ``markitdown_text_only``：纯文本 → 段落块；``page_no`` / ``bbox`` / ``page_count`` 一律
  ``None``（**不编造页数**，R-03）。

本模块**只装配数据、不写盘、不读 DB**；校验一律委托
`app.services.document_ir_schema.validate_document_ir`（R-12/Q12：校验器只读）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Mapping, Sequence

from app.services.document_ir_schema import (
    BBox,
    Block,
    ContentRef,
    CoordinateSystem,
    DocumentIr,
    DocumentStats,
    IrFigure,
    IrTable,
    TableCell,
    asset_path_from_relative_path,
    derive_stats,
)

#: MinerU 映射路径标识（design §3.1 的 `provenance.ir_source`）
IR_SOURCE_MINERU = "mineru_content_list"
IR_SOURCE_MARKITDOWN = "markitdown_text_only"

PARSER_NAME_MINERU = "mineru"
PARSER_NAME_MARKITDOWN = "markitdown"
PROFILE_MINERU = "mineru_ir_v2"
PROFILE_MARKITDOWN = "markitdown_text_only"

#: 017 适配器版本；`document_uid` 由 (sha256, 该版本) 确定性派生（R-03）
ADAPTER_VERSION = "017_mvp_v1"

PARSED_TEXT_SOURCE = "parsed_text.md"

#: content_list 中已知类型 → IR 块类型；未列出者归并为 paragraph
_KNOWN_TEXT_LEVELS = frozenset({"1", "2", "3", "4", "5", "6"})
_TABLE_TYPE = "table"
_FIGURE_TYPE = "image"
_FORMULA_TYPES = frozenset({"equation", "interline_equation", "inline_equation"})
_HEADER_TYPES = frozenset({"header", "page_header"})
_FOOTER_TYPES = frozenset({"footer", "page_footer"})


@dataclass(frozen=True)
class _CellSeed:
    text: str
    rowspan: int
    colspan: int
    is_header: bool


class _PipeTableParser(HTMLParser):
    """把 MinerU 的 ``table_body`` HTML 拍平成「行 → 单元格种子」结构。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_CellSeed]] = []
        self._row: list[_CellSeed] | None = None
        self._cell: list[str] | None = None
        self._cell_span = (1, 1)
        self._cell_header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
            return
        if tag in {"td", "th"}:
            if self._row is None:
                self._row = []
            self._cell = []
            self._cell_header = tag == "th"
            attr_map = {name: (value or "") for name, value in attrs}
            self._cell_span = (
                _positive_int(attr_map.get("rowspan"), default=1),
                _positive_int(attr_map.get("colspan"), default=1),
            )

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = "".join(self._cell).strip()
            self._row.append(
                _CellSeed(
                    text=text,
                    rowspan=self._cell_span[0],
                    colspan=self._cell_span[1],
                    is_header=self._cell_header,
                )
            )
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _positive_int(raw: str | None, *, default: int) -> int:
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


def _bbox_from(raw: Any) -> BBox | None:
    """content_list 的 bbox 可能是 ``[x0,y0,x1,y1]`` 或对象；缺失/畸形一律 None（不猜）。"""

    if raw is None:
        return None
    if isinstance(raw, Mapping):
        try:
            return BBox(
                x0=float(raw["x0"]),
                y0=float(raw["y0"]),
                x1=float(raw["x1"]),
                y1=float(raw["y1"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) == 4:
        try:
            x0, y0, x1, y1 = (float(item) for item in raw)
        except (TypeError, ValueError):
            return None
        return BBox(x0=x0, y0=y0, x1=x1, y1=y1)
    return None


def _page_no_from(entry: Mapping[str, Any]) -> int | None:
    """``page_idx`` 是 0-based，IR 的 ``page_no`` 是 1-based；无法判定时 None。"""

    page_idx = entry.get("page_idx")
    if isinstance(page_idx, bool) or not isinstance(page_idx, int):
        return None
    return page_idx + 1 if page_idx >= 0 else None


def _locate_span(text: str, parsed_text: str, *, cursor: int) -> tuple[int, int] | None:
    """在 ``parsed_text`` 中定位块文本区间；优先从 ``cursor`` 之后找，保证阅读序单调。"""

    if not text:
        return None
    found = parsed_text.find(text, cursor)
    if found < 0:
        found = parsed_text.find(text)
    if found < 0:
        return None
    return found, found + len(text)


def _text_content_ref(text: str, parsed_text: str | None, *, cursor: int) -> tuple[ContentRef, int]:
    """文本块引用：能定位则 ``text_span``，否则显式退化为 ``inline_text``（R-02，禁静默无引用）。"""

    if parsed_text:
        span = _locate_span(text, parsed_text, cursor=cursor)
        if span is not None:
            start, end = span
            return (
                ContentRef(
                    kind="text_span",
                    text=text,
                    source=PARSED_TEXT_SOURCE,
                    start_offset=start,
                    end_offset=end,
                ),
                end,
            )
    return ContentRef(kind="inline_text", text=text), cursor


def _cells_from_html(html: str | None) -> tuple[tuple[TableCell, ...], int, int]:
    """``table_body`` → 展开矩阵 cells（虚格不入 cells，R-05）。

    HTML 解析失败/为空 → ``cells=()`` 与 ``0×0``（A017-04 的降级读法，不猜尺寸）。
    """

    if not html:
        return (), 0, 0
    parser = _PipeTableParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - 畸形 HTML 一律降级，不中断文档
        return (), 0, 0

    occupied: set[tuple[int, int]] = set()
    cells: list[TableCell] = []
    row_count = 0
    col_count = 0
    for row_index, seeds in enumerate(parser.rows):
        col = 0
        for seed in seeds:
            while (row_index, col) in occupied:
                col += 1
            row_span = max(1, seed.rowspan)
            col_span = max(1, seed.colspan)
            for delta_row in range(row_span):
                for delta_col in range(col_span):
                    occupied.add((row_index + delta_row, col + delta_col))
            cells.append(
                TableCell(
                    row=row_index,
                    col=col,
                    text=seed.text,
                    rowspan=row_span,
                    colspan=col_span,
                    is_header=seed.is_header,
                )
            )
            col += col_span
            col_count = max(col_count, col)
        row_count = max(row_count, row_index + 1)
    if occupied:
        row_count = max(row_count, max(row for row, _ in occupied) + 1)
        col_count = max(col_count, max(col for _, col in occupied) + 1)
    return tuple(cells), row_count, col_count


def _manifest_index(manifest_assets: Any) -> dict[str, str]:
    """``{relative_path: sha256}`` 索引；支持 manifest 原样列表或已建索引的 Mapping。"""

    if manifest_assets is None:
        return {}
    if isinstance(manifest_assets, Mapping):
        return {str(key): str(value) for key, value in manifest_assets.items()}
    index: dict[str, str] = {}
    for entry in manifest_assets:
        if not isinstance(entry, Mapping):
            continue
        relative_path = entry.get("relative_path")
        sha256 = entry.get("sha256")
        if isinstance(relative_path, str) and isinstance(sha256, str):
            index[relative_path] = sha256
    return index


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_as_text(item) for item in value if item)
    return ""


def _join_caption(value: Any) -> str | None:
    text = _as_text(value).strip()
    return text or None


def build_from_mineru_content_list(
    *,
    content_list: Sequence[Mapping[str, Any]],
    parsed_text: str | None,
    sha256: str,
    content_uid: str,
    source_vault_path: str | None = None,
    page_count: int | None = None,
    manifest_assets: Any = None,
    adapter_version: str = ADAPTER_VERSION,
    coordinate_unit: str = "pixel",
) -> DocumentIr:
    """MinerU ``content_list.json`` → :class:`DocumentIr`。

    块顺序 = content_list 顺序（即引擎给的内容流顺序），``block_id`` 按该顺序
    1-based 编号（R-15），``reading_order`` 为 ``0..n-1`` 置换（A017-02）。
    """

    manifest = _manifest_index(manifest_assets)
    blocks: list[Block] = []
    tables: list[IrTable] = []
    figures: list[IrFigure] = []
    unresolved: list[int] = []
    cursor = 0

    for index, entry in enumerate(content_list):
        raw_type = str(entry.get("type") or "")
        page_no = _page_no_from(entry)
        bbox = _bbox_from(entry.get("bbox"))
        block_id = f"b{index + 1:04d}"

        if raw_type == _TABLE_TYPE:
            html = entry.get("table_body")
            html_text = html if isinstance(html, str) else None
            cells, n_rows, n_cols = _cells_from_html(html_text)
            table_id = f"t{len(tables) + 1:03d}"
            tables.append(
                IrTable(
                    table_id=table_id,
                    block_id=block_id,
                    page_no=page_no,
                    bbox=bbox,
                    html=html_text,
                    n_rows=n_rows,
                    n_cols=n_cols,
                    cells=cells,
                    caption_block_id=None,
                    confidence=None,
                )
            )
            block_type = "table"
            content_ref = ContentRef(kind="table_ref", table_id=table_id)
            attrs: dict[str, Any] = {"caption": _join_caption(entry.get("table_caption"))}

        elif raw_type == _FIGURE_TYPE:
            figure_id = f"f{len(figures) + 1:03d}"
            img_path = entry.get("img_path")
            asset_path = (
                asset_path_from_relative_path(str(img_path))
                if isinstance(img_path, str) and img_path
                else None
            )
            relative = asset_path[len("assets/"):] if asset_path else None
            asset_sha256 = manifest.get(relative) if relative else None
            if asset_path is not None and asset_sha256 is None:
                # fail-closed：有资产引用但 manifest 无法对齐 → 不装配该图，交由校验/服务拒绝
                unresolved.append(index)
            figures.append(
                IrFigure(
                    figure_id=figure_id,
                    block_id=block_id,
                    page_no=page_no,
                    bbox=bbox,
                    asset_path=asset_path,
                    asset_sha256=asset_sha256,
                    ocr_text=None,
                    caption=_join_caption(entry.get("img_caption")),
                    caption_block_id=None,
                    image_quality_score=None,
                    confidence=None,
                )
            )
            block_type = "figure"
            content_ref = ContentRef(kind="figure_ref", figure_id=figure_id)
            attrs = {}

        elif raw_type in _FORMULA_TYPES:
            text = _as_text(entry.get("text") or entry.get("latex"))
            content_ref, cursor = _text_content_ref(text, parsed_text, cursor=cursor)
            block_type = "formula"
            attrs = {"latex": text, "raw_type": raw_type}

        elif raw_type in _HEADER_TYPES or raw_type in _FOOTER_TYPES:
            text = _as_text(entry.get("text"))
            content_ref, cursor = _text_content_ref(text, parsed_text, cursor=cursor)
            block_type = "header" if raw_type in _HEADER_TYPES else "footer"
            attrs = {"raw_type": raw_type}

        else:
            text = _as_text(entry.get("text"))
            text_level = entry.get("text_level")
            is_heading = (
                raw_type == "text"
                and str(text_level) in _KNOWN_TEXT_LEVELS
            )
            content_ref, cursor = _text_content_ref(text, parsed_text, cursor=cursor)
            if raw_type not in {"text", "", "paragraph"}:
                # 未知 type：归并 + 记录原值（A017-01 否定断言）
                block_type = "paragraph"
                attrs = {"raw_type": raw_type}
            elif is_heading:
                block_type = "heading"
                attrs = {"heading_level": int(str(text_level))}
            else:
                block_type = "paragraph"
                attrs = {}

        blocks.append(
            Block(
                block_id=block_id,
                type=block_type,
                page_no=page_no,
                bbox=bbox,
                reading_order=index,
                confidence=None,
                content_ref=content_ref,
                attrs=attrs,
            )
        )

    stats_dict = derive_stats(
        [block.to_dict() for block in blocks],
        [table.to_dict() for table in tables],
        [figure.to_dict() for figure in figures],
    )
    stats = DocumentStats(
        block_count=stats_dict["block_count"],
        table_count=stats_dict["table_count"],
        figure_count=stats_dict["figure_count"],
        blocks_without_bbox=stats_dict["blocks_without_bbox"],
        blocks_low_confidence=stats_dict["blocks_low_confidence"],
    )

    return DocumentIr(
        content_uid=content_uid,
        sha256=sha256,
        blocks=tuple(blocks),
        stats=stats,
        tables=tuple(tables),
        figures=tuple(figures),
        coordinate_system=CoordinateSystem(unit=coordinate_unit),
        provenance={
            "parser_name": PARSER_NAME_MINERU,
            "parser_profile": PROFILE_MINERU,
            "parser_adapter_version": adapter_version,
            "ir_source": IR_SOURCE_MINERU,
            "generated_at": _generated_at(),
        },
        page_count=page_count,
        source_vault_path=source_vault_path,
    )


_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")


def build_from_markitdown_text(
    *,
    text: str,
    sha256: str,
    content_uid: str,
    source_vault_path: str | None = None,
    adapter_version: str = ADAPTER_VERSION,
) -> DocumentIr:
    """MarkItDown 纯文本 → IR（降级路径：无 bbox / 无页码 / 无表格结构）。"""

    paragraphs = [chunk.strip() for chunk in _PARAGRAPH_SPLIT.split(text) if chunk.strip()]
    blocks = [
        Block(
            block_id=f"b{index + 1:04d}",
            type="paragraph",
            page_no=None,
            bbox=None,
            reading_order=index,
            confidence=None,
            content_ref=ContentRef(kind="inline_text", text=paragraph),
            attrs={},
        )
        for index, paragraph in enumerate(paragraphs)
    ]
    stats_dict = derive_stats([block.to_dict() for block in blocks], [], [])
    stats = DocumentStats(
        block_count=stats_dict["block_count"],
        table_count=0,
        figure_count=0,
        blocks_without_bbox=stats_dict["blocks_without_bbox"],
        blocks_low_confidence=0,
    )
    return DocumentIr(
        content_uid=content_uid,
        sha256=sha256,
        blocks=tuple(blocks),
        stats=stats,
        coordinate_system=CoordinateSystem(unit="pixel"),
        provenance={
            "parser_name": PARSER_NAME_MARKITDOWN,
            "parser_profile": PROFILE_MARKITDOWN,
            "parser_adapter_version": adapter_version,
            "ir_source": IR_SOURCE_MARKITDOWN,
            "generated_at": _generated_at(),
        },
        page_count=None,
        source_vault_path=source_vault_path,
    )


def _generated_at() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def load_content_list(path: str | Any) -> list[dict[str, Any]]:
    """读取 ``content_list.json``（纯文件读取，供服务层调用；本模块不自带 IO 之外副作用）。"""

    from pathlib import Path

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("content_list.json root must be a list")
    return payload
