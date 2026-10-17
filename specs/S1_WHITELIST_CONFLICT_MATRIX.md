# S1（014/015/016/017）文件级白名单冲突矩阵

> **生成角色**：spec-lead
> **生成日期**：2026-10-16
> **数据来源（以磁盘为准）**：`specs/014-pdf-content-sniffing/tasks.md`、`specs/015-image-parse-pipeline/tasks.md`、`specs/016-semantic-quality-metrics/tasks.md`、`specs/017-unified-document-ir/plan.md §6.1`；磁盘实际文件树（`backend/app/**`、`backend/tests/**`、`config/**`）；`specs/SPEC_INDEX.md`
> **用途**：S1 合并（014/015/016/017）前的文件级冲突预判，供 TL 排期与合并顺序决策
> **边界**：本文件仅存于 `specs/**`，未触碰 `backend/**`、`generated/`，未改动 014/015/016/017 任何既有文件

---

## 0. 判定口径

| 判定 | 含义 |
|------|------|
| **硬冲突 / 互斥** | 两个 Spec 若并行改动，必定在同一函数/同一逻辑单元上语义互斥，无法靠 git 3-way merge 消解，**必须串行且后者等前者合并后重启** |
| **追加式可共存** | 同文件、不同函数或不同子命令/选项，可各自独立追加；**同文件仍需按序合并**（git 注册区/选项块可能 textual 冲突，需人工消解），但语义不互斥 |
| **无冲突（独享）** | 仅一个 Spec 触及该文件；其余 Spec 或不在白名单、或明列「禁止修改」 |
| 列取值 | `新增` / `修改` / `只读`（只读引用但禁止改） / `禁止`（明列禁止修改） / `—`（不在该 Spec 白名单） |

> 关键判据：**同文件 ≠ 硬冲突**。只有在改写同一函数签名/同一常量块/同一控制流时才升级为硬冲突（如 `_build_plan`、`_execute_parse`、staging finalize 逻辑）。

---

## 1. 全量冲突矩阵（逐文件，34 行）

| 文件路径 | 014 角色 | 015 | 016 | 017 | 冲突判定 | 建议串行顺序 |
|---|---|---|---|---|---|---|
| `backend/app/core/pdf_sniffing.py` | 新增 | — | — | — | 无冲突（014 独享） | — |
| `backend/app/core/parser_routing.py` | 修改（plan §7.2） | 只读（禁止改） | 禁止 | 禁止（红线只读） | 无冲突（014 独享） | — |
| `backend/app/core/config.py` | 修改（`storage.sniff_cache_root`） | — | — | — | 无冲突（014 独享） | — |
| `backend/app/core/parsed_paths.py` | — | 只读引用（015 T001:68；015 白名单**不含**本文件） | — | 修改（additive 键：`document_ir`/`rendered_md`/`ir_source`；017 T010 / plan §6.1:104） | 无冲突（017 独享修改；015 只读，**依据见 §1.1**） | — |
| `backend/app/services/parser_router.py` | 修改（plan §7.3） | — | — | — | 无冲突（014 独享） | — |
| `backend/app/services/mineru_pdf_parser.py` | 修改（plan §7.4 sniff 注入） | 禁止 | 禁止 | 修改（plan §3.2 ir_source additive） 【Q11 补 18:52】017 另在 `_clear_parsed_outputs`(`:805-815`) additive unlink `document_ir.json`/`rendered.md`/`ir_source/`（唯一调用点 `:512-513`，`if force:` 守卫）；红线 v1 no-op | **硬冲突（014 × 017 结构性互斥）** | 014 → 017（Owner 2026-10-16 裁定：017 排 S1 之后） |
| `backend/app/services/image_parser.py` | — | 新增 | 禁止 | 禁止 | 无冲突（015 独享） | — |
| `backend/app/services/parse_quality_checker.py` | 禁止 | 禁止 | 修改（plan §4 第五类） | 禁止 | 无冲突（016 独享） | — |
| `backend/app/services/parse_quality_report_summarizer.py` | 禁止 | — | 修改（仅两常量追加） | — | 无冲突（016 独享） | — |
| `backend/app/services/evidence_chain.py` | — | — | — | 修改（plan §4.5 `_chunk_from_ir`） | 无冲突（017 独享） | — |
| `backend/app/services/document_ir_schema.py` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/app/services/document_ir_builder.py` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/app/services/ir_renderer.py` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/app/services/document_ir_service.py` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/app/services/ir_vlm_fallback.py` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/app/adapters/image_preprocess.py` | — | 新增 | — | — | 无冲突（015 独享） | — |
| `backend/app/adapters/image_quality.py` | — | 新增 | — | — | 无冲突（015 独享） | — |
| `backend/app/adapters/ocr_paddle.py` | — | 新增 | — | — | 无冲突（015 独享） | — |
| `backend/app/adapters/vlm_adapter.py` | — | 新增 | — | — | 无冲突（015 独享） | — |
| `backend/app/cli/main.py` | 修改（`route-parsers` 新选项） | 修改（新增 `parse-images` 子命令） | 修改（`check-parse-quality --no-semantic`） | 修改（注册 `build-document-ir` 子命令） | **追加式可共存**（同文件、不同子命令/选项；须串行合并） | **014 → 015 → 016 → 017**（同文件逐 Spec 串行） |
| `config/pdf_sniffing.yaml` | 新增 | — | — | — | 无冲突（014 独享） | — |
| `config/app.example.yaml` | 修改（`storage.sniff_cache_root`） | — | — | — | 无冲突（014 独享） | — |
| `config/image_parse.yaml` | — | 新增 | — | — | 无冲突（015 独享） | — |
| `config/quality_semantics.yaml` | — | — | 新增 | — | 无冲突（016 独享） | — |
| `config/document_ir.yaml` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/requirements.txt` | 修改（`+pypdf`，Owner O-2 批准） | 修改（仅加可选依赖注释，不增硬依赖） | — | — | 追加式可共存（同文件，不同行） | 014 → 015 |
| `backend/tests/test_pdf_sniffing.py` | 新增 | — | — | — | 无冲突（014 独享） | — |
| `backend/tests/test_parser_router_v2.py` | 新增 | — | — | — | 无冲突（014 独享） | — |
| `backend/tests/test_image_parser.py` | — | 新增 | — | — | 无冲突（015 独享） | — |
| `backend/tests/test_parse_quality_checker_semantic.py` | — | — | 新增 | — | 无冲突（016 独享） | — |
| `backend/tests/test_document_ir.py` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/tests/test_ir_renderer.py` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/tests/test_evidence_chain_ir.py` | — | — | — | 新增 | 无冲突（017 独享） | — |
| `backend/tests/fixtures/document_ir_*/` | — | — | — | 新增 | 无冲突（017 独享） | — |

**矩阵行数：34 行**（唯一文件路径去重后）。

### 1.1 第 31 行（`parsed_paths.py`）判定依据 — 已复核（2026-10-16，TL 收尾校验）

**结论：维持「017 独享修改、015 只读」**。015 的新增图片产物**复用既有 `ParsedArtifactPaths` 键，不新增键**：

- **既有键**（`backend/app/core/parsed_paths.py:7-11`）：`parsed_dir` / `parsed_text` / `parsed_metadata` / `parse_manifest`。
- **015 产物契约**（`specs/015-image-parse-pipeline/spec.md:100-106`）：三件套 `parsed_text.md` / `parsed_metadata.json` / `parse_manifest.json` **正是既有三键**；新增产物 `assets/preprocess/`（spec.md:105、plan.md:66）是 `parsed_dir` 的**子目录**，由既有 `parsed_dir` 键派生即可定位，**无需新增键**。
- **OCR 文本** 落 `parsed_text.md`（既有 `parsed_text` 键）；`assets_dir`（plan.md:65）与 `extra.asset_files[]`（plan.md:66）是 **manifest/metadata 的 JSON 字段**，非路径键。
- **015 无权改本文件**：015 白名单**不含** `backend/app/core/parsed_paths.py`（plan.md §9.1:241-249、tasks.md:14-25 允许修改清单均无该文件；亦未列入 015 禁止清单 → 属纯只读 T001:68）。
- **与 017 对照**：017 为子目录**新增** `ir_source` 键（017 T010 / plan §6.1:104）；015 为 `assets/` 子目录**不**加键 —— 属风格差异，**不构成冲突**。

因此第 31 行维持原判定。**升级条件**：若 015 实现期 Dev 提出需显式 `assets_dir` 键，则须走 015 白名单扩项（当前禁止）；届时本行升级为「**015 × 017 追加式可共存**（同文件 additive，须串行合并）」，并同步 §2 / §3。

---

## 2. 硬冲突清单（逐条列出文件路径）

**硬冲突总数：1 条**

1. `backend/app/services/mineru_pdf_parser.py` — **014 × 017 结构性互斥**
   - 014 改动点：`_build_plan` 注入 PDF 嗅探管线（`plan.md §7.4`），影响路由判定分支。
   - 017 改动点：staging 清理前保留 `content_list.json` / `middle.json` 至 `parsed_dir/ir_source/`，manifest 增 `ir_source_files`，`parser_profile` 升 `mineru_ir_v2`（`plan.md §3.2`）。
   - 二者虽名义「同文件、不同方法」，但共享同一 staging finalize 生命周期与 `parser_profile` 常量；017 明确要求「不改既有 v1 行为分支」，而 014 正在改的正是该分支上游。**必须串行**。
   - **裁定依据**：Owner 2026-10-16 裁定（`specs/017-.../plan.md §3.2`、`acceptance.md` 头部「排期约束」、`SPEC_INDEX.md` §106「S1 期间不得开工 017 实现」）：**017 实现排期在 S1（014/015/016）合并之后**。

> 除上述 1 条外，**无其他硬冲突或互斥**。`backend/app/cli/main.py` 与 `backend/requirements.txt` 虽为多方共享，但均属「追加式可共存」，不计入硬冲突（见下节说明）。

---

## 3. 建议串行 / 并行序

### 3.1 总序（对齐 SPEC_INDEX.md §98-99、§106）

```text
【S1 阶段】
  014  ──►  015  ──►  016
（顺序推进；SPEC_INDEX 已锁定 014→015→016）

【S2 阶段（S1 合并后）】
  017
```

### 3.2 逐文件/逐依赖说明

| 环节 | 结论 | 理由 |
|------|------|------|
| 014 先行 | **必须** | 014 修改 `mineru_pdf_parser.py` / `parser_routing.py` / `parser_router.py`，是 015/016 的**只读上游**（015/016 均 `import match_route_type` 且禁止改路由）；014 也先落地 CLI 约定 |
| 015 → 016 顺序 | **建议按序**（理论可并行） | 015 与 016 的**代码文件零重叠**（015 在 `services/image_parser.py`+`adapters/*`；016 在 `services/parse_quality_checker.py`+`summarizer`），可并行开发。**但二者同改 `cli/main.py`**，且 016 白名单依赖 015 的 `parser_name="image_ocr"` 才能验证「不再产 `MANIFEST_PARSER_NAME_INVALID`」（016 T009/A016-09 联动）→ 建议 **015 先合并，016 后合并** |
| 017 最后 | **强制** | ① `mineru_pdf_parser.py` 硬冲突，须等 014 合并；② 017 也改 `cli/main.py`，须排在 014/015/016 之后；③ SPEC_INDEX 明令 S1 期间不得开工 017 实现 |
| `cli/main.py` 合并 | **014 → 015 → 016 → 017 严格串行** | 4 方同文件。语义上各加独立子命令/选项（不互斥），但 Typer 注册区 textual 冲突需人工消解；**禁止两方并行编辑同一 `main.py`**；015/016/017 仅 append 新子命令/选项，不得回改或重排 014 已冻结的 42 行 |
| `requirements.txt` | 014 → 015 | 014 增 `pypdf` 一行；015 仅加可选依赖注释；同文件串行避免行级冲突 |

### 3.3 可真正并行的窗口

- 015 与 016 的**非 CLI 代码 + 测试**可并行开发（互不引用对方实现文件）。
- 并行期间若两者都需动 `cli/main.py`：建议由**同一实现者串行落两处 CLI 改动**，或约定 CLI 改动窗口期（一方先合）。
- 017 的 `document_ir_*` / `ir_renderer` 等**新增文件**可提前设计定稿，但**不得**写入仓库实现（SPEC_INDEX §106）。

---

## 4. 与派工描述不一致之处（以磁盘为准）

| # | 派工描述 | 磁盘实际 | 影响 |
|---|----------|----------|------|
| D-1 | 「017 涉及 `mineru_pdf_parser.py`、**`search_service`**、config、parse_manifest、evidence_chain 等」 | 017 `plan.md §6.1` 白名单**不含 `search_service.py`**；`grep search_service specs/017-*` = 0 命中。017 实际涉及：5 个新增 `document_ir_*`/`ir_renderer`/`ir_vlm_fallback` + `evidence_chain.py` + `core/parsed_paths.py` + `cli/main.py` + `mineru_pdf_parser.py` + `config/document_ir.yaml` | 矩阵**不列** `search_service.py`；若后续 017 确需改 `search_service.py`，属白名单扩项，需先回到 017 plan 更新 |
| D-2 | 「014 白名单含 `cli/main.py`（T007）」并暗示 014 进行中 | 磁盘 `git status` 显示 `backend/app/cli/main.py` **未修改** → 014 T007 尚未开工；`backend/tests/test_pdf_sniffing.py`、`test_parser_router_v2.py` **不存在** → T002/T008 未完成 【2026-10-16 18:31 消解 / 18:35 复核】cli/main.py 已完成 T007 并冻结（md5 15ee9fc5e7b1cf700dfe7c15dbc50f6f；git diff --numstat = 42/0 纯新增）；tests/test_pdf_sniffing.py（27 用例）、tests/test_parser_router_v2.py（34 用例）均已落盘（pytest --collect-only 实测；此前 20/27 系未核数字，已修正）；config/pdf_sniffing.yaml 未被污染（md5 8a790c352a61f26b4288fb1b6134f5f0）→ 014 转为"实现近完成，仅剩缓存白名单整改 + 自检报告" 【18:36 补】缓存写白名单 MUST-FIX 已落盘，并经独立变异证伪（team-lead 复核：白名单退化为 if True → 恰 2 failed；ENCRYPTED→PDF_DIGITAL → import AssertionError）；两测试文件实为 27 / 34 用例，**合计 61 passed**；全量 **4 failed / 253 passed / 129 skipped**（4 failed 均系既有 Typer/Click `--help` 基线，非本次引入）→ 014 实现批次**已完成**，待 QA。 | 014 为**部分实现**：已改 `core/config.py`、`core/parser_routing.py`、`services/parser_router.py`、`services/mineru_pdf_parser.py`、`requirements.txt`、`config/app.example.yaml`；已新增 `core/pdf_sniffing.py`、`config/pdf_sniffing.yaml`。`cli/main.py` 冲突尚未实际发生（仍会随 T007 到来） 【18:31 更新】T007 已落盘并冻结；`cli/main.py` 的 014 侧冲突窗口**已关闭**（改由 015/016/017 按序 append） 【18:35 复核】015 已具备启动条件（唯一前置 = 014 通过独立 QA）；016 待 015 合并后再动 `check-parse-quality`（依据 §3.2:111） 【2026-10-16 已消解】014 独立 QA verdict = **PASS**（独立 QA = ir-architect；A014-01~12 全 PASS；M1 白名单变异 → 恰 2 failed、M2 守卫变异 → import AssertionError，两项均已恢复原 md5；本矩阵复验 5 份冻结 md5 全部命中）→ `specs/014-pdf-content-sniffing/tasks.md` T011 下游门禁**已释放**（正文 L220 已 `- [x]`；注：同期进度总览 L238 仍 `- [ ] 待 QA`，属 014 侧待同步项，非本矩阵可改）→ **015 正式放行** |
| D-3 | 派工称「014 已实现」`parser_router.py` / `mineru_pdf_parser.py`（§7.4）进行中 | 二者均为 `M 修改` 状态（存在改动） | 与派工一致；但需注意 `mineru_pdf_parser.py` 尚在改动中，**与 017 的冲突窗口仍开放** 【18:34 更新】实测 `mineru_pdf_parser.py`（mtime 18:25）/ `parser_router.py`（mtime 18:24）自此未再变动，014 侧改动趋于冻结；**与 017 的冲突窗口仍开放**（原因改判为 017 依 Owner 裁定排 S1 之后，与 014 是否仍在编辑无关） |
| D-4 | 015 tasks T001 引用 `backend/app/services/parsed_paths.py` | 实际路径为 `backend/app/core/parsed_paths.py`（`services/` 下无此文件） | 015 文档**路径笔误**（仅阅读指引，非白名单项）；017 正确引用 `core/parsed_paths.py`。建议 015 tasks T001 修正为 `core/` |
| D-5 | 派工称 015「5 新增（adapter/service + CLI + yaml + test）」 | 015 白名单实为 **9 条**：5 代码新增（`image_parser` + 4 adapter）+ `cli/main.py` 修改 + `config/image_parse.yaml` 新增 + `requirements.txt` 注释 + `test_image_parser.py` 新增 | 措辞差异，不影响判定；重点是其中 **CLI 与 requirements 两项与 014 共享** |
| D-6 | 未提及 | 015/016 的白名单**交叉引用**：015 明列禁止改 `parse_quality_checker.py`/`summarizer`（016 范围）；016 明列禁止改 `image_parser.py`/`mineru_pdf_parser.py`（015/014 范围） | 边界设计良好，**无冲突**；可作为合并期的交叉校验依据 |

---

## 5. 附：四 Spec 白名单来源与规模

| Spec | 来源 | 白名单规模 |
|------|------|------------|
| 014 | `specs/014-pdf-content-sniffing/tasks.md` §Dev 文件白名单 | 11 条（9 代码/config + 1 requirements + 1 tasks.md；含 2 测试新增） |
| 015 | `specs/015-image-parse-pipeline/tasks.md` §Dev 文件白名单 | 9 条（5 新增 + CLI + yaml + requirements + 1 测试） |
| 016 | `specs/016-semantic-quality-metrics/tasks.md` §Dev 文件白名单 | 5 条（3 修改 + yaml + 1 测试） |
| 017 | `specs/017-unified-document-ir/plan.md §6.1` | 14 条（12 新增含 fixtures + `mineru_pdf_parser.py`/`evidence_chain.py`/`parsed_paths.py`/`cli/main.py` 4 修改） |

> 注：各 Spec `tasks.md` 自身（`specs/0xx-*/tasks.md` 勾选）不计入代码合并矩阵。

---

## 结论速览

- **矩阵行数：34 行**
- **硬冲突条数：1 条** — `backend/app/services/mineru_pdf_parser.py`（014 × 017）
- **追加式可共存（需串行合并）：2 条** — `backend/app/cli/main.py`（014×015×016×017）、`backend/requirements.txt`（014×015）
- **建议串行序**：`014 → 015 → 016`（S1，SPEC_INDEX 已锁定）→ **017**（S1 合并后，强制）
- **可并行窗口**：015 与 016 的非 CLI 文件可并行；CLI 与 requirements 必须串行

---

**矩阵结束**

---

## 6. 变更留痕：014 冻结件 md5 漂移（team-lead，2026-10-17）

> 本节为 append-only 留痕，**不改写 §1 矩阵既有判定**。

| 文件 | §1 判定 | 原冻结 md5 | 现盘 md5（现拼） | 对矩阵结论的影响 |
|---|---|---|---|---|
| `backend/app/services/mineru_pdf_parser.py` | 硬冲突（014 × 017），序 014 → 017 | `d8383fde…`（014 acceptance §1） | `a82a3653…` | **判定不变**：仍是 014 × 017 结构性硬冲突，017 仍须排 014 之后；但 017 引用的**行号锚点已漂移**（见下） |
| `backend/app/services/image_parser.py` | 015 独享 | `0d6aaf31…`（015 `p2_db_review.md` §11） | `927b9029…` | **判定不变**：仍 015 独享；本次由 TL 代修 1 行（`instance_uid` → `file_instance_uid`），已在 015 报告附录 B 登记 |

**给 017 的锚点更正**（017 `plan.md §3.2` 引「`_clear_parsed_outputs :805-815`」）

- `_clear_parsed_outputs` 定义现位于 **`:841`**；函数体（三键 unlink + `assets_dir` rmtree）位于 **`:846-851`**；唯一调用点位于 **`:530`**（`_execute_parse` 内，`if force:` 守卫）。
- 建议 017 全文改用**符号引用**（`MineruPdfParser._clear_parsed_outputs`），行号仅作参考，避免再次漂移。
- 017 若需追加 IR 清理，仍须遵守 §1 该行备注：**additive 追加、既有行逐行不动、先进白名单、先 STOP 报 TL**。

**本次未触碰（可据以核对）**：`core/pdf_sniffing.py` `f6799625…`、`core/parser_routing.py` `f41c6441…`、`services/parser_router.py` `e25a20b2…`、`tests/test_pdf_sniffing.py` `cdd0bc72…`、`tests/test_parser_router_v2.py` `7db8dcb4…`、`config/pdf_sniffing.yaml` `8a790c35…` —— 全部与 014 acceptance §1 基线逐字一致。

---

## 7. 016 白名单例外登记（TL 裁定，2026-10-17）

016 实现期报出**规格内部硬冲突**，TL 核实后授权一处白名单外改动，特此登记。

### 7.1 冲突事实（实测，非推演）

`backend/tests/test_parse_quality_report_summarizer.py` 两条既有断言在同一输出字段上互斥：

| 位置 | 断言 | 隐含要求 |
|---|---|---|
| `:111` | `set(data["issue_counts"]) == set(ISSUE_CODES)` | 输出须为**扩展后 23 键** |
| `:516` | `data["issue_counts"] == payload["issue_counts"]`（fixture 18 键） | 输出须**恰好 18 键** |

而 `test_issue_codes_match_008_checker`（`ISSUE_CODES == CHECKER_ISSUE_CODES`）又要求 016 扩了 checker 就必须扩 summarizer → 上述两条必然对撞。

### 7.2 裁定

- **取方案 A**：对 `:516` 做单行等价化，期望改为 `{code: payload["issue_counts"].get(code, 0) for code in ISSUE_CODES}`。
- 理由：`:111` 本身就是 009 对扩展后 `ISSUE_CODES` 的期望；改 `:516` 只把「硬编码 18 键」改为「按 `ISSUE_CODES` 归一化」，**保留其原意**（过滤 severity 不改变计数）。反向选择（输出镜像 18 键）只会把失败转移到更强的集合相等断言上。
- 约束：只改该一处；不得硬编码 23；不得放松该文件其他断言。

### 7.3 白名单例外清单

| 文件 | 例外内容 | 归属 |
|---|---|---|
| `backend/tests/test_parse_quality_report_summarizer.py` | **仅 `:516` 一处**等价化改动（009 测试文件，016 白名单外） | TL 授权，需在 016 `tasks.md` 与交付回执留痕（改前/改后 md5 + diff 行数） |

### 7.4 另一处规格偏离（同一裁定）

plan §3.2 写「summarizer **仅** `ISSUE_CODES` / `ALLOWED_PARSER_NAMES` 两常量追加」，但只改两常量会使 **016 自己产的 1.1 报告 009 读不了**（`validate_input_report` 对 `schema_version != "1.0"` 直接 raise），真实链路断裂。TL 批准输入侧最小兼容，附 4 条硬约束：

1. `INPUT_SCHEMA_VERSIONS={"1.0","1.1"}`，**未知版本仍须 raise**；
2. 18 个 legacy 键**仍必填**，缺失仍 raise（校验强度不得降低）；5 个语义键仅 1.1 可选；
3. `SCHEMA_VERSION` **输出常量不动**（009 仍输出 1.0）；
4. `_classify_noise` / 分桶路径 / `:120-121` 硬编码一行不动。
