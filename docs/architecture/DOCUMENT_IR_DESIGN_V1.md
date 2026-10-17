# Document IR 统一文档中间表示设计 V1

> Loop: S2 / Gate: G0（纯设计门禁，不改业务代码）
> Author: ir-architect
> Date: 2026-10-16
> 关联契约：005/007 解析产物三件套（parsed_text.md / parsed_metadata.json / parse_manifest.json）、010-evidence-chain（chunk + evidence）、006 parse registry
> 关联 spec 草案：`specs/017-unified-document-ir/spec.md`

---

## 1. 背景与问题陈述

当前解析产物为 005 契约三件套，文本/表格/图像之间**无块级关联**：

1. `parsed_text.md` 是纯文本流，表格被压平为文本或 HTML 片段，图像仅剩文件引用（或完全丢失）。
2. MinerU 实际产出了带 `bbox`、`page_idx`、块类型（text/image/table/equation）的结构化中间件（`content_list.json` / `middle.json`），但 `MineruPdfParserService._finalize_success_from_staging` 只保留 `.md` 与图片 assets，**结构化中间件在 staging 清理时被丢弃**（mineru_pdf_parser.py:526-540）。
3. 证据链（010）只能产生 `section_quote` 一种证据，chunk 只有 page/section 两级粒度，无法回指"表格第 r 行第 c 列"或"图像区域"。
4. MarkItDown 路线（Office 文档）信息全损：无 bbox、无表格结构、无图像抽取。

目标：定义第 4 件产物 `document_ir.json`（Document Intermediate Representation），使单文档中的**文本 + 表格 + 图像**全面转化为 AI 可理解的块级结构化输入，同时**不动旧三件套契约**（additive-only）。

参考：IBM Docling `DoclingDocument`（DocItem 类型枚举 + 全局 reading order + provenance bbox）与 PP-StructureV3 输出（块序列 + bbox + 类别），但保持精简——本项目不需要版面层级树、列表嵌套、公式 LaTeX 双流等高级特性，V1 只保留：块清单 + 表格矩阵 + 图像清单 + 全局阅读序。

---

## 2. 设计原则

| # | 原则 | 说明 |
|---|------|------|
| P1 | 契约加法，不改旧契约 | 三件套字节级不变；IR 是第 4 件产物；`parser_profile` 升 `*_v2` 以示产物集差异（`kb_document` 已有 `parser_profile` 列，`uk_document_profile(content_uid, parser_profile, pipeline_version)` 天然支持 v1/v2 并存） |
| P2 | 单一事实源 | IR 是所有下游（渲染、chunk、证据）的唯一结构来源；渲染产物可从 IR 确定性再生成 |
| P3 | 回引优先于复制 | `content_ref` 优先用 span 回引 `parsed_text.md` 字符区间或 assets 文件路径，避免内容重复存储 |
| P4 | 解析器无关 | MinerU / Docling / VLM 兜底三路产出同一 IR；差异只体现在 `confidence`、可选字段缺失与 `provenance` |
| P5 | fail-closed | 缺 bbox 的块仍可存在（`bbox=null`），但 `bbox` 存在时必须符合统一坐标规范；证据生成时无 bbox 不得伪造 |
| P6 | 幂等可重建 | 同一 sha256 + 同一 adapter 版本 → IR 字节级确定（排序键固定，时间戳除外） |

---

## 3. document_ir.json Schema V1

### 3.1 顶层结构

```json
{
  "schema_version": "document-ir/1.0",
  "document_uid": "doc_...",
  "content_uid": "cnt_...",
  "sha256": "<64hex>",
  "source_vault_path": "raw_vault/by_hash/ab/cd/abcdef.../original.bin",
  "provenance": {
    "parser_name": "mineru",
    "parser_profile": "mineru_ir_v2",
    "parser_adapter_version": "017_mvp_v1",
    "route_type": "PDF_DIGITAL",
    "ir_source": "mineru_content_list",
    "generated_at": "2026-10-16T02:00:00Z"
  },
  "page_count": 12,
  "coordinate_system": {
    "origin": "top_left",
    "unit": "pdf_point",
    "page_ref": "per_page"
  },
  "blocks": [ "..." ],
  "tables": [ "..." ],
  "figures": [ "..." ],
  "reading_order": ["b0001", "b0002", "..."],
  "stats": {
    "block_count": 128,
    "table_count": 3,
    "figure_count": 5,
    "blocks_without_bbox": 0,
    "blocks_low_confidence": 2
  }
}
```

字段约定：

- `ir_source` ∈ `{mineru_content_list, mineru_middle_json, docling, vlm_fallback, markitdown_text_only}`，记录 IR 由哪条映射路径生成。
- `coordinate_system`：MinerU 原生为页面像素坐标（依版本不同单位有差异），IR 统一声明一次；V1 规定 `unit ∈ {pdf_point, pixel}`，由 adapter 在映射时完成归一并如实记录，**不静默混用**。
- `reading_order`：全局 block_id 序列，跨页单调递增；`blocks[i].reading_order` 是该序列中的索引。

### 3.2 blocks[] — 通用块

```json
{
  "block_id": "b0007",
  "type": "paragraph",
  "page_no": 3,
  "bbox": {"x0": 72.0, "y0": 120.5, "x1": 523.0, "y1": 148.2},
  "reading_order": 7,
  "confidence": 0.97,
  "content_ref": {
    "kind": "text_span",
    "text": "本季度营收同比增长 12%……",
    "source": "parsed_text.md",
    "start_offset": 1024,
    "end_offset": 1158
  },
  "attrs": {
    "heading_level": null,
    "caption_of": null,
    "language": "zh"
  }
}
```

- `type` ∈ `{heading, paragraph, table, figure, formula, caption, header, footer}`（枚举封闭，未知类型由 adapter 归并为最接近者并写入 `attrs.raw_type`）。
- `block_id`：`b` + 4 位序号，按 reading_order 分配；**文档内唯一、跨重跑稳定**（序号由结构顺序决定而非随机）。
- `bbox` 可空（MarkItDown/VLM 降级路径）；`page_no` 从 1 开始，可空（非分页文档）。
- `confidence` ∈ [0,1]，可空（VLM/MinerU 不提供时置 null，不得虚构）。
- `content_ref.kind` ∈ `{text_span, inline_text, table_ref, figure_ref, asset, none}`：
  - `text_span`：回引 `parsed_text.md` 的 `[start_offset, end_offset)` 区间（与 010 chunk 的 offset 体系一致，可互相对齐）。
  - `inline_text`：内容仅存在于 IR（如 VLM 生成、OCR 文本），`text` 字段直接承载。
  - `table_ref` / `figure_ref`：type=table/figure 时指向 `tables[].table_id` / `figures[].figure_id`。
- `attrs.caption_of`：caption 块指向其说明对象 block_id（图题/表题关联）。

### 3.3 tables[] — 表格结构

```json
{
  "table_id": "t001",
  "block_id": "b0012",
  "page_no": 4,
  "bbox": {"x0": 60.0, "y0": 200.0, "x1": 540.0, "y1": 420.0},
  "html": "<table><tr><td colspan=\"2\">项目</td>...</tr></table>",
  "n_rows": 5,
  "n_cols": 4,
  "cells": [
    {"row": 0, "col": 0, "rowspan": 1, "colspan": 2, "is_header": true, "text": "项目", "bbox": null},
    {"row": 0, "col": 2, "rowspan": 1, "colspan": 1, "is_header": true, "text": "金额", "bbox": null}
  ],
  "caption_block_id": "b0011",
  "confidence": 0.91
}
```

- `cells[]` 采用**展开矩阵坐标**：每个逻辑单元记录其左上角锚点 `(row, col)` 与 `rowspan/colspan`；被合并覆盖的虚格不出现在 cells 中。渲染器可据此无歧义重建 HTML 或 MD。
- `html` 保留 MinerU `table_body` 原文（含 rowspan/colspan），作为高保真渲染源；`cells` 是结构化抽取源（供 table_cell 证据与检索）。
- `confidence` 为表级置信度；单元格级置信度 V1 不建模（开放问题 OQ-3）。

### 3.4 figures[] — 图像结构

```json
{
  "figure_id": "f002",
  "block_id": "b0019",
  "page_no": 5,
  "bbox": {"x0": 100.0, "y0": 300.0, "x1": 480.0, "y1": 560.0},
  "asset_path": "assets/images/img_p5_1.jpg",
  "asset_sha256": "<64hex>",
  "ocr_text": "图内文字（若有 OCR）",
  "caption": "图 3-2 系统架构",
  "caption_block_id": "b0020",
  "image_quality_score": 0.88,
  "confidence": 0.95
}
```

- `asset_path` 相对 parsed_dir（与 007 `assets/images/` 现有目录约定一致）；`asset_sha256` 与 `parse_manifest.json.asset_files[].sha256` 对齐校验。
- `image_quality_score` ∈ [0,1]，由 adapter 估算（分辨率/模糊度启发式），可空；低于阈值（默认 0.5，可配）的图在渲染轨降级为 caption-only 并标 `degraded: true`。
- `ocr_text` 可空；MinerU V1 不提供时置 null，VLM 路径可填充。

---

## 4. IR → 双轨渲染规则

渲染器 `IrRenderer` 纯函数：`document_ir.json → (rendered.md, rendered.flat.json)`，不写库。

### 4.1 Markdown 轨（rendered.md）

| IR 元素 | 渲染规则 |
|---|---|
| heading | `#{attrs.heading_level} {text}`；缺 level 时用 `##` |
| paragraph | 原文段落 |
| header/footer | 默认**剔除**正文流，附加到文末 `<!-- page-header/footer: page_no=... -->` 注释（可配置保留） |
| table | 首选 HTML 表（保 rowspan/colspan）；`table.html` 缺失时从 cells 降级生成 MD 管道表（合并单元格重复值填充）；表前插入锚点 `<!-- ir:block=b0012 table=t001 -->` |
| figure | `![{caption}]({asset_path}#block_id={block_id})`，无 caption 用 figure_id；`image_quality_score < 阈值` 或 asset 缺失时渲染为占位 `[图像 {figure_id}: {caption 或 ocr_text 截断}]` |
| caption | 依附其 `caption_of` 目标渲染，不单独成段 |
| formula | `$$\n{content_ref.text}\n$$` |

顺序严格按 `reading_order`。**Markdown 轨可从 IR 100% 再生成，不得引入 IR 之外的内容**（锚点注释除外）。

### 4.2 JSON 轨

两个层次：

1. **权威轨**：`document_ir.json` 本身即 JSON 轨，供程序直接消费。
2. **展平轨**（可选产物 `document_flat.json`）：按 reading_order 展平为一维记录流，每条 = block + 展开的 table cell / figure 引用，便于检索系统按块索引。V1 可先不产出，012 搜索演进时再定。

### 4.3 与旧三件套的关系（OQ-5=B，DECIDED 2026-10-16）

- v2 文档：`parsed_text.md` **保持解析器原文语义不变**（005 契约不动）；渲染轨另落新产物 `rendered.md`，旧消费者（010 chunk 按 offset、012 FULLTEXT）零改动；`document_ir.json` 为第 4 件新增产物。
- v1 文档（存量）：无 IR，继续走旧路径；IR 不反向回填（OQ-2=A 双轨共存，不强制重跑）。

---

## 5. 证据链扩展设计

### 5.1 新 evidence_type

| evidence_type | 来源 | 定位字段 | 说明 |
|---|---|---|---|
| `section_quote`（存量） | text block span | start/end offset + heading_path | 不变 |
| `table_cell` | tables[].cells[] | `metadata.block_id` + `metadata.cell={row,col,rowspan,colspan}` + bbox | quote_text = cell.text |
| `image_region` | figures[] | `metadata.block_id` + `metadata.figure_id` + bbox + `metadata.asset_path` | quote_text = caption/ocr_text |
| `field_value` | （S3 预留） | `metadata.field_name/field_value/bbox` + `confidence` 列 | 见 §7 |

`evidence_uid` 计算扩展负载：`f"evidence|v2|{chunk_uid}|{evidence_type}|{block_id}|{locator}"`（locator = cell 坐标或 figure_id），v1 section_quote 保持原 v1 公式不变（幂等性不破）。

### 5.2 chunk 扩展

- 新 `chunk_type`：`table` / `figure`（与 `text` 并存）；`chunk_level` 新增 `block`。
- `block_id` 存放位置：**V1 进 `metadata_json.block_id`（零迁移）**；OQ-1=A 已裁定不升独立列（DECIDED 2026-10-16），012 检索演进时再评估。
- v2 文档的 chunk 改为**按块聚合**：一个块一个 chunk（paragraph 可相邻合并至 token 上限），table/figure 独立 chunk。chunk ↔ block 是 N:1 关系（`metadata.block_ids[]`）。
- IR 缺位时（v1 文档、IR 生成失败）：回退现有 `_chunk_markitdown` / `_chunk_mineru` 页级策略，**不得阻塞证据链构建**。

### 5.3 sql/ 迁移影响面

| 项 | 是否需迁移 | 依据 |
|---|---|---|
| `kb_evidence.evidence_type` 新枚举值 | **否** | `VARCHAR(64)`，无 CHECK 约束（001_init_schema_v1_1.sql:232） |
| `kb_evidence.confidence` 填充 | **否** | 列已存在 `DECIMAL(5,2)`（line 246） |
| `kb_document_chunk.chunk_type/chunk_level` 新值 | **否** | VARCHAR，无约束 |
| `block_id` 独立列 | 可选（迁移） | 若检索需按 block 过滤则建议加 `block_id VARCHAR(32)` + KEY；V1 放 metadata JSON，P2 DB Review 裁定 |
| `kb_parsed_artifact.artifact_type='document_ir'` | **否** | artifact_type 为 VARCHAR(64)，006 注册表天然支持新类型登记（006_parse_registry_v1.sql:75） |
| `kb_document` 计数列（table_count/image_count）回填 | **否**（可选填充） | 列已存在（document.py:33-34），v2 解析时顺带填充 |

**结论：V1 可实现零 schema 迁移**；唯一候选迁移（block_id 列）交由 P2 DB Review 决定。

### 5.4 search_service 影响面

- 现状：`search_service.py` 未按 `evidence_type` 过滤或分支处理（grep 无命中），新证据类型**零代码改动**即可被 FULLTEXT 命中。
- 建议演进（非 S2 范围）：`hit_type` 增加 `table_cell`/`image_region` 展示分支（前端图标与跳转锚点）；`HIT_TYPE_RANK` 排序权重调整。列为 012 后续 spec 事项。

---

## 6. 适配器落地路径

### 6.1 MinerU → IR 映射表（主路径，优先复用）

**前置变更**（S2 实现期）：`MineruPdfParserService` 在 staging 清理前将 `content_list.json`（必需）与 `middle.json`（可选，用于 span 级置信度）拷入 `parsed_dir/ir_source/`，并在 `parse_manifest.json` 登记。这是当前唯一的信息丢失点。

MinerU `content_list.json` → IR 映射：

| content_list item | 判定条件 | IR block.type | IR 填充 |
|---|---|---|---|
| `type=text` | `text_level=1` | heading | attrs.heading_level=1（2 级同理），content_ref=text_span（对齐 .md offset）或 inline_text |
| `type=text` | 无 text_level | paragraph | 同上 |
| `type=image` | — | figure | figures[]：asset_path←`img_path`（重写为 assets/images/ 下相对路径），caption←`img_caption` 拼接，bbox←`img_bbox` |
| `type=table` | — | table | tables[]：html←`table_body`，cells 从 HTML 解析（rowspan/colspan 提取），caption←`table_caption`，bbox←`table_bbox` |
| `type=equation` | — | formula | content_ref=inline_text（`text` 为 LaTeX），bbox←item bbox |
| `type=image`/`table` 的 caption 项 | `img_caption`/`table_caption` 独立项 | caption | attrs.caption_of←目标 block_id |
| `page_idx` | 每项 | page_no | `page_idx + 1` |
| `bbox` | 每项 | bbox | 坐标按 `coordinate_system` 声明原样记录（adapter 不做单位换算，只声明） |
| 页眉页脚 | middle.json `para_blocks` 中 page_header/page_footer 区 | header / footer | V1 可选；MinerU 版差异大，允许 `ir_source` 标注缺失 |

未覆盖项处理：未知 `type` 归并为 paragraph + `attrs.raw_type` 记录原值；缺 bbox → null 并在 stats.blocks_without_bbox 计数（不失败）。

### 6.2 DoclingAdapter（Office 路线，替代 MarkItDown 信息全损）— OQ-4=B，DECIDED 2026-10-16 不立项

评估结论（供 Owner 裁定，见 OQ-4）：

- **问题**：MarkItDown 对 docx/pptx/xlsx 只输出文本流，表格结构/图像/bbox 全损，无法生成有意义的 IR。
- **Docling** 输出 `DoclingDocument`（DocItem: title/section-header/text/table/picture/caption…，带 provenance bbox 与页号）→ 与本文 §3 schema 几乎一一对应，映射成本低；且支持 PDF+Office 双路线，长期可统一。
- **代价**：新依赖（docling 包 + 模型下载，~GB 级）；离线环境需解决模型分发；PPTX/XLSX 的 bbox 语义与 PDF 不完全一致（slide 坐标）。
- **V1 裁定（OQ-4=B，DECIDED 2026-10-16）**：Office 路线 IR 采用 `markitdown_text_only` 降级模式（整文单 paragraph 块 + heading 块，bbox 全 null），**DoclingAdapter 不立项**（留待单独评估），不阻塞 S2 主路径（MinerU PDF）。

### 6.3 VLM 兜底 → 同一 IR

适用：MinerU 失败/EMPTY、扫描件低质量、Office 降级模式之外需要图像理解时。

- 输入：页面图像（MinerU assets 或 PDF 渲染页）。
- 输出约束：VLM 必须以固定 JSON 模板返回 `{blocks: [{type, text, caption, bbox?}], tables: [{html}], figures: [{caption, ocr_text}]}`，经 schema 校验后由 adapter 归一为 IR；`confidence` 置 null，`provenance.ir_source=vlm_fallback`，`attrs` 增记 `vlm_model`。
- 红线：VLM 产物必须过同一 schema 校验器；校验失败则该页整页降级为单 figure 块（asset 引用页图），**不得产出半结构化脏块**。

---

## 7. 任务4（单据识别审核）预留：field_value 证据

不展开 S3 设计，仅定义承载格式，S2 落库格式先行冻结：

```json
{
  "evidence_type": "field_value",
  "chunk_uid": "<所属块 chunk>",
  "page_no": 1,
  "bbox": {"x0": 72.0, "y0": 88.0, "x1": 300.0, "y1": 104.0},
  "confidence": 92.50,
  "quote_text": "2026-09-30",
  "metadata": {
    "block_id": "b0031",
    "field_name": "invoice_date",
    "field_value": "2026-09-30",
    "field_type": "date",
    "extractor": "vlm_fallback",
    "candidate_values": []
  }
}
```

- `confidence` 用现有 `DECIMAL(5,2)` 列（存 0-100 分制，S3 统一定义）。
- field ↔ block 双回引：metadata.block_id 指向 IR 块，S3 的字段抽取器只消费 IR，不直接碰解析器原生输出。
- S2 阶段**不产生** field_value 证据，只保证 schema/表结构可容纳。

---

## 8. 迁移与影响清单（汇总）

| 影响面 | 变更 | 性质 |
|---|---|---|
| `parsed_dir` 产物 | 新增 `document_ir.json`；MinerU 路径新增 `ir_source/content_list.json`（+可选 middle.json） | additive |
| `parsed_text.md`（v2 文档） | **不变**（005 契约冻结，内容/hash 与 v1 一致）；渲染轨另落新增 `rendered.md`（OQ-5=B） | 无变更（仅新增产物） |
| `parse_manifest.json` | 增记 `document_ir_path`、`ir_source_files`、`parser_profile=*_v2` | additive 字段 |
| `kb_parsed_artifact` | 新 artifact_type=`document_ir` 登记 | 零迁移 |
| `kb_document_chunk` / `kb_evidence` | 新 chunk_type/evidence_type 值、metadata.block_id | 零迁移 |
| `search_service` | 无代码改动（V1） | 无 |
| `config` | 新增 `ir.image_quality_threshold` 等少量配置键 | additive |
| MinerU adapter | staging 保留 content_list.json；新增 IrBuilder | 实现期 |
| sql/migrations | V1 **零新增**（OQ-1=A：`block_id` 留 `metadata_json`，不升独立列；`017_block_id_column.sql` 候选**已按裁定废弃**，仅当 012 检索演进需按 block 过滤时再重新评估） | 无（V1）／待 012 评估 |

---

## 9. 开放问题（Owner 裁定项）— 已全部 DECIDED（2026-10-16）

> Owner 于 2026-10-16 裁定：OQ-1~7 全部采纳建议值。017 批准登记并进入实施，排期在 S1（014/015/016）之后（避免 `mineru_pdf_parser.py` 改动与 014 白名单冲突）。

| # | 问题 | 选项 | 建议 | 裁定（DECIDED 2026-10-16） |
|---|---|---|---|---|
| OQ-1 | `block_id` 是否升格为 `kb_document_chunk`/`kb_evidence` 独立列（加索引） | A. metadata JSON（零迁移）/ B. 加列（需 migration + DB Review） | V1 选 A；012 检索演进时评估 B | **DECIDED = A**：V1 留 metadata JSON，不升独立列 |
| OQ-2 | 存量 v1 文档是否强制重跑生成 IR | A. 双轨共存（v1 无 IR 走旧逻辑）/ B. 全量重跑 | 建议 A，重跑作为可选运维命令 | **DECIDED = A**：双轨共存，不强制重跑 |
| OQ-3 | 单元格级置信度是否建模 | A. 表级即可 / B. cells[].confidence | 建议 A（MinerU 不提供格级置信度，B 为空字段） | **DECIDED = A**：不建模单元格置信度 |
| OQ-4 | 是否立项 DoclingAdapter 替代 MarkItDown Office 路线 | A. 立项（新依赖、模型分发）/ B. Office 走 text_only 降级 IR / C. 两者并存 | 建议 B 先行，Docling 单独评估报告 | **DECIDED = B**：Office 先走降级 IR，Docling 不立项（留待单独评估） |
| OQ-5 | `parsed_text.md` 由 IR 渲染产出是否可接受（v2 文档文本可能与 v1 有差异） | A. 接受（渲染轨即权威文本）/ B. 保留解析器原文、渲染另存 `rendered.md` | 建议 B 更保守：005 契约的 parsed_text.md 语义是"解析器原文"，新增 `rendered.md` 不动旧语义 | **DECIDED = B**：新增 `rendered.md`，005 `parsed_text.md` 原文语义不动 |
| OQ-6 | header/footer 块默认剔除正文流 | A. 剔除+注释 / B. 保留原位 | 建议 A | **DECIDED = A**：剔除正文流 + 文末注释 |
| OQ-7 | bbox 坐标单位声明（pdf_point vs pixel）以 MinerU 实测为准，是否需要抽样实测验证 | A. 实现期抽样 3 篇实测 / B. 信任文档声明 | 建议 A | **DECIDED = A**：实现期抽 3 篇实测后冻结 `coordinate_system.unit` 声明 |

**裁定后需同步修订的章节**（已由 ir-architect 于 2026-10-16 同步落实，见下 ✓）：

1. ✓ 已落实 §4.3：v2 文档的 `parsed_text.md` **保持解析器原文**（OQ-5=B），渲染轨落 `rendered.md`；旧消费者零改动的前提由此保证。
2. ✓ 已落实 §5.2：`block_id` 一律走 `metadata_json.block_id`，删除"可选独立列"表述（OQ-1=A）。
3. ✓ 已落实 §6.2：DoclingAdapter 标记为「不立项，留待单独评估」，Office 路线仅 `markitdown_text_only` 降级 IR（OQ-4=B）。

## 10. 非目标（S2 G0 边界）

- 不实现任何代码（G0 纯设计）。
- 不改动 001–013 任何 DONE spec 的行为。
- 不引入向量/embedding/LLM 摘要。
- 不设计 S3 单据识别业务流程（仅冻结 field_value 承载格式）。
- 不决定 Docling 立项（仅评估，OQ-4）。
