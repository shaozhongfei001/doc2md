# Plan: 统一文档中间表示 Document IR（017 实现计划）

> **项目**：`doc2md`（个人历史项目文档知识库）
> **项目路径**：`/home/szf/dev/doc2md`
> **版本基线**：V1.1-SDD / Loop S2
> **编写日期**：2026-10-16
> **前置条件**：001–013、902 已完成；S1（014/015/016）完成后启动（`mineru_pdf_parser.py` 白名单冲突规避）；Owner 已裁定 OQ-1~7（见 `docs/architecture/DOCUMENT_IR_DESIGN_V1.md` §9，全部 DECIDED）
> **编写角色**：IR Architect（ir-architect）— Loop S2 G0
> **建议分支**：`feature/017-unified-document-ir`
> **权威设计**：`docs/architecture/DOCUMENT_IR_DESIGN_V1.md`（schema/映射表/渲染规则以该文档为准）

---

## 1. 背景

见 `spec.md` §1–2 与设计文档 §1。核心缺口一句话：MinerU 已产出带 bbox/块类型的 `content_list.json`，但 `MineruPdfParserService._finalize_success_from_staging` 只保留 `.md` + 图片，结构化中间件随 staging 删除而丢失；文本/表格/图像无块级关联，证据链只有 `section_quote`。

017 交付：第 4 件产物 `document_ir.json` + 渲染轨 `rendered.md` + 证据链扩展（`table_cell` / `image_region`），全程 additive-only、零 schema 迁移。

## 2. 输入数据

### 2.1 文件系统（只读，新增开口）

| 文件 | 017 操作 | 用途 |
|------|----------|------|
| `parsed/.../ir_source/content_list.json` | 只读 | MinerU → IR 主映射源 |
| `parsed/.../ir_source/middle.json` | 只读（可选） | span 置信度、header/footer 判别 |
| `parsed/.../parsed_text.md` | 只读 | content_ref span 回引 offset；MarkItDown 降级 IR 源 |
| `parsed/.../parse_manifest.json` | 只读 | `asset_files[].sha256` 对齐校验、parser 身份 |
| `parsed/.../assets/images/*` | 只读 | figures[] asset 存在性与 sha256 校验 |

### 2.2 MySQL

| 表 | 操作 |
|----|------|
| `kb_parse_result` / `kb_document` / `kb_file_content` | SELECT（候选加载，模式对齐 010） |
| `kb_document_chunk` / `kb_evidence` | INSERT/UPSERT（复用 010 幂等 upsert 模式） |
| `kb_parsed_artifact` | INSERT（新 `artifact_type='document_ir'` / `'rendered_md'` 登记，零迁移） |

### 2.3 配置

`config/document_ir.yaml`（新文件，缺失用默认值）：

```yaml
document_ir:
  enabled: true
  image_quality_threshold: 0.5      # 低于此值渲染轨降级为 caption-only 占位
  render:
    header_footer: "annotate"        # annotate=剔除正文流+文末注释（OQ-6）；保留选项 "inline"
    table_format: "html_prefer"      # 首选 table.html；缺失时 cells → MD pipe 降级
  mineru:
    ir_source_dir: "ir_source"       # parsed_dir 下相对目录
  vlm_fallback:
    enabled: false                   # 默认关闭；开启需 Owner 批准的本地/内网模型
```

`DocumentIrConfig` dataclass 承载，服务构造函数可注入（测试传内存配置）。

## 3. 输出数据

### 3.1 磁盘产物（additive）

```text
parsed/by_hash/{sha256[:2]}/{sha256[2:4]}/{sha256}/
  parsed_text.md            # 不动（OQ-5=B，解析器原文语义）
  parsed_metadata.json      # 不动
  parse_manifest.json       # additive 字段：document_ir_path / rendered_md_path / ir_source_files
  document_ir.json          # 新增，schema_version="document-ir/1.0"
  rendered.md               # 新增，由 IR 确定性渲染
  ir_source/                # 新增（MinerU 路径）：content_list.json [+ middle.json]
```

### 3.2 解析器侧变更（唯一前置改动）

`MineruPdfParserService`：staging 清理前将 `content_list.json`（必需）与 `middle.json`（可选）拷入 `parsed_dir/ir_source/`，manifest 增记 `ir_source_files`。`parser_profile` 升 `mineru_ir_v2`；旧 `mineru_default_v1` 产物无 ir_source → IR 构建跳过该候选（不报错，记 `NO_IR_SOURCE` 跳过原因）。

**排期约束**：此改动与 014 共享 `mineru_pdf_parser.py` 白名单，017 P4 必须在 S1 合并后启动（Owner 已裁定）。

### 3.3 证据链扩展输出

- 新 `chunk_type`：`table` / `figure`；新 `chunk_level`：`block`；`metadata_json.block_id`（OQ-1=A，不升独立列）。
- 新 `evidence_type`：`table_cell`（locator = `metadata.block_id` + `metadata.cell={row,col,rowspan,colspan}`）、`image_region`（locator = `metadata.block_id` + `metadata.figure_id` + `metadata.asset_path`）。
- `field_value`：**S2 只冻结承载格式，不产生任何数据行**（验收含否定断言）。
- `evidence_uid` v2 公式仅用于新类型；v1 `section_quote` 公式不变。

## 4. 模块设计

### 4.1 新增文件

```text
backend/app/services/document_ir_schema.py   # IR dataclass + 校验器（封闭枚举、bbox 结构、cells 矩阵合法性）
backend/app/services/document_ir_builder.py  # MinerU content_list → IR；MarkItDown 降级 IR；block_id/reading_order 分配
backend/app/services/ir_renderer.py          # IR → rendered.md（纯函数，无 IO 副作用之外的写）
backend/app/services/document_ir_service.py  # 批处理编排：候选 → 构建 → 校验 → 落盘 → artifact 登记
backend/app/services/ir_vlm_fallback.py      # VLM 产物 schema 校验 + 整页降级规则（默认禁用，见 §5.4）
```

### 4.2 修改文件

| 文件 | 变更 |
|------|------|
| `backend/app/services/mineru_pdf_parser.py` | 仅 additive：staging 保留 ir_source + manifest 字段 + profile 常量升 v2（不改既有 v1 行为分支） |
| `backend/app/services/evidence_chain.py` | 新增 `_chunk_from_ir()` 路径：`document_ir.json` 存在时按块聚合 chunk + 产 `table_cell`/`image_region` 证据；不存在时**完全走现有路径** |
| `backend/app/core/parsed_paths.py` | `ParsedArtifactPaths` 增 `document_ir` / `rendered_md` / `ir_source` 键（additive） |
| `backend/app/cli/main.py` | 注册 `build-document-ir`（`--config/--content-uid/--sha256/--limit/--dry-run/--force`） |

### 4.3 MinerU → IR 映射（实现按设计文档 §6.1 映射表逐行落实）

实现要点：

1. `block_id` = `b` + 4 位序号，按 reading_order 分配，跨重跑稳定（禁用随机 UUID）。
2. 未知 `type` → 归并 paragraph + `attrs.raw_type` 记录原值；缺 bbox → null 且 `stats.blocks_without_bbox` 计数（**不得伪造 bbox**）。
3. `img_path` 重写为 `assets/images/` 相对路径；`asset_sha256` 与 `parse_manifest.asset_files` 对齐，**不一致则该校验失败、候选 FAILED**（fail-closed）。
4. `table_body` HTML → cells 矩阵：解析 rowspan/colspan，虚格不入 cells；HTML ↔ cells 双向可重建。
5. caption 关联：`img_caption`/`table_caption` → caption 块 + `attrs.caption_of` 回引。
6. `coordinate_system.unit`：实现期抽 3 篇真实 MinerU 产物实测后冻结（OQ-7=A），实测记录附 P6 E2E 报告。

### 4.4 MarkItDown 降级 IR

整文按 markdown heading 切分：heading 块 + paragraph 块，`bbox=null`、`page_no=null`、`confidence=null`、`ir_source="markitdown_text_only"`。Office 路线 Docling 不立项（OQ-4=B）。

### 4.5 证据链扩展（`evidence_chain.py`）

```text
候选处理时探测 document_ir.json：
  存在 → _chunk_from_ir()：
    paragraph 相邻合并至 token 上限（chunk_type=text, chunk_level=block）
    table/figure 每块一 chunk（chunk_type=table/figure）
    证据：section_quote（文本块 span 回引）+ table_cell（逐 cell）+ image_region（逐 figure）
  不存在 → 现有 _chunk_markitdown / _chunk_mineru 页级路径（零行为变化）
```

## 5. 关键设计决策

### 5.1 D-1 渲染轨与解析原文分离（OQ-5=B）

`rendered.md` 由 IR 确定性渲染；`parsed_text.md` 保持解析器原文。010/012 既有消费者零改动；新旧文本差异风险隔离在新增文件中。

### 5.2 D-2 缺数据不编造（反假绿核心）

1. 无 bbox → `bbox=null` 并计数，**绝不**估算填充；
2. 无 caption → `caption=null`，渲染用 figure_id 占位；
3. `confidence`/`ocr_text`/`image_quality_score` 不可得 → null；
4. `stats.*` 必须由实际 blocks 重算得出，不得硬编码。

### 5.3 D-3 校验 fail-closed

IR 落盘前过 `document_ir_schema.py` 校验器：枚举封闭、reading_order 与 blocks 一一对应、cells 坐标无越界/无重叠、asset sha256 对齐、caption_of 指向存在块。任一失败 → 候选 FAILED、不落盘半成品 IR。

### 5.4 D-4 VLM 兜底默认禁用 + Mock 诚实性

`vlm_fallback.enabled` 默认 false。开启时产物必须过同一 schema 校验；校验失败 → 该页整页降级为单 figure 块（asset 引用页图）。测试用 MockVlmClient 时，**mock 文本绝不允许不经校验落盘**（沿用 015 A015-06 的 Mock 诚实性变异标准）。

### 5.5 D-5 零 schema 迁移

017 不新增 `sql/migrations/**`。block_id 走 metadata JSON（OQ-1=A）；新枚举值复用 VARCHAR 列。若 P2 发现任何必须迁移项 → STOP，回报 Owner。

## 6. Dev 实现边界

### 6.1 白名单

| 操作 | 文件 |
|------|------|
| 新增 | `backend/app/services/document_ir_schema.py` / `document_ir_builder.py` / `ir_renderer.py` / `document_ir_service.py` / `ir_vlm_fallback.py` |
| 修改 | `backend/app/services/mineru_pdf_parser.py`（仅 §3.2 additive 变更，S1 合并后启动） |
| 修改 | `backend/app/services/evidence_chain.py`（仅 §4.5 新增路径） |
| 修改 | `backend/app/core/parsed_paths.py`（additive 键） |
| 修改 | `backend/app/cli/main.py`（注册 `build-document-ir`） |
| 新增 | `config/document_ir.yaml` |
| 新增 | `backend/tests/test_document_ir.py` / `test_ir_renderer.py` / `test_evidence_chain_ir.py` + `backend/tests/fixtures/document_ir_*/` |

### 6.2 禁止修改

```text
sql/**（任何迁移）
backend/app/models/**
backend/app/services/parse_registry.py 写路径（artifact 登记仅复用既有 register 流程）
backend/app/services/markitdown_parser.py / parse_quality_checker.py / image_parser.py
backend/app/core/parser_routing.py
raw_vault/**  parsed/ 下真实产物  data/**
specs/001..016/**（封版/进行中）  specs/902/**
parsed_text.md / parsed_metadata.json 既有语义（只读消费）
```

### 6.3 测试先行与 fixture 策略

先写测试（红）→ 实现 → 绿。fixture 全部现场构造：手工编写 `content_list.json`（含 heading/paragraph/image/table/equation/caption/header/footer 全覆盖、含 rowspan/colspan 表、含缺 bbox 块）、`parsed_text.md`、`parse_manifest.json`（含 asset_files sha256）、Pillow 现场生成图片。直接向测试 DB 插入 registry 行（对齐 `test_evidence_chain.py` 既有模式）。

## 7. 异常处理设计

| 场景 | 处理 |
|------|------|
| `ir_source/content_list.json` 缺失（v1 产物） | 跳过，记 `NO_IR_SOURCE`，**不 FAILED** |
| content_list JSON 非法 | 候选 FAILED，错误入报告，批处理继续 |
| asset sha256 与 manifest 不对齐 | 校验失败，候选 FAILED，IR 不落盘（fail-closed） |
| `table_body` HTML 解析失败 | 该表降级：cells 为空 + `html` 原样保留 + `stats` 计数；不中断文档 |
| 缺 bbox / caption / confidence | null 填充 + stats 计数（D-2），不失败 |
| VLM 产物不过 schema 校验 | 整页降级单 figure 块（D-4） |
| 单候选异常 | 候选级容错，批处理不中断 |
| 中文路径 | 全链路 UTF-8，报告 `ensure_ascii=False` |

## 8. TL 决策表（Dev 必遵）

| # | 问题 | 决策 |
|---|------|------|
| Q1 | block_id 分配 | `b`+序号按 reading_order，禁随机 |
| Q2 | 缺 bbox | null + stats 计数，禁止伪造 |
| Q3 | asset 校验 | sha256 不对齐 → fail-closed FAILED |
| Q4 | header/footer | 剔除正文流 + 文末注释（OQ-6） |
| Q5 | v1 产物 | `NO_IR_SOURCE` 跳过，证据链走旧路径 |
| Q6 | field_value | S2 零数据行，仅格式冻结 |
| Q7 | VLM | 默认禁用；mock 文本不过校验不落盘 |
| Q8 | sql 迁移 | 零迁移；P2 发现必要项 → STOP 报 Owner |
| Q9 | parsed_text.md | 不动原文语义，渲染落 rendered.md（OQ-5） |
| Q10 | bbox 单位 | 抽 3 篇实测后冻结声明（OQ-7） |

## 9. Acceptance Criteria 摘要

见 `acceptance.md`（A017-01 ~ A017-12，否定式断言 + 变异复验）。

## 10. 流水线与 STOP 点

```text
G0 规格+设计（本批）→ Owner 裁定（已完成 2026-10-16）→ [等待 S1 合并]
→ P2 DB Review（确认零迁移 + 写入范围）→ P4 Dev（测试先行）→ STOP
→ P5 QA → P6 E2E（含 OQ-7 bbox 实测记录）→ P7 TL Final Review
```

---

**Plan 结束** — Dev 先读 `acceptance.md`、`docs/architecture/DOCUMENT_IR_DESIGN_V1.md` 与 §6 白名单再写代码。
