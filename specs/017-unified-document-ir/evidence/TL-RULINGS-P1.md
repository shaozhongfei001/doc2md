# 017 TL 裁定表（P1 阶段，2026-10-17）

> **来源**：`evidence/AUDIT-P1.md` 报出 13 项 `SPEC_GAP`（两文档互斥 3 项、定义缺失 10 项）
> **裁定人**：team-lead
> **性质**：本表为 P1~P5 实现与验收的**唯一判据**；与 design 文档冲突处以本表为准（design 仅为设计稿，未冻结）
> **已落地**：GAP-01 / GAP-06 / GAP-10 / GAP-13 已当场收口到 `acceptance.md`、`test_cases.md`、`plan.md §8`；其余为本表新增约束

---

## 一、字段与结构裁定

| # | GAP | 裁定 | 判据落点 |
|---|---|---|---|
| **R-01** | GAP-01 字段名互斥 | `content_ref` span 字段实名 = **`start_offset` / `end_offset`**（半开区间 `[start, end)`，与 010 chunk 体系一致）。已改 `acceptance.md` L26/L28、`test_cases.md` TC017-08/09/10 共 9 个 token。**禁止连带改** `KbEvidence.source_char_start/source_char_end`（不同载体，合法并存） | design §3.2 L98-99；QA_PREP §六 M1 |
| **R-02** | GAP-02 退化形式未定义 | `content_ref.kind` **必填**且 ∈ `{text_span, inline_text, table_ref, figure_ref, asset, none}`（封闭）。各 kind 键集：`text_span{text, source, start_offset, end_offset}`；`inline_text{text}`；`table_ref{table_id}`；`figure_ref{figure_id}`；`asset{asset_path}`；`none{reason}`。**缺 kind 或 kind 非法 → 校验失败**（不得静默降级为「无引用」） | design §3.2 L114-116；A017-03 |
| **R-03** | GAP-03 顶层必填键集 | 以 design §3.1 示例为契约：`schema_version / document_uid / content_uid / sha256 / source_vault_path / provenance / page_count / coordinate_system / blocks / tables / figures / reading_order / stats` **全部必填**（值可为 `null`，但键不得缺）。markitdown 路径 `page_count = null`（**不编造页数**）。`document_uid` 必须确定性：`doc_<sha256[:16]>_<adapter_version>` | design §3.1 |
| **R-04** | GAP-04 bbox 合法性 | `bbox` 为对象 `{x0, y0, x1, y1}` 或 `null`。校验：四值均为 number；`x1 > x0`、`y1 > y0`；四值 `>= 0`。`null` 合法（缺数据），**禁估算填充** | design §3.2；D-2 |
| **R-05** | GAP-05 cells 键集与尺寸语义 | `cells[]` 元素键集以 design §3.3 示例为准：`{row, col, rowspan, colspan, is_header, text, bbox}`；`bbox` **恒为 `null`**（V1 不建模格级 bbox）。`n_rows`/`n_cols` = **展开矩阵逻辑尺寸**（被 rowspan/colspan 覆盖的虚格计入尺寸，但不入 `cells`） | design §3.3 |
| **R-06** | GAP-06 格级 confidence | `cells[]` 元素**任何情况下都不得出现 `confidence` 键**（由「表级为 null 时不得出现」收紧为无条件禁止；否则表级非 null 时产格级键的变异抓不到）。已改 `acceptance.md` A017-04 | OQ-3=A；A017-04 |
| **R-07** | GAP-07 asset 对齐键 | manifest `asset_files[].relative_path` 基准 = **`assets_dir`**；IR `asset_path` 基准 = **`parsed_dir`**。转换：`relative_path` 已以 `assets/` 开头则直用，否则 `asset_path = "assets/" + relative_path`。**对齐键 = `sha256`**（不靠路径字符串匹配） | `mineru_pdf_parser.py:806-822`；A017-05 |
| **R-08** | GAP-08 img_path 重写 | 重写目标 = `parsed_dir` 相对路径，固定 `assets/images/<basename>`；跨页同名冲突时前缀 figure_id：`assets/images/<figure_id>_<basename>`。**不得**残留 staging 绝对路径 | A017-05 |
| **R-09** | GAP-09 stats 口径 | 键集以 design §3.1 为准：`block_count` / `table_count` / `figure_count` / `blocks_without_bbox` / `blocks_low_confidence`。前三个分别 = `len(blocks)` / `len(tables)` / `len(figures)`；`blocks_without_bbox` = `bbox is null` 计数；`blocks_low_confidence` = `confidence` 非 null 且 `< 0.5` 计数。**全部由数据复算，禁硬编码**（A017-12 变异 1 的判定基线） | design §3.1；A017-12 |
| **R-10** | GAP-10 占位渲染互斥 | asset 缺失 / caption=null → 占位文本 `[图像 {figure_id}: {caption 或 ocr_text 截断}]`（design §5 渲染表为准）。**不得**产出指向缺失资产的 Markdown 图片链接；否定断言：该 figure 在 `rendered.md` 中无 `![` 形式。已改 `acceptance.md` A017-06 | design L179；A017-06 |
| **R-11** | GAP-11 degraded 落点 | `degraded: true` **仅出现在渲染侧**（渲染产物/渲染说明）；IR `figures[]` **不新增该键**，IR 侧信息由 `image_quality_score` 表达 | design §3.4 L162 |
| **R-12** | GAP-12 evidence_uid v2 | 复用 v1 的构造方式（同一 hash 算法与前缀/长度），仅要求 `locator` 序列化确定：`json.dumps(locator, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`；`cells` 的 locator 键固定 `{row, col, rowspan, colspan}`。**v1 `section_quote` 的 uid 值必须零变化**（P5 需以 v1 源码对齐并给出对照证据） | A017-10 |
| **R-13** | GAP-13 Q11-Q13 缺权威出处 | 已补入 `plan.md §8`：Q11 `--force` 清理 = `_clear_parsed_outputs` additive 追加三产物（v1 no-op、既有行不动）；Q12 校验器 = 只读校验（禁写盘/禁 DB）；Q13 dry-run = 4 态（`would_build` / `would_skip(no_ir_source)` / `would_fail` / `already_built`） | plan §8 |
| **R-14** | 补充：block_id 位数 | `block_id = "b" + 按 reading_order 的顺序号，零填充至至少 4 位`；超过 9999 自然进位为 5 位（`b10000`），**不截断、不重置** | design §3.2 L107 |

---

## 二、审计者附带的实测基线（P2/P5 必须据此设定对照）

| # | 事实 | 影响 |
|---|---|---|
| **B-01** | `evidence_chain._chunk_mineru` 读 `metadata["pages"]`，但 `mineru_pdf_parser.py:629-639` 的 `metadata_payload` **从不写 `pages`** → 现有 mineru 路径实际总是回退到 `_chunk_markitdown`（section 切分） | A017-09「`document_ir.json` 缺位时走旧路径」的对照基线是 **section 切分**，不是「页级」；P5 验收须以此为准 |
| **B-02** | v1 uid 公式（`evidence_chain.py:586/590-591`）使用 `chunk.start_offset/end_offset`；evidence 侧字段为 `source_char_start/source_char_end` | R-01 改字段名时两组不可连带改；P5 须给「v1 uid 零变化」的对照证据 |
| **B-03** | `git status --porcelain sql/migrations/` 输出为空 | 零迁移基线成立（A017-11 的对照起点） |

---

## 三、执行状态

| 项 | 状态 |
|---|---|
| GAP-01 / 06 / 10 / 13 文档收口 | **已完成**（`acceptance.md` / `test_cases.md` / `plan.md §8`） |
| GAP-02/03/04/05/07/08/09/11/12 约束 | **本表即契约**，由 P1~P5 实现与 P6 验收共同遵循 |
| 是否需 Owner 裁定 | **否**（13 项均可在既有 OQ/决策表框架内闭合；无扩大试点范围或语义变更） |

---

## 四、追加裁定 R-15 ~ R-18（P1 实现者提出，2026-10-17 补）

| # | 歧义 | 裁定 | 落地 |
|---|---|---|---|
| **R-15** | `block_id` 基数：`acceptance.md` A017-02 写「`b0001`…」→ 1-based；design §3.2 示例 `b0007` 配 `reading_order: 7` → 0-based | **1-based**：`block_id = "b" + f"{reading_order + 1:04d}"`，首块 `b0001` ↔ `reading_order=0`；超 9999 自然进位（`b10000`）。design 示例为旧笔误，以 acceptance 为准 | P2 builder 生成规则；schema 只校 `^b\d{4,}$` + 唯一 + 随 reading_order 严格递增（对基数不表态，故 P1 无需改动） |
| **R-16** | R-03「13 键值可 null」的范围 | 收窄：仅 `page_count` / `source_vault_path` / `provenance` 可 `null`；`document_uid` / `content_uid` / `sha256` / `schema_version` / `coordinate_system` / `blocks` / `tables` / `figures` / `reading_order` / `stats` **必须非 null**（否则 uid 确定性与结构校验失去意义） | 已由 P1 校验器实现（`97819aca…`） |
| **R-17** | R-08 只允许 `assets/images/` 会拒掉 VLM 整页图 | `asset_path` 允许**两个扁平目录**：`assets/images/<basename>`（MinerU 抽图）与 `assets/pages/<basename>`（VLM 整页渲染）。均**禁嵌套子目录**；新增目录须先报 TL | P2/P5；P1 校验器已按 images 严格校，P5 前需补 pages 白名单（在 P5 阶段改，属 R-08 修订而非新开口） |
| **R-18** | 空表降级时 `n_rows/n_cols` 取值 | `cells=[]` 且 `n_rows=n_cols=0` **合法**（对应 A017-04「html 解析失败 → cells=[] + html 原样 + stats 计数」）；**不得**改 null、**不得**猜测尺寸 | P2 builder 降级路径 |

### 附：已知测试隔离债（非 017 引入，登记待收口）

P1 验收期间出现两次**瞬时多失败**，复跑即消失：
- TL 侧：全量 5 failed，多出 `test_markitdown_parser.py::test_chinese_path_integration`（`StaleDataError: UPDATE kb_file_content expected 1 row`）；
- 实现者侧：全量 10 failed，多出 `test_mineru_pdf_parser.py` 6 个。

共同点：都依赖**共享测试库**中的既有行，跨文件执行顺序/残留会互相破坏（单跑或复跑即恢复）。结论：
① 非 017 改动引入（新测试文件对 DB 零写入，已用 `--ignore` 对照验证）；
② 记为**测试隔离债**，登记到 P7 收口清单（不在当前阶段修复，避免扩大改动面）；
③ 后续阶段凡写 DB 的用例，须按 sha256 前缀自带清理，不得依赖执行顺序。
