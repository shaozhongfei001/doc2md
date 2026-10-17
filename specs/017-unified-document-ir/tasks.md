# Tasks: 统一文档中间表示 Document IR（017）

> **Spec**：`specs/017-unified-document-ir/`
> **分支**：`feature/017-unified-document-ir`
> **Plan**：`plan.md`（IR Architect G0 已落地；Owner 已裁定 OQ-1 ~ OQ-7 全部 DECIDED 2026-10-16，见设计文档 §9）
> **权威设计**：`docs/architecture/DOCUMENT_IR_DESIGN_V1.md`（schema / 映射表 / 渲染规则以该文档为准）
> **Dev 必读顺序**：本文件 → `plan.md` → `spec.md` → `acceptance.md` → `test_cases.md` → 设计文档 §3/§4/§5/§6.1
> **排期硬前置**：017 P4 必须在 S1（014/015/016）**合并后**启动 —— `mineru_pdf_parser.py` 白名单与 014 §7.4 冲突（Owner 2026-10-16 裁定，见「白名单冲突约束」）。

---

## Dev 文件白名单（全局）

**允许新增**：

```text
backend/app/services/document_ir_schema.py       # IR dataclass + 校验器
backend/app/services/document_ir_builder.py      # MinerU content_list → IR；MarkItDown 降级 IR；block_id 分配
backend/app/services/ir_renderer.py              # IR → rendered.md（纯函数）
backend/app/services/document_ir_service.py      # 批处理编排：候选 → 构建 → 校验 → 落盘 → artifact 登记
backend/app/services/ir_vlm_fallback.py          # VLM 产物 schema 校验 + 整页降级（默认禁用）
config/document_ir.yaml
backend/tests/test_document_ir.py
backend/tests/test_ir_renderer.py
backend/tests/test_evidence_chain_ir.py
backend/tests/fixtures/document_ir_*/            # 现场构造（tmp_path 为主）
```

**允许修改（仅下列范围）**：

```text
backend/app/services/mineru_pdf_parser.py   # 仅 plan §3.2 additive：staging 保留 ir_source + manifest 字段 + profile 升 *_v2（S1 合并后启动）
backend/app/services/evidence_chain.py      # 仅 plan §4.5：新增 _chunk_from_ir() 路径；IR 缺位时零行为变化
backend/app/core/parsed_paths.py            # 增 document_ir / rendered_md / ir_source 键（additive）
backend/app/cli/main.py                     # 注册 build-document-ir（--config/--content-uid/--sha256/--limit/--dry-run/--force）
specs/017-unified-document-ir/tasks.md      # 勾选
```

**禁止修改**：

```text
sql/**（任何迁移）   backend/app/models/**   generated/**
backend/app/services/parse_registry.py 写路径（artifact 登记仅复用既有 register 流程）
backend/app/services/markitdown_parser.py  parse_quality_checker.py  parse_quality_report_summarizer.py
backend/app/services/image_parser.py  backend/app/core/parser_routing.py
backend/app/services/inventory_scanner.py  file_content_vault.py  duplicate_governance.py
backend/app/core/pdf_sniffing.py  backend/app/core/config.py        # 014 范围
raw_vault/**（只读）  parsed/** 真实产物  curated/**  quarantine/**  data/**
config/app.example.yaml  config/parser_rules.yaml  config/pdf_sniffing.yaml  config/quality_semantics.yaml
specs/001..016/**（封版/进行中）  specs/902/**
parsed_text.md / parsed_metadata.json 既有语义（只读消费，零改动）
specs/017-unified-document-ir/plan.md  spec.md  acceptance.md       # Dev 不改 Plan/Spec/Acceptance
```

---

## 全局硬约束（T001–T013 均适用）

1. **005 契约冻结（OQ-5=B）**：`parsed_text.md` 内容与 hash **零改动**（`rendered.md` 是新产物，不得覆盖 `parsed_text.md`）；`parsed_metadata.json` 不动；`parse_manifest.json` 仅 additive 字段。
2. **V1 零 schema 迁移（OQ-1=A）**：不新增 `sql/migrations/**`；`block_id` 存 `metadata_json.block_id`（不升独立列）；新枚举值复用既有 VARCHAR 列。若 P2 发现必须迁移项 → **STOP 报 Owner**。
3. **只读红线**：`raw_vault/**`、`parsed/**` 真实产物只读；017 仅在既有 `parsed_dir` 内 additive 落盘（`document_ir.json` / `rendered.md` / `ir_source/`）。
4. **D-2 缺数据不编造（反假绿核心）**：缺 `bbox` → `null` + `stats.blocks_without_bbox` 计数，**绝不**估算填充；缺 `caption`/`confidence`/`ocr_text`/`image_quality_score` → `null`；`stats.*` **必须由实际 blocks 重算**，不得硬编码。
5. **D-3 校验 fail-closed**：IR 落盘前过 `document_ir_schema` 校验器（封闭枚举 / 必填键 / reading_order 置换 / cells 无越界无重叠 / asset sha256 对齐 / caption_of 指向存在块）。任一失败 → 候选 `FAILED`，**不落盘半成品 IR**。
6. **幂等**：同 sha256 + 同 adapter 版本 → IR 与 `rendered.md` 确定（仅时间戳可差异）；重复 `build-document-ir` 不重写产物、artifact 登记幂等（复用 010 upsert 模式）。
7. **dry-run 零写入**：`parsed/` 无新增文件、`kb_parsed_artifact` 无新行、raw_vault 不变。
8. **单候选容错**：单候选异常记 `errors[]`，批处理不中断；`NO_IR_SOURCE`（v1 产物/无 `ir_source`）属"跳过"**非** `FAILED`，两者语义严格区分。
9. **VLM 默认禁用 + Mock 诚实性**：`vlm_fallback.enabled` 默认 `false`；启用时产物必须过同一 schema 校验，失败整页降级单 figure 块；`MockVlmClient` 文本**绝不允许不经校验落盘**（沿用 015 A015-06 标准）。
10. **测试先行**：先写测试（红）→ 实现 → 绿。fixture 全部**现场构造**（手工 `content_list.json` 覆盖 heading/paragraph/image/table/equation/caption/header/footer + rowspan/colspan 表 + 缺 bbox 块 + 未知 type；Pillow 现场生成图片），**不提交二进制 fixture**；测试 DB 直接插 registry 行（对齐 `test_evidence_chain.py` 既有模式）。
11. **实现前必读** `plan.md` §8 TL 决策表（Q1–Q10）与设计文档 §3/§4/§6.1 映射表。

---

## ⚠ 白名单冲突约束（T009 关键前置）

`backend/app/services/mineru_pdf_parser.py` 是 **014 与 017 的共享白名单文件**：

- **014**（`specs/014-pdf-content-sniffing/tasks.md` T006）已改该文件 `_build_plan`（嗅探注入）；
- **017** T009 需改该文件 `_finalize_success_from_staging`（staging 保留 `ir_source/` + manifest 字段 + `parser_profile` 升 `mineru_ir_v2`）。

**裁定**（Owner 2026-10-16）：017 P4 必须在 S1 **全部合并后**启动；T009 只允许 §3.2 的 additive 变更，**不得**回改 014 引入的嗅探逻辑（`_build_plan` / `IN_SCOPE_ROUTE_TYPES` 行为冻结）。同一函数区域冲突 → **STOP 报 Owner**，不自行改写 014 代码。

---

## T001 阅读 005/007/010/006 代码与本 Spec

- [ ] 已读 `mineru_pdf_parser.py`（`_finalize_success_from_staging` staging 清理段与 manifest 组装）
- [ ] 已读 `evidence_chain.py`（`_chunk_markitdown` / `_chunk_mineru` 页级路径、chunk/evidence upsert 幂等模式）
- [ ] 已读 `core/parsed_paths.py`（`ParsedArtifactPaths` 键约定）与 `cli/main.py` 命令注册风格
- [ ] 已读 `backend/tests/test_evidence_chain.py` DB fixture 模式
- [ ] 已读 `plan.md` 全文、`acceptance.md` A017-01 ~ A017-12、设计文档 §3/§4/§5/§6.1

---

## T002 测试先行：三测试文件骨架 + fixture 构造器（红）

**允许修改范围**：`backend/tests/test_document_ir.py` / `test_ir_renderer.py` / `test_evidence_chain_ir.py` / `backend/tests/fixtures/document_ir_*/`（新增）

- [ ] `content_list.json` 构造器：text/text_level、image(+img_caption)、table(+table_body 含 rowspan/colspan, +table_caption)、equation、page header/footer、**未知 type**、**缺 bbox 块** 全覆盖
- [ ] `parsed_text.md` + `parse_manifest.json`（含 `asset_files[].sha256`）构造器；Pillow 现场生成图片资产
- [ ] 覆盖 A017-01 ~ A017-12 全部断言（此时全红）
- [ ] 每个 fail-closed 分叉（A017-01/02/03/06/08/10）**已写好变异复验用例骨架**（变异→FAIL、恢复→PASS）
- [ ] 否定断言显式存在（如 `assert all(b["type"] in BLOCK_TYPES for b in blocks)`、`assert "field_value" not in produced_types`）

---

## T003 `document_ir_schema.py`：IR dataclass + 校验器

- [ ] `BLOCK_TYPES = {heading, paragraph, table, figure, formula, caption, header, footer}` 封闭常量；枚举外值 → 校验失败
- [ ] 每块必填键 `block_id/type/page_no/bbox/reading_order/confidence/content_ref` 全在（缺键失败；值为 `null` 合法）
- [ ] `reading_order` 为 `0..len(blocks)-1` 置换（无重号/无越界/无空洞）
- [ ] `coordinate_system.unit` 非空且 ∈ `{pdf_point, pixel}`，否则失败
- [ ] cells 坐标无越界/无重叠/虚格不入 cells；`caption_of`/`caption_block_id` 必须指向存在块
- [ ] asset sha256 与 manifest 不对齐 → 校验失败（fail-closed）
- [ ] 校验器为纯函数，不写盘、不读 DB

---

## T004 `document_ir_builder.py`：MinerU content_list → IR

- [ ] `block_id = "b" + 4 位序号`，**按 reading_order 分配、禁随机 UUID**，跨重跑稳定
- [ ] `type=text` + `text_level` → heading（`attrs.heading_level`）；无 text_level → paragraph
- [ ] `type=image` → figure（`asset_path` ← `img_path` 重写为 `assets/images/` 相对路径；`caption` ← `img_caption`；`bbox` ← `img_bbox`）
- [ ] `type=table` → table（`html` ← `table_body`；cells 由 HTML 解析提取 rowspan/colspan；caption ← `table_caption`）
- [ ] `type=equation` → formula（`content_ref=inline_text`，text 为 LaTeX）
- [ ] 未知 `type` → 归并 paragraph + `attrs.raw_type` 记录原值（**不得**透传为块类型、**不得**丢弃该块）
- [ ] 缺 bbox → `null` + `stats.blocks_without_bbox` 计数（**不得伪造 bbox**）
- [ ] caption 关联：`img_caption`/`table_caption` → caption 块 + `attrs.caption_of` 回引
- [ ] `page_no = page_idx + 1`；`content_ref` 优先 `text_span` 回引 `parsed_text.md` offset
- [ ] `stats.*` 由实际 blocks 重算（block_count/table_count/figure_count/blocks_without_bbox/blocks_low_confidence）

---

## T005 MarkItDown 降级 IR + coordinate_system 声明

- [ ] 整文按 markdown heading 切分：heading 块 + paragraph 块
- [ ] `bbox=null` / `page_no=null` / `confidence=null` / `provenance.ir_source="markitdown_text_only"`
- [ ] `coordinate_system` 仍显式声明（`unit` 非空，不得留空键）
- [ ] 对照：无 heading 文档 → 全 paragraph，零 issue
- [ ] 不引入 docling 依赖、不调用任何外部模型（OQ-4=B）

---

## T006 `ir_renderer.py`：IR → rendered.md（含三条降级路径）

- [ ] heading → `#{level}`，缺 level 用 `##`；paragraph → 原文；formula → `$$…$$`
- [ ] table：`table_format="html_prefer"` 首选 `table.html`；**降级路径 1**：html 缺失但 cells 完整 → cells → MD pipe 渲染，内容不丢
- [ ] figure：`![{caption}]({asset_path}#block_id={block_id})`；无 caption 用 figure_id；**降级路径 2**：asset 缺失 → figure_id 占位，不产空链接
- [ ] **降级路径 3**：`image_quality_score < image_quality_threshold` → caption-only 占位（不嵌图），标 `degraded: true`
- [ ] header/footer 默认剔除正文流 + 文末注释（`render.header_footer="annotate"`）
- [ ] caption 依附 `caption_of` 目标渲染，不单独成段；表前插锚点 `<!-- ir:block=… table=… -->`
- [ ] **绝不写 `parsed_text.md`**（OQ-5=B）；纯函数，除目标文件外零副作用

---

## T007 `document_ir_service.py`：批处理编排 + 落盘 + artifact 登记

- [ ] 候选加载模式对齐 010（`kb_parse_result` / `kb_document` / `kb_file_content` SELECT）
- [ ] 校验失败 → 候选 `FAILED`、`document_ir.json` **不落盘**、`errors[]` 含 sha256 与原因、批处理继续
- [ ] 落盘 `document_ir.json` + `rendered.md`（+ MinerU 路径 `ir_source/`）；manifest 增记 `document_ir_path`/`rendered_md_path`/`ir_source_files`
- [ ] artifact 登记：新 `artifact_type='document_ir'`/`'rendered_md'`，复用既有 register 流程（不改 `parse_registry.py` 写路径）
- [ ] 幂等：同 sha256 二次运行不重写、登记无重复行；`--force` → 重建并更新登记
- [ ] `--dry-run` 零写入：`parsed/` 无新增、artifact 无新行、raw_vault 不变；stdout `would_build`/`would_skip`
- [ ] 单候选异常隔离：批处理不中断；中文路径全链路 UTF-8

---

## T008 `ir_vlm_fallback.py`：VLM 产物校验 + 整页降级

- [ ] `vlm_fallback.enabled=false`（默认）→ VLM 路径零调用（monkeypatch 客户端"被调用即 fail"仍通过）
- [ ] 启用时：VLM 产物**必须**过同一 `document_ir_schema` 校验
- [ ] 校验失败 → 该页**整页降级为单个 figure 块**（asset 引用页图），**不得**产出部分脏块
- [ ] `confidence=null`、`provenance.ir_source="vlm_fallback"`、`attrs.vlm_model` 记录
- [ ] `MockVlmClient` 返回伪造非空文本 → 管线**不得**写出含该文本的 IR/渲染产物
- [ ] 红线：VLM 仅限 Owner 批准的本地/内网模型，不上传私有文档到外部服务

---

## T009 `mineru_pdf_parser.py`：ir_source 保留 + profile 升 v2（S1 合并后启动）

**前置**：014/015/016 已合并；确认不与 014 `_build_plan` 嗅探逻辑冲突，冲突则 STOP 报 Owner（见「白名单冲突约束」）。

- [ ] staging 清理前将 `content_list.json`（必需）与 `middle.json`（可选）拷入 `parsed_dir/ir_source/`
- [ ] `parse_manifest.json` 增记 `ir_source_files`（additive）；`parser_profile` 升 `mineru_ir_v2`
- [ ] 既有 v1 产物字段与 `parsed_text.md` **字节不变**
- [ ] 旧 `mineru_default_v1` 产物无 `ir_source/` → 候选记 `NO_IR_SOURCE` **跳过**（**不得** `FAILED`、**不得**报错中断）
- [ ] `test_mineru_pdf_parser.py` 既有用例**零修改**通过

---

## T010 `parsed_paths.py` additive 键 + CLI `build-document-ir`

- [ ] `ParsedArtifactPaths` 增 `document_ir`/`rendered_md`/`ir_source` 键（additive，既有键不变）
- [ ] `cli/main.py` 注册 `build-document-ir`，选项 `--config/--content-uid/--sha256/--limit/--dry-run/--force`
- [ ] Rich 汇总输出候选状态计数（built/skipped/failed）与 `NO_IR_SOURCE` 跳过原因
- [ ] 报告 JSON `ensure_ascii=False`，中文无乱码；`--help` 可用

---

## T011 `evidence_chain.py`：`_chunk_from_ir()` 路径 + 证据扩展

- [ ] 探测 `document_ir.json`：存在 → `_chunk_from_ir()`；不存在 → **完全走现有** `_chunk_markitdown`/`_chunk_mineru` 页级路径（零行为变化）
- [ ] paragraph 相邻合并至 token 上限（`chunk_type=text`, `chunk_level=block`）；table/figure 每块一 chunk（`chunk_type=table`/`figure`）
- [ ] `metadata_json.block_id` 回引 IR 块；`metadata.block_ids[]` 承载 chunk ↔ block 的 N:1
- [ ] 证据：文本块产 `section_quote`（span 回引）、表格逐 cell 产 `table_cell`（locator = `block_id` + `cell={row,col,rowspan,colspan}`）、图逐 figure 产 `image_region`（locator = `block_id` + `figure_id` + `asset_path`）
- [ ] `evidence_uid` **v2 公式仅用于新类型**；v1 `section_quote` 公式与既有 uid 值**零变化**
- [ ] **否定**：`field_value` 证据类型**零数据行**（计数为 0，仅格式冻结）
- [ ] **否定**：`kb_document_chunk`/`kb_evidence` 无新增列（零 migration）

---

## T012 反假绿变异复验 + 红线验证

- [ ] 变异复验（5/5 必须被捕获，缺一不通过）：① 注释掉 schema 校验直接落盘 → FAIL（A017-07）；② `MockVlmClient` 伪造文本 → 不得落盘（A017-07）；③ `stats.*` 硬编码 → FAIL（A017-12）；④ 缺 bbox 用估算填充 → FAIL（A017-12）；⑤ 校验器放宽（允许重复 reading_order / 偏移 offset / 枚举外 type 透传）→ FAIL（A017-01/02/03）
- [ ] 恢复后全绿
- [ ] 红线：执行前后 `parsed_text.md` + `parsed_metadata.json` 内容与 hash 完全不变；`raw_vault/**` listing + sha256 不变
- [ ] 零迁移硬断言：`sql/migrations/**` 无新增文件（git status 校验）
- [ ] 中文路径：`中文路径` 下 `content_list.json`/产物全链路 UTF-8 通过；报告 `ensure_ascii=False` 无乱码；`asset_path` 相对路径可定位
- [ ] `middle.json` 可选路径：缺失时不失败，header/footer 判别降级为类型判定（OQ-6 剔除正文流 + 文末注释仍生效）

---

## T013 回归与 STOP

**禁止事项**：Dev **不得**自我宣布 A017-01 ~ A017-12 通过；**不得**写 handoff；**不得** merge main。

- [ ] `pytest -q tests/test_document_ir.py tests/test_ir_renderer.py tests/test_evidence_chain_ir.py` 全绿
- [ ] 既有测试**零修改**通过：`test_mineru_pdf_parser.py`、`test_markitdown_parser.py`、`test_evidence_chain.py`、`test_parser_router.py`
- [ ] 001–016 全量回归通过；017 不改 001–016 任何已完成契约
- [ ] 边界审查：无 `magic-pdf`/`markitdown` 新增调用；无 `sql/**` 写；无 raw_vault 写
- [ ] Dev 输出：修改文件清单、pytest 命令、CLI 命令、遗留问题 → STOP → P2 DB Review → QA → E2E（含 OQ-7 bbox 单位 3 篇实测记录）→ TL Final Review

---

## 任务进度总览

| Task | 说明 | 主要文件 | 状态 |
|------|------|----------|------|
| T001 | 阅读 005/007/010/006 与规格 | — | [ ] |
| T002 | 测试先行（三测试骨架 + fixture 红测） | `tests/test_document_ir.py` / `test_ir_renderer.py` / `test_evidence_chain_ir.py` + `fixtures/document_ir_*/` | [ ] |
| T003 | IR dataclass + 校验器 | `document_ir_schema.py` | [ ] |
| T004 | MinerU content_list → IR 主映射 | `document_ir_builder.py` | [ ] |
| T005 | MarkItDown 降级 IR + 坐标声明 | `document_ir_builder.py` | [ ] |
| T006 | rendered.md 渲染 + 三降级路径 | `ir_renderer.py` | [ ] |
| T007 | 批处理编排 + 落盘 + artifact 登记 | `document_ir_service.py` | [ ] |
| T008 | VLM 校验 + 整页降级（默认禁用） | `ir_vlm_fallback.py` | [ ] |
| T009 | ir_source 保留 + profile 升 v2（S1 后） | `mineru_pdf_parser.py` | [ ] |
| T010 | 路径键 + CLI build-document-ir | `core/parsed_paths.py` / `cli/main.py` / `config/document_ir.yaml` | [ ] |
| T011 | `_chunk_from_ir()` + 证据扩展 | `evidence_chain.py` | [ ] |
| T012 | 反假绿变异复验 + 红线 | 全部 | [ ] |
| T013 | 回归与 STOP | — | [ ] |

---

**Tasks 结束** — Dev 从 T001 开始，严格在白名单内实现；T009 必须等 S1 合并后启动。
