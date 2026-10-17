# 017 Unified Document IR — spec.md（草案）

> Project: `pkb_sdd`
> Spec Directory: `specs/017-unified-document-ir/`
> Phase: `P1 Design Draft — G0 gate only`
> Status: `DRAFT / NOT APPROVED`
> Authority: `specs/SPEC_INDEX.md`（017 尚未登记，需 Owner 批准后加入）
> Design doc: `docs/architecture/DOCUMENT_IR_DESIGN_V1.md`（权威细节以设计文档为准）

---

## 1. Background

001–013 已完成：005/007 产出解析三件套（`parsed_text.md` / `parsed_metadata.json` / `parse_manifest.json`），010 构建 chunk + evidence。但三件套中**文本/表格/图像无块级关联**：

- MinerU 的结构化中间件（`content_list.json` / `middle.json`，含 bbox/page_idx/块类型）在 staging 清理时被丢弃；
- MarkItDown Office 路线表格/图像信息全损；
- 证据链只有 `section_quote` 一种类型，无法定位表格单元格或图像区域。

017 定义第 4 件产物 **`document_ir.json`**（统一文档中间表示，Document IR），使文本+表格+图像全面转化为 AI 可理解的块级结构化输入。

## 2. Problem Statement

1. 表格在 parsed_text.md 中被压平，行列/合并单元格结构丢失，无法生成 `table_cell` 证据。
2. 图像仅剩 assets 文件，无 caption/bbox/页号回链，AI 无法理解"图文对应"。
3. 无全局 reading order，跨页内容顺序不可靠。
4. 后续单据识别（任务4/S3）的 `field_value` 证据没有承载位置。

## 3. Goals

```text
G001 定义 document_ir.json schema v1：blocks[]（8 类块）+ tables[]（cells 矩阵含 rowspan/colspan）+ figures[]（asset/ocr/caption/quality）+ reading_order 全局序列。
G002 不动 005 三件套契约；parser_profile 升 *_v2 标识产物集差异，v1/v2 双轨共存。
G003 定义 IR → 双轨渲染规则：Markdown 轨（表格→HTML/MD，图→![caption](asset#block_id)）+ JSON 轨（IR 本身即权威轨）。
G004 证据链扩展：evidence_type 新增 table_cell / image_region / field_value（承载格式冻结，S2 不产生 field_value 数据）；chunk 经 metadata.block_id 回引 IR 块。
G005 V1 零 schema 迁移（block_id 走 metadata JSON；独立列与否交 P2 DB Review）。
G006 适配器路径定义：MinerU content_list → IR 映射表（主路径）；MarkItDown 走 text_only 降级 IR；VLM 兜底统一 schema 校验后归一。
G007 幂等可重建：同 sha256 + 同 adapter 版本 → IR 确定（时间戳除外）。
G008 fail-closed：缺 bbox 置 null 不伪造；VLM 产物不过校验则整页降级为 figure 块。
```

## 4. Non-goals

```text
NG001 不改动 001–013 任何 DONE spec 的运行行为（v1 profile 路径完全不变）。
NG002 不实现代码（本 spec 为 G0 设计门禁产物）。
NG003 不引入向量库 / embedding / LLM 摘要。
NG004 不设计 S3 单据识别审核业务流程（仅冻结 field_value 证据承载格式）。
NG005 不决定 DoclingAdapter 立项（仅评估结论，Owner 裁定 OQ-4）。
NG006 不强制存量 v1 文档重跑（OQ-2 裁定前保持双轨）。
NG007 不上传私有文档到外部服务（VLM 兜底仅限 Owner 批准的本地/内网模型）。
```

## 5. Contract Summary（详见设计文档 §3）

- 产物：`parsed/by_hash/{...}/{sha256}/document_ir.json`，`schema_version="document-ir/1.0"`。
- 块类型封闭枚举：`heading, paragraph, table, figure, formula, caption, header, footer`。
- 每块：`block_id / type / page_no / bbox / reading_order / confidence / content_ref`；content_ref 优先 span 回引 parsed_text.md offset。
- tables[]：`block_id` 回引 + `html` + `cells[]`（row/col/rowspan/colspan/is_header/text）+ caption_block_id。
- figures[]：`asset_path`（相对 parsed_dir）+ `asset_sha256` + `ocr_text` + `caption` + `image_quality_score` + bbox。
- 证据扩展：新 evidence_type 值复用 `VARCHAR(64)` 列，零迁移；`evidence_uid` v2 公式仅用于新类型，v1 section_quote 公式不变。

## 6. Adapter Paths

| 路径 | ir_source | 说明 |
|---|---|---|
| MinerU（主） | `mineru_content_list` | 复用 content_list.json（实现期需在 staging 清理前保留至 `parsed_dir/ir_source/`） |
| MarkItDown（降级） | `markitdown_text_only` | heading/paragraph 块，bbox 全 null |
| VLM（兜底） | `vlm_fallback` | 固定 JSON 模板 + schema 校验，失败整页降级 figure |
| Docling（评估） | `docling` | Office 路线候选，OQ-4 裁定 |

## 7. Open Questions（Owner 裁定，详见设计文档 §9）

OQ-1 block_id 是否升格独立列；OQ-2 存量重跑与否；OQ-3 格级置信度；OQ-4 Docling 立项；OQ-5 parsed_text.md 由渲染产出 vs 新增 rendered.md；OQ-6 header/footer 剔除策略；OQ-7 bbox 单位实测抽样。

## 8. P1 STOP Condition

本 spec 与 `docs/architecture/DOCUMENT_IR_DESIGN_V1.md` 即 G0 交付。未经 Owner 批准与 SPEC_INDEX 登记，不进入 P2/P4。
