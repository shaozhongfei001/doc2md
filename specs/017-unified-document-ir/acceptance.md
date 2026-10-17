# Acceptance: 统一文档中间表示 Document IR（017）

> **Spec**：`specs/017-unified-document-ir/`
> **Plan 对照**：`plan.md` §9 / §8（Q1–Q10）/ §5（D-1 ~ D-5）
> **权威设计**：`docs/architecture/DOCUMENT_IR_DESIGN_V1.md`（OQ-1~7 全部 DECIDED 2026-10-16）
> **测试文件**：`backend/tests/test_document_ir.py` / `test_ir_renderer.py` / `test_evidence_chain_ir.py`
> **反假绿原则**：每条"必须产出 X"均配"变异/对照必须失败或不产出 X"；核心红线为 **D-2 缺数据不编造**（禁伪造 bbox/stats/caption）与 **D-3 校验 fail-closed**（不过校验绝不落盘半成品 IR）。
> **排期约束**：017 实现必须在 S1（014/015/016）合并后启动（`mineru_pdf_parser.py` 白名单冲突，Owner 2026-10-16 裁定）。

---

## A017-01 IR schema 契约校验（封闭枚举 + 必填字段 + 坐标声明）

- 正常 fixture 产出的 `document_ir.json`：`schema_version="document-ir/1.0"`；`blocks[]` 每块含 `block_id / type / page_no / bbox / reading_order / confidence / content_ref` 全字段（不可得者为显式 `null`，不得缺键）；`type` 值全部落在 8 类封闭枚举 `{heading, paragraph, table, figure, formula, caption, header, footer}` 内；`coordinate_system.unit` 声明非空（`pdf_point` 或 `pixel`，实测后冻结）。
- **否定断言**：fixture 含一个未知 `type`（如 `"unknown_chart"`）→ 归并为 `paragraph` 且 `attrs.raw_type` 记录原值，**不得**原样透传为块类型，**不得**丢弃该块。
- **否定断言**：`blocks[]` 中出现枚举外 `type` 值的断言必须显式为真（`assert all(b["type"] in BLOCK_TYPES for b in blocks)`）。

## A017-02 block_id 跨重跑稳定性与 reading_order 一致性

- 同一 `content_list.json` 连续构建两次：`block_id` 集合与映射逐字节一致（`b0001`… 按 reading_order 分配，无随机 UUID）；同 sha256 + 同 adapter 版本 → IR 确定（仅 `generated_at` 类时间戳可差异）。
- `reading_order` 与 `blocks[]` 一一对应：值域为 `0..len(blocks)-1` 的置换，无重号、无越界、无空洞。
- **否定断言**：变异为随机 UUID 分配 → 稳定性测试必须 **FAIL**；渲染轨/证据链对 `block_id` 的引用不得因重跑而失配。

## A017-03 content_ref span 回引 parsed_text.md 正确性

- 逐块 `content_ref`：`offset_start/offset_end` 指向 `parsed_text.md` 内实际字符区间；用 `text[offset_start:offset_end]` 与块文本比对，一致性阈值断言必须存在且为真。
- `content_ref` 优先 span，退化为 block 引用时须显式标记（不得静默降级为"无引用"）。
- **否定断言**：越界 offset（`offset_end > len(text)`）必须被校验器拒绝；构造一个 offset 偏移 1 位的变异 → 回引一致性断言必须 **FAIL**。

## A017-04 tables[] cells 矩阵与 html 一致性

- `rowspan/colspan` 表 fixture：`cells[]` 矩阵合法——坐标无越界、无重叠、虚格（被 rowspan/colspan 覆盖位）**不入 cells**；`html ↔ cells` 双向可重建（cells → 重建 HTML 与原 `html` 语义等价）。
- `caption_block_id` 指向存在的 caption 块（`table_caption` 关联生效）；表级 `confidence` 为 null 时不产格级 `confidence` 键（OQ-3=A 不建模单元格置信度）。
- **否定断言**：`table_body` HTML 解析失败 → 该表降级为 `cells=[]` + `html` 原样保留 + `stats` 计数，**不得**中断文档、**不得**伪造 cells。
- **对照**：无表格文档 → `tables[]` 为空数组，零 issue。

## A017-05 figure asset 对齐与 fail-closed

- `figures[]` 各项：`asset_path` 为 `parsed_dir` 相对路径（指向 `assets/images/`）；`asset_sha256` 与 `parse_manifest.asset_files[]` 逐项对齐；`asset_path` 文件实际存在。
- **否定断言（fail-closed 核心）**：篡改 fixture 使 asset sha256 与 manifest 不一致 → 候选记为 `FAILED`，`document_ir.json` **不落盘**（半成品 IR 零写入），`errors[]` 含 sha256 与原因，批处理继续。
- `img_path` 重写为 `assets/images/` 相对路径（不得残留 staging 绝对路径或已清理路径）。

## A017-06 双轨渲染（Markdown 轨降级路径）

- `rendered.md` 由 IR 确定性渲染（纯函数）；`table_format="html_prefer"` 时首选 `table.html`。
- **降级路径 1**：`table.html` 缺失但 cells 完整 → 由 cells → MD pipe 降级渲染，表格内容不丢失。
- **降级路径 2**：`asset` 缺失 / `caption=null` → 用 `figure_id` 占位不产空链接（`![figure_id](...)` 形式可预测）。
- **降级路径 3**：`image_quality_score < image_quality_threshold` → 渲染轨降级为 caption-only 占位（不嵌图）。
- **否定断言**：`rendered.md` 不写 `parsed_text.md`（OQ-5=B）；`parsed_text.md` 内容与 hash 在执行前后**完全不变**。

## A017-07 VLM 兜底：schema 不过则整页降级 + Mock 诚实性变异（反假绿核心）

- `vlm_fallback.enabled=false`（默认）→ VLM 路径零调用（monkeypatch 客户端"被调用即 fail"仍通过）。
- 开启后：VLM 产物**必须**过同一 `document_ir_schema` 校验；**否定断言**：校验失败 → 该页整页降级为**单个 figure 块**（asset 引用页图），**不得**产出部分脏块（不得把部分合规块混入 `blocks[]`）。
- **变异复验（沿用 015 A015-06 标准）**：
  - 变异 A：注释掉 schema 校验直接落盘 → 测试必须 **FAIL**（脏块落盘断言命中）。
  - 变异 B：`MockVlmClient` 返回伪造非空文本 → 管线仍**不得**写出含该文本的 IR/渲染产物。
  - 两个变异缺一被捕即验收不通过；恢复后全绿。

## A017-08 MinerU ir_source 保留与 v1 产物兼容

- `parser_profile="mineru_ir_v2"` 路径：staging 清理前 `content_list.json`（必需）与 `middle.json`（可选）已拷入 `parsed_dir/ir_source/`；manifest 增记 `ir_source_files`（additive）；`2005 parsed_text.md` 与既有 v1 产物字段不变。
- **对照/否定断言**：旧 `mineru_default_v1` 产物（无 `ir_source/`）→ 该候选记 **`NO_IR_SOURCE` 跳过**，**不得** `FAILED`、**不得**报错中断；证据链走既有页级路径。
- content_list JSON 非法 → 候选 `FAILED`，错误入报告，批处理继续（与"缺失"语义严格区分）。

## A017-09 旧三件套契约与既有链路零回归

- `parsed_text.md` / `parsed_metadata.json` **零改动**（内容 + hash 不变）；`parse_manifest.json` 仅 additive 字段（`document_ir_path` / `rendered_md_path` / `ir_source_files`）。
- v1 profile 路径行为完全不变：`test_mineru_pdf_parser.py`、`test_markitdown_parser.py`、`test_evidence_chain.py`、`test_parser_router.py` 既有用例**零修改**通过。
- `document_ir.json` 不存在时，`evidence_chain.py` **完全走现有路径**（`_chunk_from_ir()` 不触发）。
- **否定断言**：001–016 全量回归通过；017 不改 001–016 任何已完成契约。

## A017-10 证据链扩展（table_cell / image_region / field_value 零数据）

- IR 存在时 `_chunk_from_ir()` 生效：`paragraph` 相邻合并至 token 上限（`chunk_type=text`, `chunk_level=block`）；`table`/`figure` 每块一 chunk（`chunk_type=table`/`figure`）；`metadata_json.block_id` 回引 IR 块（OQ-1=A 不升独立列）。
- 证据类型：文本块产 `section_quote`（span 回引）、表格逐 cell 产 `table_cell`（locator = `block_id` + `cell={row,col,rowspan,colspan}`）、图逐 figure 产 `image_region`（locator = `block_id` + `figure_id` + `asset_path`）。
- `evidence_uid` **v2 公式仅用于新类型**，v1 `section_quote` 公式与既有 uid 值**零变化**。
- **否定断言（S2 边界核心）**：`field_value` 证据类型**零数据行**——本 Loop 结束后 `kb_evidence` 中 `evidence_type='field_value'` 计数为 0（仅格式冻结）。
- **否定断言**：`kb_document_chunk` / `kb_evidence` 无新增列（零 migration）。

## A017-11 幂等、dry-run 与零 schema 迁移

- 同 sha256 + 同 adapter 版本重复 `build-document-ir`：第二次不重写产物（或产出一致内容），artifact 登记幂等（复用 010 upsert 模式，无重复行）。
- `--force` → 重建产物并更新登记。
- **dry-run 零写入**：`parsed/` 无新增文件、`kb_parsed_artifact` 无新行、raw_vault 不变；stdout 输出 `would_build/would_skip`。
- **零迁移硬断言**：`sql/migrations/**` 无任何新增文件（git status 校验）；`kb_parsed_artifact.artifact_type` 新增 `document_ir` / `rendered_md` 值走既有 VARCHAR 列。若 P2 DB Review 发现必须迁移项 → STOP 报 Owner（`plan.md` Q8）。

## A017-12 反假绿总变异复验 + 中文路径

- **三项变异必须全部被捕获（捕获率 3/3，缺一不通过）**：
  1. `stats.*` 改为硬编码常量（不复算 blocks）→ stats 一致性断言 **FAIL**（D-2 第 4 条）；
  2. 缺 bbox 块改为用页面均分估算填充 → "缺 bbox 必须为 null" 断言 **FAIL**（D-2 第 1 条，禁止伪造 bbox）；
  3. 校验器放宽（允许重复 reading_order）→ A017-02 一致性断言 **FAIL**（D-3 fail-closed）。
- 恢复后全绿。
- 中文路径：`中文路径` 目录下的 `content_list.json` / 产物全链路 UTF-8 通过；报告 JSON `ensure_ascii=False` 中文无乱码；`asset_path` 相对路径可定位。
- `middle.json` 可选路径：缺失时不失败，header/footer 判别降级为类型判定（OQ-6 剔除正文流 + 文末注释仍生效）。

---

## 验收门禁汇总

| 门禁 | 通过条件 |
|------|----------|
| IR schema 契约 | A017-01 ~ A017-05 全过（封闭枚举、block_id 稳定、span 回引、cells 矩阵、asset 对齐 fail-closed） |
| 反假绿 | A017-07 两变异 + A017-12 三变异全部被捕获（5/5）；A017-05/06/08/10 否定断言全真 |
| 渲染双轨 | A017-06 三条降级路径全过；`parsed_text.md` 零改动 |
| 兼容冻结 | A017-08（v1 产物 `NO_IR_SOURCE`）、A017-09（旧三件套 + 001–016 零回归） |
| 安全红线 | A017-10（`field_value` 零数据、零新增列）、A017-11（dry-run 零写入、零 migration） |
| 幂等与容错 | A017-02（跨重跑稳定）、A017-11（重复运行幂等）、单候选失败不中断批处理 |

---

## STOP 条件

Dev 完成后 **STOP**，不得自我宣布 A017-01 ~ A017-12 通过、不得写 handoff、不得 merge main。下游顺序：

```text
P2 DB Review（确认零迁移 + 写入范围：kb_document_chunk / kb_evidence / kb_parsed_artifact）
→ P4 Dev（测试先行）→ STOP → P5 QA → P6 E2E（含 OQ-7 bbox 单位 3 篇实测记录）→ P7 TL Final Review
```
