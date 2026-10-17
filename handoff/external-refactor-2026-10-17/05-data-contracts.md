# 05 · 数据契约（字段级）

> 重构时**这些是接口**，不是实现细节。改字段 = 改契约 = 全链路回归。

---

## 1. 三件套（005/007 起冻结，只允许 additive）

### 1.1 `parsed_text.md`
- **内容**：Markdown 正文。标题用 `#`，图片用 `![](images/<sha>.jpg)`，表格在 016 之前是 HTML/纯文本，017 起保留引擎 HTML。
- **铁律**：该文件是证据 span 的基准（`content_ref.start_offset/end_offset`、`kb_document_chunk`、`source_char_*` 全部指向它）。**任何重构都不得改写它的内容或 hash**（`rendered.md` 才是渲染产物）。

### 1.2 `parsed_metadata.json`（MinerU 路径）

```json
{
  "parser_name": "mineru",
  "parser_profile": "mineru_default_v1",          // 017 计划升 mineru_ir_v2（尚未改常量）
  "parser_adapter_version": "007_mvp_v1",
  "route_type": "PDF_DIGITAL",                     // 014 嗅探结果
  "content_uid": "<sha256>",
  "sha256": "<sha256>",
  "source_vault_path": "raw_vault/by_hash/ab/cd/<sha>/original.bin",
  "generated_at": "2026-10-17T13:40:43.320393Z",
  "library_version": "magic-pdf"
}
```

图片线（`parser_name=image_ocr`）额外含：`image_quality{method,score,detail}`、`ocr_confidence{mean,min,sample_count}`（**缺则 null，禁止编造**）、`engine{requested,actual,fallback_reason}`、`extra{preprocess_ops,skipped_ops,asset_files[]}`。

### 1.3 `parse_manifest.json`（产物清单）

```json
{
  "content_uid": "<sha>", "sha256": "<sha>",
  "route_type": "PDF_DIGITAL",
  "parser_name": "mineru", "parser_profile": "mineru_default_v1",
  "parser_adapter_version": "007_mvp_v1",
  "source_vault_path": "...",
  "parsed_text_path": "<abs>", "parsed_metadata_path": "<abs>",
  "generated_at": "...",
  "status": "SUCCESS | EMPTY | FAILED",
  "content_size_bytes": 21184783,
  "input_metadata": {"file_ext": ".pdf", "mime_type": "application/pdf",
                     "rule_name": "pdf_sniff_digital", "vault_uid": "<sha>"},
  "output_size_bytes": 648,
  "output_hash": "<sha256 of parsed_text.md>",
  "assets_dir": "<abs>",
  "asset_files": [{"relative_path": "images/<sha>.jpg", "source_name": "<sha>.jpg", "sha256": "<sha>"}],
  "ir_source_files": [{"name": "content_list.json", "relative_path": "ir_source/content_list.json", "sha256": "<sha>"}]
}
```

**失败时有** `warnings[]` + `error{code,message}`。
**路径基准**（关键，易错）：`asset_files[].relative_path` 基准是 **`assets_dir`**；`ir_source_files[].relative_path` 基准是 **`parsed_dir`**；IR 的 `asset_path` 基准也是 **`parsed_dir`**（形如 `assets/images/x.jpg`）。

---

## 2. Document IR（017）`document_ir.json`

```json
{
  "schema_version": "document-ir/1.0",
  "document_uid": "doc_<sha256[:16]>_<adapter_version>",   // 确定性，禁随机
  "content_uid": "<sha>", "sha256": "<sha>",
  "source_vault_path": "...",
  "provenance": {"parser_name": "mineru", "parser_profile": "mineru_ir_v2",
                 "parser_adapter_version": "017_mvp_v1",
                 "ir_source": "mineru_content_list", "generated_at": "..."},
  "page_count": 1,
  "coordinate_system": {"origin": "top_left", "unit": "pixel", "page_ref": "per_page"},
  "blocks": [{
    "block_id": "b0001", "type": "heading", "page_no": 1,
    "bbox": {"x0": 60.0, "y0": 60.0, "x1": 400.0, "y1": 90.0},   // 可为 null
    "reading_order": 0, "confidence": null,
    "content_ref": {"kind": "text_span", "text": "…", "source": "parsed_text.md",
                    "start_offset": 2, "end_offset": 19},
    "attrs": {"heading_level": 1}
  }],
  "tables": [{"table_id": "t001", "block_id": "b0003", "page_no": 1, "bbox": null,
              "html": "<table>…</table>", "n_rows": 4, "n_cols": 4,
              "cells": [{"row":0,"col":0,"rowspan":1,"colspan":1,"is_header":false,
                         "text":"指标","bbox":null}],
              "caption_block_id": null, "confidence": null}],
  "figures": [{"figure_id": "f001", "block_id": "b0004", "page_no": 1, "bbox": null,
               "asset_path": "assets/images/<sha>.jpg", "asset_sha256": "<sha>",
               "ocr_text": null, "caption": "图 1 趋势", "caption_block_id": null,
               "image_quality_score": null, "confidence": null}],
  "reading_order": ["b0001","b0002","b0003","b0004"],
  "stats": {"block_count":4,"table_count":1,"figure_count":1,
            "blocks_without_bbox":4,"blocks_low_confidence":0}
}
```

### 2.1 校验清单（`validate_document_ir`，13 组，顺序执行，首个失败即抛）
1. `validate_top_level`：13 键全在；非 nullable 键不得为 null；`schema_version` 必须等于 `document-ir/1.0`；sha256 为 64 hex；`provenance` 四键齐全且 `ir_source` 属五值枚举；`document_uid` 必须等于确定性派生值。
2. `validate_coordinate_system`：`unit ∈ {pdf_point, pixel}` 且非空。
3. `validate_block_required_keys`：7 键全在（值可 null）。
4. `validate_block_ids`：`^b\d{4,}$`、唯一、随 `reading_order` 严格递增。
5. `validate_block_types`：8 类封闭枚举。
6. `validate_block_field_ranges`：`confidence ∈ [0,1]`、`bbox` 结构（`x1>x0`、`y1>y0`、非负）。
7. `validate_reading_order`：块级置换 + 顶层序列置换 + 双向一致。
8. `validate_content_refs`：kind 六值 + **各 kind 键集严格** + span 越界拒绝 + ref 目标真实存在。
9. `validate_tables`：cells 键集 7 键、坐标无越界/无重叠/虚格不入、矩阵完整覆盖、`caption_block_id` 指向存在块、**cells 不得含 confidence**。
10. `validate_figures`：`asset_path` 相对且落在 `assets/images/`（或 `assets/pages/`）、有 `asset_path` 必须有 `asset_sha256`、figure 块必须有 `figures[]` 条目。
11. `validate_caption_links` / 12. `validate_figure_caption_targets`：caption 关联双向有效。
13. `validate_asset_alignment`（需 `manifest_assets`）：以 **sha256 为键**对齐；manifest 缺失但存在 figure asset → **直接失败**。
14. `validate_stats`：五个计数必须等于 `derive_stats()` 复算值。

> 校验器是**纯函数**（不写盘、不读 DB）。`parsed_text` 与 `manifest_assets` 由调用方显式传入。

---

## 3. 配置文件（`config/`，9 个）

| 文件 | 作用 | 谁读 |
|---|---|---|
| `app.yaml` | 存储根路径 + MySQL 连接 + `raw.original_files_readonly` | `core/config` |
| `app.example.yaml` | 上者的模板（不含真实密码） | 部署 |
| `parser_rules.yaml` | 扩展名 → route_type 规则 | `core/parser_routing` |
| `pdf_sniffing.yaml` | 嗅探阈值（`text_chars >= 100`、`avg >= 20`）、缓存开关 | `core/pdf_sniffing`（**014 独享，禁改**） |
| `image_parse.yaml` | 图片线：评分器、`blur_threshold=100`（Laplacian）、`pillow_blur_threshold=20`、预处理 ops、引擎开关 | `image_parser` |
| `quality_actions.yaml` | 8 类结构问题的处置动作 | `parse_quality_checker` |
| `quality_semantics.yaml` | 016 语义阈值：garbled `warn 0.02 / error 0.10`、confidence `min_mean 0.70`、`min_sample_chars 200` | `parse_quality_checker` |
| `value_rules.yaml` / `project_taxonomy.yaml` | 011 精编规则/分类 | `curated_project_assets` |

---

## 4. 解析产物目录（`parsed/by_hash/ab/cd/<sha>/`）

```
parsed_text.md              （正文，契约冻结）
parsed_metadata.json        （元数据）
parse_manifest.json         （清单；017 增 ir_source_files）
document_ir.json            （017，待 P4 接 CLI 落盘）
rendered.md                 （017 渲染轨，未实现）
assets/images/<sha>.jpg     （抽图，sha 命名）
assets/preprocess/*.png     （015 预处理中间产物）
ir_source/content_list.json （017；引擎结构化块序列）
ir_source/middle.json       （017；版面中间件，可选）
```

---

## 5. 状态枚举（下游判定的共同语言）

| 枚举 | 取值 | 出处 |
|---|---|---|
| `status`（manifest） | `SUCCESS` / `EMPTY` / `FAILED` | 007/015 |
| `decision`（parse item） | `PARSED` / `ALREADY_SUCCESS` / `ROUTE_MISMATCH` / `DEPENDENCY_MISSING` / `ENGINE_ERROR` / `TIMEOUT` / `OUTPUT_CONTRACT_VIOLATION` / `ASSET_INCOMPLETE` / `UNSUPPORTED_WITH_HINT` / `SKIPPED` | 007/015 |
| `route_type` | `PDF_DIGITAL` / `PDF_SCANNED_OR_IMAGE` / `MARKITDOWN` / `IMAGE` / `ROUTE_MISMATCH` / `UNKNOWN` / `UNSUPPORTED` | 004/014/015 |
| `SniffStatus` | `TEXT_LAYER_PRESENT` / `NO_TEXT_LAYER` / `ENCRYPTED` / `MISSING_DEPENDENCY` / `ERROR` / `NOT_APPLICABLE` | 014 |
| 质量 issue code | 008 既有 18 个 + 016 新增 5 个（`OCR_GARBLED_RATIO` / `OCR_LOW_CONFIDENCE` / `OCR_CONFIDENCE_UNAVAILABLE` / `TABLE_STRUCTURE_INCOMPLETE` / `EMPTY_BUT_NONZERO_PAGES`） | 008/016 |
| 质量 schema_version | `1.0`（语义关闭） / `1.1`（语义开启） | 016 |
| `evidence_type` | `section_quote`（v1）/ `table_cell` / `image_region`（017 新）/ `field_value`（**零数据**） | 010/017 |
| `chunk_type` | `text` / `table` / `figure` | 010/017 |
