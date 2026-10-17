# 017 验收准备清单（QA_PREP_017）

> **角色**：ir-architect（独立 QA，014 session `qa-014-001`；017 验收准备由 team-lead 派工）
> **日期**：2026-10-16
> **性质**：**只读准备**。不含实现、不含测试代码、不产生 fixture、不改任何既有规格文件。
> **授权**：team-lead 明示本文件为 `specs/017/**` 下**唯一授权例外**（已预登记给 spec-lead）；本目录内不得写第二个文件。
> **排期约束**：`specs/SPEC_INDEX.md:106` / `:112` 明令 **S1 期间不得开工 017 实现**。
> **权威设计**：`docs/architecture/DOCUMENT_IR_DESIGN_V1.md`（OQ-1~7 全 DECIDED 2026-10-16）
> **末行声明**：写入时 `specs/017-unified-document-ir/` 下除本文件外**无任何新增/修改**。

---

## 一、就绪度判定与前置阻塞

| 维度 | 判定 | 说明 |
|------|------|------|
| G0 交付物齐备度 | 齐备 | 设计稿 + spec/plan/tasks/test_cases/acceptance；OQ-1~7 全 DECIDED |
| 验收口径完备度 | 基本完备 | A017-01~12 × TC017-01~52 × 11 变异，6 条 fail-closed 分叉 1:1 变异映射 |
| 验收就绪度 | **条件就绪** | 3 项前置阻塞 + 6 项 M 待 017 收口窗口统一修正（§六） |
| 本仓真实 MinerU 产物 | **无** | 实测见 §三.1 |

**前置阻塞（须 017 P4 前闭环）**

1. **P2 DB Review**（`plan.md` §10）：确认零迁移 + 写入范围（`kb_document_chunk` / `kb_evidence` / `kb_parsed_artifact`）。
2. **真实产物缺位**（§三）：OQ-7 三篇实测 / A017-08 v2 真实路径 / P6 E2E 依赖外部输入。
3. **测试基线与 `--help` 红点口径**（§七 G-08）。

---

## 二、IR 校验用例设计（A017-01~12 × TC017-01~52 审查结论）

### 2.1 覆盖性核对

- 验收项覆盖 **12/12**；用例 **52** 条（TC017-01~52），其中变异复验 11 条。
- fail-closed 分叉 1:1：A017-01→TC017-04、A017-02→TC017-07(+50)、A017-03→TC017-10、A017-06→TC017-25、A017-08→TC017-33、A017-10→TC017-43 —— **6/6 齐备**。
- 非分叉红线亦配变异：A017-05（TC017-19）、A017-07（TC017-28/29）、A017-12（TC017-48/49/50）。
- **结论**：用例设计可验收；不足集中在「字段命名口径」（M1）与「缺失断言」（M5）。

### 2.2 逐项 QA 锚点与反假绿要害

| 验收 | QA 必须命中的判定点 | 反假绿要害 |
|------|----------------------|-----------|
| A017-01 | `assert all(b["type"] in BLOCK_TYPES for b in blocks)`；7 必填键不可缺（`null` 合法）；`coordinate_system.unit` 非空 | 未知 type → paragraph + `attrs.raw_type`，不得透传、不得丢块 |
| A017-02 | `block_id` 跨重跑逐字节一致；`reading_order` 为 `0..n-1` 置换 | 禁随机 UUID；TC017-07/50 必须 FAIL |
| A017-03 | `text[start:end]` 与块文本一致（**显式为真的阈值断言**，不得只断言"不抛异常"） | 越界必须拒绝；偏移 1 位必须 FAIL |
| A017-04 | cells 无越界/无重叠、虚格不入；`html↔cells` 双向可重建；表级 null 时不产格级键 | 畸形 `table_body` → `cells=[]` + html 原样，不中断、不伪造 |
| A017-05 | `asset_path` 相对 parsed_dir 且文件存在；sha256 与 manifest 逐项对齐 | **fail-closed 核心**：不一致 → `FAILED` 且 IR 零写入；img_path 不得残留 staging 绝对路径 |
| A017-06 | 三条降级路径可复现；顺序严格按 `reading_order` | 红线：`rendered.md` 不写 `parsed_text.md`，内容+hash 前后完全不变 |
| A017-07 | 默认禁用 → VLM **零调用**（"被调用即 fail"仍通过） | 校验失败 → 整页降级单 figure 块，不得混入部分合规块；Mock 伪造文本不得落盘 |
| A017-08 | v2 `ir_source/` 保留 + manifest 增记 `ir_source_files` | v1 无 ir_source → `NO_IR_SOURCE` **跳过**，不得 FAILED；**非法 ≠ 缺失** |
| A017-09 | 三件套内容+hash 零改动；manifest 仅 additive 三字段 | 无 IR 时 `_chunk_from_ir()` **不触发**，行为逐字段等同旧路径 |
| A017-10 | `metadata_json.block_id` 回引；chunk↔block N:1 用 `metadata.block_ids[]` | **S2 边界核心**：`field_value` 零数据行；两表**零新增列**；v1 uid 值零变化 |
| A017-11 | 二次运行不重写；`--force` 重建并更新登记；dry-run 三处零写入 | 零迁移硬断言：`sql/migrations/**` 无新增文件（作用域见 G-07） |
| A017-12 | 三变异全部捕获；中文路径全链路 UTF-8；middle.json 缺失不失败 | `stats.*` 必须由实际 blocks **重算**；缺 bbox **必须 null** |

### 2.3 审查中新发现（**Q-A / Q-B 已获 TL 裁定**，转 spec-lead 写入 017 `plan.md` **§8 TL 决策表（Q12 = Q-A；Q13 = Q-B）** —— **TL 终裁落点 = `plan.md` §8，已收敛、无二义**；`plan.md §2.3` 实为「配置」段、无裁定语义（**本节号 `§2.3` 系本文件自身章节号，与 `plan.md §2.3` 无关**）；Q-C / Q-D 由 M5 / M3 关闭）

- **Q-A（接口归属，P2 前厘清）—— TL 定稿（2026-10-16）：采纳 (b) 方向** → 校验器改为「**只读校验**」：**允许**读 `parsed_dir` / assets / manifest 做 sha256 对齐，**禁写盘、禁 DB**。落地：spec-lead 写入 `plan.md` **§8 TL 决策表（Q12）**，T003 按此实现。原提请与两案对比：`tasks.md` T003 同时要求校验器承担「asset sha256 与 manifest 不对齐 → 校验失败」（L113）与「校验器为**纯函数**，不写盘、不读 DB」（L114），但 sha256 对齐必然要读取资产与 manifest。**(a)** 校验器只接**已加载**的 `manifest dict` + 预算 `asset_hashes`（IO 上移到 builder/service）；**(b)** 显式放宽为「允许只读传入 parsed_dir，禁写盘/DB」。不厘清则 T003/T004 会出现重复实现或漏校验。
- **Q-B（dry-run 语义，P2 前厘清）—— TL 定稿（2026-10-16）：采纳 4 态，QA 断言按此升级** → `would_build` / `would_skip(no_ir_source)` / `would_fail` / `already_built`。落地：spec-lead 写入 `plan.md` **§8 TL 决策表（Q13）**；**G-04 的判定即以这 4 态可区分为准**。原提请：A017-11 / TC017-46 只要求 stdout 含 `would_build` / `would_skip`，未定义 **`would_skip` 是否包含 `NO_IR_SOURCE`**，也未定义校验失败候选的输出键；否则「跳过」与「失败」被混同，掩盖 G-04 的语义区分。
- **Q-C（`document_flat.json` 分叉 → 由 M5 关闭，留证）**：design `L169` 渲染器签名为 `document_ir.json → (rendered.md, rendered.flat.json)`，而 `L190` 明确「V1 可先不产出」，T006/T007 与 acceptance 只提 `rendered.md`，design §8 产物清单（L313）亦不含 flat 产物。**V1 口径只能取「不产出」**，须以 M5 否定断言显式关闭。
- **Q-D（`stats.blocks_low_confidence` 阈值缺口 → 由 M3 关闭，留证）**：design `L73` 有该统计项，但 `L162` 的「默认 0.5，可配」属 `image_quality_score` 口径，`plan.md` §2.3 无对应键 → 无阈值则该统计不可复现。

---

## 三、只读勘察结果：本仓真实 MinerU 产物

### 3.1 实测（只读命令，未改动任何文件）

| 勘察对象 | 结果 |
|---|---|
| `parsed/` 目录（`find . -type d -name parsed`） | **0 命中** |
| `raw_vault/` 目录 | **0 命中** |
| `content_list.json` / `middle.json` | **0 命中** |
| `document_flat.json` / `document_ir.json` / `rendered.md` | **0 命中** |
| `parse_manifest.json` | **0 命中** |
| 唯一 PDF | `backend/tests/fixtures/sample_dataset/pdf/hello.pdf`（440 B，902 样例） |
| `data/sample_dataset/` | 902 合成样例（txt/yaml/docx/pdf/md） |

**结论（一行）**：**无** —— 本仓**不存在**任何可直接只读利用的真实 MinerU `parsed/**` 产物，亦无 `raw_vault/**`。

### 3.2 对 017 的直接影响

1. **OQ-7（bbox 单位 3 篇实测）无法在本仓完成** → 须外部提供真实产物，或授权在隔离目录实际运行 MinerU。
2. **A017-08 / TC017-30 的 v2 路径只能靠现场构造 fixture 验证**（与 `plan.md` §6.3「fixture 全部现场构造」一致），真实 `ir_source/` 保留行为需 P6 E2E 补证。
3. **P6 E2E 需 Owner 提供 ≥3 篇真实 PDF**（建议含：数字版带表格、扫描件、含图带 caption 各 ≥1），否则 OQ-7 与「真实 MinerU 产物可解析」两项只能出「部分验证」。

---

## 四、既有代码锚点核对（只读实测，供 T001/T009/T011 与 QA 定位）

> 全部行号为**本次实测**（2026-10-16）。QA 断言一律以**函数名/符号**锚定，行号仅作导航。

| 锚点 | 实测位置 | 对 017 的意义 |
|------|----------|---------------|
| `ParsedArtifactPaths` 现有 4 键 | `backend/app/core/parsed_paths.py:7-11`（`parsed_dir`/`parsed_text`/`parsed_metadata`/`parse_manifest`） | T010 只做 additive 加 3 键，既有键不得改名（015 只读引用，见冲突矩阵 §1.1） |
| `_finalize_success_from_staging` 定义 | `backend/app/services/mineru_pdf_parser.py:570` | T009 的唯一改动落点 |
| **staging 删除点** | `mineru_pdf_parser.py:566-568`（**调用方 `finally`** 中 `shutil.rmtree(staging_dir)`） | **T009 硬约束**：`_finalize_success_from_staging` 返回后 staging 即被删除 → `content_list.json` / `middle.json` 的拷贝**必须在该函数内部**完成；`staging_dir` 作为参数传入（`:563`）可直接取用 |
| `_collect_assets` 仅收图片 | `mineru_pdf_parser.py:758-`（扩展名白名单 `:766`，`continue` 于 `:774`） | 精确证实 design §1.2 的「信息丢失点」：结构化中间件当前**必然**随 staging 删除而丢失 |
| `_clear_parsed_outputs` | `mineru_pdf_parser.py:805-815`（仅清三件套 + assets；精确行体见 §六 M7 留痕） | `--force` 场景需确认新产物 `document_ir.json` / `rendered.md` / `ir_source/` 是否纳入清理 → **已裁定：Q11 追加 unlink，见 §六 M7** |
| 证据链派发点 | `backend/app/services/evidence_chain.py:349`（`parser_name == "mineru"` → `_chunk_mineru`，否则 `_chunk_markitdown`） | T011 插桩点：须在 `:349` 之前探测 `document_ir.json` |
| `_chunk_markitdown` / `_chunk_mineru` | `evidence_chain.py:424` / `:507`；后者内部还 fallback 到前者（`:565-568`） | A017-09 / TC017-36 的「IR 缺位零行为变化」对照基线 |
| **M1 语义级铁证** | `evidence_chain.py:586` `f"parsed_text.md:{chunk.start_offset}-{chunk.end_offset}"`；`:590-591` `source_char_start=chunk.start_offset` / `source_char_end=chunk.end_offset` | chunk 侧字段**实名**即 `start_offset/end_offset`，与 design 逐字一致；`:586` 同时是 parsed_text.md 回引串，与 A017-03 对应（M1 的代码级支撑） |
| M1 字段实名载体 | `backend/app/models/evidence.py:13` `class KbDocumentChunk` → `:27-28` `start_offset/end_offset`；`:41` `class KbEvidence` → `:55-56` `source_char_start/source_char_end` | 两组字段**合法并存**；M1 收口只改 017 文档 token，**绝不连带改** `source_char_*` |
| `sql/migrations/` 现状 | 仅 `006_parse_registry_v1.sql` + `_down.sql` + `.gitkeep` | A017-11 / TC017-47 零迁移硬断言的**基线快照** |
| `config/` 现状 | 6 个 yaml；**无 `document_ir.yaml`**（017 新增）、**无 `quality_semantics.yaml`**（016 尚未开工） | M3 / M4 落地前提 |
| `specs/017-unified-document-ir/evidence/` | 本文件写入前**不存在**；由本文件新建 | M2 的 `bbox_unit_measurement.md` 属**实现期**产物，现在**不创建** |

---

## 五、两处漂移登记（**不改**，留待 017 收口窗口）

| # | 漂移 | 实测证据 | 处置 |
|---|------|----------|------|
| ① | `sql/001_init_schema_v1_1.sql:202` 列注释 `chunk_level VARCHAR(32) COMMENT 'page/section/semantic'` **未含 `block`** | 本次读文件 L201-202：`chunk_level VARCHAR(32) COMMENT 'page/section/semantic'` | S1 期间**禁改 `sql/**`** → 017 收口时随 P2 一并修，或明确豁免并留痕 |
| ② | design §1.2 引 staging 清理点为 `mineru_pdf_parser.py:526-540`，**与实测不符** | 实测：`rmtree` 在 `:566-568`（调用方 finally），`_finalize_success_from_staging` 定义在 `:570` | **T009 / QA 一律以函数名锚定**（`_finalize_success_from_staging` + 其调用方 `finally`），017 收口时顺带修 design 行号 |

---

## 六、M1~M6（我的建议 + team-lead 裁定）+ 追加提请

> 裁定口径**即刻生效**；**文件修改统一推迟到 017 收口窗口**由 spec-lead 执行（本清单不动 acceptance/test_cases）。

| # | 议题 | 我的建议（已交付） | **TL 裁定（生效）** | 017 收口时的动作 |
|---|------|--------------------|---------------------|------------------|
| **M1** | 字段命名冲突（**最高**） | 以设计稿 §3.2 为 schema 权威（**载体 = `docs/architecture/DOCUMENT_IR_DESIGN_V1.md`**，非 `specs/017-*/design.md`——后者不存在） | **`start_offset` / `end_offset`**（半开区间 `[start, end)`，与 010 offset 体系对齐） | **5 行 / 9 个 token**，落点与执行口径见 **§六.1**；执行人 = spec-lead，时点 = S1 收口后 |
| **M2** | bbox 单位实测证据制品 | 独立证据文件 | 制品 = `specs/017-unified-document-ir/evidence/bbox_unit_measurement.md`；字段 = 文档 sha256 + MinerU 版本 + 原始 bbox 样本 + 推断单位 + 冻结声明 | **017 实现期**产出（OQ-7 实测），P6 E2E 报告引用；**现在不创建**。**授权暂缓（TL 2026-10-16）**：本仓无真实 MinerU 产物（无 `parsed/`、无 `raw_vault/`；`content_list.json`/`document_ir.json`/`parse_manifest.json`/`rendered.md` 全 0 命中；PDF 仅 `hello.pdf` 的 **2 份同一 fixture**（各 440 B）→ **真实语料 0 篇**，见 §八.2）→ **无 input，建了也是空壳**；待 **Owner 供 ≥3 篇真实 PDF**（数字版带表格 / 扫描件 / 带 caption 图 各 ≥1）后再授权 |
| **M3** | 低置信阈值缺位 | 新增显式配置键 | `config/document_ir.yaml` 增 **`low_confidence_threshold`**（默认值须**显式声明**），并注明与 `image_quality_threshold` 语义不同（**块级 confidence vs 图像质量**） | 随 T010 落地 |
| **M4** | 与 016 阈值关系 | 不复用 | **017 不复用 016 阈值**（`config/quality_semantics.yaml` 归 016；该文件当前**尚不存在**）；017 图像质量启发式自持，并在 plan 声明「不复用」 | plan 声明 + 日后复用须走 017 plan 变更 |
| **M5** | `document_flat.json` 缺失断言 | 加否定断言 | 加否定断言：`parsed_dir` **无** `document_flat.json`、`kb_parsed_artifact` **无**对应 `artifact_type`（V1 不产出） | acceptance 增否定断言（依据见 §2.3 Q-C） |
| **M6** | 变异总数口径 | 以总数为准 | 统一按 **11/11** 为准；`5/5` 注明为 **A017-12 局部口径**；**QA 以 11 为准** | acceptance L105 / tasks T012 补注口径 |
| **M7** | `--force` 的 IR 产物清理范围（`document_ir.json` / `rendered.md` / `ir_source/`） | 随 T009 一并裁定；否则残留旧 IR → 「重建后仍读到旧 IR」的**假绿** | **TL 终裁（2026-10-16）= 取原「备选」为决策（Q11）**：**`_clear_parsed_outputs` additive 追加 unlink `document_ir.json` / `rendered.md` / `ir_source/`**（对应 `parsed_paths` 新键 `document_ir` / `rendered_md` / `ir_source`）。**理由（TL 现拼）**：引用缺口**就在 `_clear_parsed_outputs`**（014 **唯一** `--force` 调用点 `:512-513`），而原「首选 = 017 自身代码清理」**无法覆盖 014 `parse --force` 场景**（014 不调用 017）→ **首选不成立**。**授权 ✓**：Q11 行**本身即构成授权枚举**（`plan.md §8` = Dev 必遵权威表）→ **无需外部 STOP**。**红线**：**不得改 v1 行为分支** —— v1 无此三产物 → 新 unlink / `rmtree` 均为 **no-op**，v1 行为零变化。**追加要求**：若 017 自身 `build-document-ir` 亦有 `--force` → **须同集合自清**（实现细节，不另开 STOP）。实现落点（现拼）：清理体 `:810-813` 按 `artifacts` 的 3 个 key 迭代 → 在 `:810` 元组追加 3 个新键；`ir_source/` 属目录型，范式参照 `:814-815` 的 `assets_dir` | A017-11 / TC017-45 加**可证伪否定断言**（三条独立、逐条可判，一一对应三处新 `artifacts` 键）：① **`ir_source/` 目录不存在**（`not (parsed_dir / "ir_source").exists()`）；② **`document_ir.json` 不存在**；③ **`rendered.md` 不存在**；三条之外**并须「不得读到旧 IR」**（与 G-02 联动） |
| **M8** | `ir_source/` / `document_ir.json` 的 git 可见性 | 确认 `.gitignore` 覆盖 `parsed/` **且 017 实际落盘路径 ⊆ 该模式** | **已关闭（TL 现拼，2026-10-16）—— 不入 `plan.md §8` 决策表**：`.gitignore` **L23-26** = `raw_vault/`、`parsed/`、`curated/`、`quarantine/`。**落盘路径核实（本次现拼，回答 TL §四）**：017 三处新产物**全部在 `parsed_dir` 内** —— 输出根 `plan.md:64` = `parsed/by_hash/{sha256[:2]}/{sha256[2:4]}/{sha256}/`；`spec.md:56` `document_ir.json` 位于该根；`plan.md:52` = `ir_source_dir: "ir_source"  # parsed_dir 下相对目录`；`tasks.md:61` = 「017 仅在既有 `parsed_dir` 内 additive 落盘（`document_ir.json` / `rendered.md` / `ir_source/`）」→ **⊆ `parsed/` 成立**（`.gitignore:24` 无前导 `/`、无锚定 → 匹配**任意层级**的 `parsed` 目录，含未来出现的 `backend/parsed/…`）→ **M8 零待办、无需补 `.gitignore` 条目**（原提请的"路径未核"顾虑已消除） | 无需动作；QA 复验留痕见 §八.2 |

### 六.1 M1 落点与执行口径（授权执行人 = spec-lead，时点 = S1 收口后）

**权威载体（唯一）**：`docs/architecture/DOCUMENT_IR_DESIGN_V1.md` —— md5 `dc278a2aae3ef7dff45b242c7165d096`，**351 行**；`§3.2 blocks[] — 通用块` 在 **L84**；`L98` = `"start_offset": 1024`、`L99` = `"end_offset": 1158`、`L114` = 「回引 `parsed_text.md` 的 `[start_offset, end_offset)` 区间（与 010 chunk 的 offset 体系一致）」。017 文档自证：`spec.md` L10「Design doc: `docs/architecture/DOCUMENT_IR_DESIGN_V1.md`（权威细节以设计文档为准）」。**017 目录无 `design.md`（仅 5 文件）**——如有转述写成 `specs/017-*/design.md §3.2`，属路径错误，勿引作证据。

**裁定**：统一为 `start_offset` / `end_offset`（**改 017 文档，不动设计稿**）。

**落点：5 行 → 9 个 token 替换点**（`before` md5 为收口前值）

| # | 文件（before md5） | 行 | 现文 token（转义书写，见下方注） | 应改 |
|---|---|---|---|---|
| 1 | `specs/017-*/acceptance.md`（`293b651daf9b6bd5844a19b434dadadf`，120 行） | L26 | `offset\_start/offset\_end` | `start_offset/end_offset` |
| 2 | 同上 | L26 | `text[offset\_start:offset\_end]` | `text[start_offset:end_offset]` |
| 3 | 同上 | L28 | `offset\_end > len(text)` | `end_offset > len(text)` |
| 4 | `specs/017-*/test_cases.md`（`69e82420f1e56ea87f213752906e7c2f`，120 行） | L22 TC017-08 | `text[offset\_start:offset\_end]` | `text[start_offset:end_offset]` |
| 5 | 同上 | L23 TC017-09 | `offset\_end > len(text)` | `end_offset > len(text)` |
| 6 | 同上 | L24 TC017-10 | `offset\_start += 1` | `start_offset += 1` |
| | | | **合计 9 个 token（跨 5 行）** | |

> **书写约定（采纳 spec-lead 建议：本清单全文零未转义 token）**：表中与正文的 token 一律以 **`offset[_]start` / `offset[_]end`** 形式书写 —— `[_]` 即字面下划线，等价于未转义写法，且**已实测 `grep` 匹配能力无损**（见下方验证段实证）。如此**本清单自身不再构成收口后的残留命中**，正式判据遂由「**文件作用域**（acceptance + test_cases）」**升级为「目录作用域」（`specs/017-unified-document-ir/` 全目录）**；原「全仓 grep 不是有效判据」的例外说明**随之删除（caveat 归零）**。

**不变量（防误改，逐条排除）**

1. **`docs/architecture/DOCUMENT_IR_DESIGN_V1.md`（`dc278a2a…`）不改** —— 它是权威正确来源；本项只改 017 文档。
2. **`source_char_start` / `source_char_end` 绝不可连带改** —— evidence 侧**独立字段**，与 chunk 的 `start_offset/end_offset` **合法并存**：`specs/010-evidence-chain/p3_implementation_gate.md:305`、`backend/app/models/evidence.py:55-56`、`backend/app/services/evidence_chain.py:82-83`。误改将破 010 既有契约。
3. **只做 token 级替换，禁止整行重写** —— L26 含全角标点与反引号，整行重写易引入隐藏差异。
4. **L28 / L23 的否定断言语义、L24 的「变异 → FAIL → 恢复后 PASS」结构一字不动**，仅替换 noun。
5. **`§3.2` 在 017 内二义** —— `specs/017-*/plan.md` L73 另有 `### 3.2 解析器侧变更（唯一前置改动）`（讲 `ir_source/` + `parser_profile=mineru_ir_v2`，与命名无关）。**引用一律写全路径** `docs/architecture/DOCUMENT_IR_DESIGN_V1.md §3.2`。

**判定依据：纯文档笔误、零实现耦合**（本次独立复核）

- **现拼命中（两点实证，勿只引其一）**：**转义前 = 8 行** —— `acceptance.md` L26/L28（**2**）+ `test_cases.md` L22/L23/L24（**3**）+ **本清单 3 处散文**；**转义完成后 = 5 行**（仅剩 017 两文档）→ 该 8→5 回落即「本清单转义已生效」的直接证据。**代码侧（`*.py`）= 0 命中**。转义前的 8 行构成与 spec-lead 独立复核**逐字一致**。
- chunk 侧代码**实名**即 `start_offset/end_offset`：`models/evidence.py:27-28`、`evidence_chain.py:586`（`f"parsed_text.md:{chunk.start_offset}-{chunk.end_offset}"`）、`:590-591`（`source_char_start=chunk.start_offset` / `source_char_end=chunk.end_offset`）。
- 故「三处各写一套」的风险**只存在于 017 文档内部**，不影响任何已实现行为。

**状态写法**：**M1 = 已裁定、待 spec-lead 收口**。上表 md5 为**收口前**值；收口后由 spec-lead 报新 md5；**017 验收一律以收口后 md5 为准**。

**收口后验证（目录作用域 —— 已无需限定到文件）**：`grep -rnE 'offset[_]start|offset[_]end' specs/017-unified-document-ir/` **应为空**（**全目录**，含本清单）。

> **命令自证（两点实证，现拼）**：① **转义前**：`[_]` 写法回显 **8**、未转义写法回显 **8** → **逐条一致（8 = 8）**，证明 `[_]` 写法**匹配能力无损**、可原样复制执行；② **转义完成后**：`[_]` 写法回显 **5**（仅剩 017 两文档）→ 证明本清单 **3 处转义均已生效**。该命令文本**自身不含未转义 token**，故不污染判据。

并须同时确认三处「未改」：
- `docs/architecture/DOCUMENT_IR_DESIGN_V1.md` md5 仍为 `dc278a2aae3ef7dff45b242c7165d096`（权威源未改）；
- `backend/app/models/evidence.py` md5 仍为 `646b8dc11d73393bc66c27fbaa1e9385`（`source_char_*` 未被连带改）；
- `backend/app/services/evidence_chain.py` md5 仍为 `b4815f76c32e900251ff3e7b41272c09`（`:586` / `:590-591` 未被连带改）。

**017 全量基线 md5（现拼 2026-10-16，收口前）**

| 文件 | md5 | 行数 |
|---|---|---|
| `docs/architecture/DOCUMENT_IR_DESIGN_V1.md` | `dc278a2aae3ef7dff45b242c7165d096` | 351 |
| `specs/017-*/spec.md` | `0416ea9213b3e7456ca1d6d4b05772c2` | 78 |
| `specs/017-*/plan.md` | `857083cef37774a01c2de3eda88b4c09` | 231 |
| `specs/017-*/tasks.md` | `1603d2dd7d98dd6ffc83576a34574f1b` | 254 |
| `specs/017-*/acceptance.md` | `293b651daf9b6bd5844a19b434dadadf` | 120 |
| `specs/017-*/test_cases.md` | `69e82420f1e56ea87f213752906e7c2f` | 120 |

**追加提请 → 已全部裁定（M7 / M8 见上表；**无遗留未裁项**）**

**M7 证据留痕（本次现拼逐行对照，`backend/app/services/mineru_pdf_parser.py`）**

| 现拼行号 | 内容 | 与 M7 的关系 |
|---|---|---|
| `:502` | `artifacts = build_parsed_artifact_paths(parsed_dir)` | `artifacts` 字典来源；其键即 `plan.md:104` 所述 `ParsedArtifactPaths` 的 `document_ir` / `rendered_md` / `ir_source` |
| **`:512-513`** | `if force:` → `self._clear_parsed_outputs(artifacts, assets_dir)` | **`--force` 的唯一清理调用点**（现拼：全仓该函数 **1 处 def + 1 处 call**） |
| `:563` | `_finalize_success_from_staging(... staging_dir=staging_dir, ...)` | staging 交接（TL 引「`:563` 传参」**属实**） |
| `:566-568` | `finally:` → `if staging_dir.exists(): shutil.rmtree(...)` | staging 清理（TL 引「`:566-568` finally rmtree」**属实**） |
| **`:805-815`** | `def _clear_parsed_outputs(...)` —— **签名 `:805-809`、逻辑体 `:810-815`** | 清理范围本体 |
| `:810-813` | `for key in ("parsed_text", "parsed_metadata", "parse_manifest"):` → `path.unlink()` | **仅清三件套**，**未含** `document_ir` / `rendered_md` / `ir_source` |
| `:814-815` | `if assets_dir.is_dir(): shutil.rmtree(assets_dir, ignore_errors=True)` | 目录型产物的现成处理范式（`ir_source/` 参照） |

**行号纠偏（防第三次留痕错位）**：清理范围一条曾并存两个**略有偏差**的引用 —— TL 的 `:806-814` 与我的原稿 `:809-815`；**现拼精确值 = `:805-815`**（def 在 `:805`，签名续至 `:809`，逻辑体 `:810-815`）。**上表为唯一判据。**

**M7 前提成立**：`--force` 的清理集合 = `parsed_text` / `parsed_metadata` / `parse_manifest` 三件套 + `assets_dir` → `document_ir.json` / `rendered.md` / `ir_source/` **均不在其内** → 残留旧 IR → 「重建后仍读到旧 IR」= **假绿**。

**M8 证据留痕（本次现拼，2026-10-16）**：`.gitignore` 运行期段 L22-L27 = `source_registry/` / **`raw_vault/`** / **`parsed/`** / **`curated/`** / **`quarantine/`** / `reports/`。
- **判据一（模式）**：L24 = `parsed/`（**无前导 `/`、无锚定**）→ 匹配**任意层级**的 `parsed` 目录。
- **判据二（实际落盘路径 ⊆ 该模式，回答 TL §四）**：017 三产物落盘根 = `parsed/by_hash/{sha256[:2]}/{sha256[2:4]}/{sha256}/`（`plan.md:64`、`spec.md:56`）；`ir_source/` = `parsed_dir` 下相对目录（`plan.md:52`、`plan.md:70`）；`tasks.md:61` 明述「仅在既有 `parsed_dir` 内 additive 落盘（`document_ir.json` / `rendered.md` / `ir_source/`）」→ **无任何产物落在 `parsed/` 模式之外**。
- **结论 → M8 关闭、零待办**（无需补 `.gitignore` 条目），与 TL 亲核一致；原提请的"未逐行核对 / 路径未核"两处顾虑均已由本次实测消除。

**另：一项需 Owner（不阻塞，TL 已上报）**
- **真实 MinerU 产物缺失（2026-10-16 现拼复核，与 TL 结论一致）**：本仓无 `parsed/` / `raw_vault/` 目录；`content_list.json` / `document_ir.json` / `parse_manifest.json` / `rendered.md` **全 0 命中**；全仓 PDF **仅 2 份、且为同一 fixture**（`backend/tests/fixtures/sample_dataset/pdf/hello.pdf` 与 `data/sample_dataset/pdf/hello.pdf`，各 **440 B**，md5 同为 `daeaf29317539048e1f537b8188518b0`）→ **真实语料 = 0 篇** → **P6 E2E 与 OQ-7 三篇实测需 Owner 供 ≥3 篇真实 PDF**（数字版带表格 / 扫描件 / 带 caption 图 各 ≥1）。因缺 input，**M2 的 `bbox_unit_measurement.md` 授权暂缓**。

---

## 七、QA 门禁清单（8 条，可执行断言）

| # | 门禁 | 通过条件（**可执行断言**） |
|---|------|---------------------------|
| G-01 | 契约与枚举 | A017-01 全过；`assert all(b["type"] in BLOCK_TYPES for b in blocks)` 为真；7 必填键不可缺 |
| G-02 | 稳定性与幂等 | A017-02（跨重跑逐字节一致）+ A017-11（二次运行不重写、登记无重复行）；`--force` 重建正确 |
| G-03 | fail-closed 六分叉 | A017-01/02/03/06/08/10 六变异全部被捕获（变异→FAIL、恢复→PASS） |
| G-04 | 语义区分 | v1 无 ir_source → `NO_IR_SOURCE` **跳过**；content_list **非法** → `FAILED`；两者在报告与 dry-run 输出中**可区分**（见 Q-B） |
| G-05 | 红线不可破 | `parsed_text.md` / `parsed_metadata.json` 内容+hash 前后完全不变；`raw_vault/**` listing + sha256 不变 |
| G-06 | S2 边界 | `field_value` **零数据行**；`kb_document_chunk` / `kb_evidence` **零新增列**；v1 `section_quote` uid 值零变化 |
| G-07 | 零迁移 | **作用域限定**：`git --no-pager status --porcelain sql/migrations/` 输出为空（**不得**用全仓 `git status`——本仓当前存在 8 个 ` M` 条目与大量 `??`（含 `.ua/`、`.codebuddy/`、新增 specs 等），全仓断言必然假红）。**本次实测该命令输出为空 → 零迁移基线成立** |
| G-08 | 回归基线 | 017 新建三测试文件全绿；既有 `test_mineru_pdf_parser.py` / `test_markitdown_parser.py` / `test_evidence_chain.py` / `test_parser_router.py` **零修改**通过；001–016 全量**无新增失败**。**口径（TL 批准，与 014 同）**：Typer/Click `--help` 类 4 项既有红点（`test_evidence_chain` / `test_markitdown_parser` / `test_parser_router` / `test_search_service`）**不计缺陷、不得修复**；基线 = 全量 **4 failed / 253 passed / 129 skipped**，断言只判「无新增失败」 |

**变异总数（M6 口径）**：**11/11 必须全部被捕获**，缺一不通过；`5/5` 仅为 A017-07（2）+ A017-12（3）的局部口径。若 QA 只跑「5/5」会漏掉 6 条分叉变异（TC017-04/07/10/25/33/43）。

---

## 八、边界遵守与请求

**本轮已遵守（team-lead 四条边界）**

1. **未开工 017 实现** —— 本文件为文档，无任何 `backend/**` 写入（`SPEC_INDEX.md:106`）。
2. **未进 `cli/main.py`** —— 其归属 015（实现 = pilot-014）；本轮未读取未修改。
3. **未动 `mineru_pdf_parser.py`** —— 014 冻结 + 017 硬冲突；仅**只读**勘察（§四）。
4. **只读勘察** design 与 `specs/017-*`，**唯一写动作** = 本文件。

**请求与处置（2026-10-16 更新）**

1. **M7 / M8 —— 已裁定 / 已关闭（✓）**：M7 **TL 终裁 = 取原「备选」为决策（Q11）** —— `_clear_parsed_outputs` **additive 追加 unlink** `document_ir.json` / `rendered.md` / `ir_source/`；原「首选 = 017 自身代码清理」经 TL 现拼判定**无法覆盖 014 `parse --force` 场景**（014 不调用 017）→ 不成立；**授权由 Q11 行本身构成、无需 STOP**；**红线 = 不改 v1 行为分支**（v1 无三产物 → unlink / `rmtree` no-op）。**可证伪断言已细化为三条独立判据**（`ir_source/` 目录不存在 / `document_ir.json` 不存在 / `rendered.md` 不存在，一一对应三处新 `artifacts` 键）—— 见 §六 M7 行 + 本节证据留痕（含**行号纠偏**）。M8 **已关闭（TL 现拼，2026-10-16）、零待办、不入 `plan.md §8` 决策表**：`.gitignore` L23-26 覆盖 `parsed/`，**且 017 三产物落盘路径经现拼核实 ⊆ `parsed/` 模式**（结论：**无待办项、无需补 `.gitignore` 条目**）。
2. **Q-A / Q-B —— 已定稿并转 spec-lead（✓）**：Q-A = 校验器「**只读校验**」（允许读 parsed_dir / assets / manifest 做 sha256 对齐，**禁写盘、禁 DB**）；Q-B = dry-run **4 态**（`would_build` / `would_skip(no_ir_source)` / `would_fail` / `already_built`）。**落地 = spec-lead 写入 017 `plan.md` §8 TL 决策表（Q12 = Q-A；Q13 = Q-B）** —— TL 已终裁**单一落点 §8**（`plan.md §2.3` 为「配置」段、无裁定语义）；**QA 断言按定稿口径，不再沿用旧二态**。
3. **M2 授权状态**：`bbox_unit_measurement.md` **授权暂缓**（本仓无真实 MinerU 产物 → 无 input，建了是空壳），待 Owner 供 ≥3 篇真实 PDF 后另行授权；**在此之前不创建**。
4. **需 Owner 的一项**：供 **≥3 篇真实 PDF**（数字版带表格 / 扫描件 / 带 caption 图 各 ≥1）→ 解锁 P6 E2E、OQ-7 三篇实测、M2。
5. **015 落盘后**：按本条「五步校验」思路承接 015 独立验收（待 team-lead 派工）。

---

**本文件版本历史（供后续 QA 锚定；**引旧版 md5 属快照过期**）**

| 版本 | md5 | 行数 | 内容 |
|---|---|---|---|
| v1（首版落盘，team-lead 已核实） | `086c019a41c3535aa84a2140dafd81fb` | 174 | 初次落盘：M1~M6 建议 + §2.3 四问（Q-A~Q-D）+ §七 门禁清单 |
| v2 | `d909110b98a83850e1aec61c6ac0b578` | 226 | 按 M1「**5 行 / 9 token**」口径重写：新增 §六.1 落点表、代码级铁证、防误改不变量、收口后验证基线、017 全量基线 md5 |
| **v3（含两轮追加）** | 每轮追加后即变 → **一律以交付回执「现拼 md5」为准**（自指，不可内嵌当前值） | — | **第 1 轮**：M7 裁定（升级硬性要求）+ M8 关闭 + Q-A/Q-B 定稿 + M2 授权暂缓 + 需 Owner 项 + 本节版本历史。**第 2 轮**（依 TL §三/§四 + spec-lead 建议）：M7 **三条可证伪断言**（`ir_source/` / `document_ir.json` / `rendered.md` **各不存在**）+ `_clear_parsed_outputs` **行号纠偏至精确 `:805-815`**（并新增 `--force` 唯一调用点 `:512-513`）+ M8 **落盘路径 ⊆ `parsed/` 现拼核实** + 全文 token 改写为 `offset[_]start` / `offset[_]end`（**零未转义 token**）→ 判据**升级为目录作用域**、原 grep caveat 删除 |
| **v4（本文件终版，已冻结）** | 见交付回执现拼值 | — | **第 3 轮（TL 终裁 + 二义收敛）**：① 全文「`plan.md` §2.3」→「`plan.md` **§8 TL 决策表（Q11/Q12/Q13）**」（§2.3 标题 / Q-A / Q-B / §八.2 四处），**收敛为单一 §8、二义消除**；② **M7 终裁 = 取原「备选」为决策（Q11）** —— 首选因「无法覆盖 014 `parse --force`」被 TL 现拼否决，改为 `_clear_parsed_outputs` additive 追加 unlink 三产物、**授权无需 STOP**、红线不改 v1 行为分支；③ 行号遗留 `:809-815` → 精确 **`:805-815`**（落点表）；④ M8 标注「**已关闭（TL 现拼，2026-10-16）**、不入 `plan.md §8` 表」。**TL 令：改毕冻结。** |

> 🧊 **冻结声明（2026-10-16，TL 令）**：v4 落盘后，**本文件非新 TL 裁定不再编辑**。冻结依据：本夜 v1→v2→v3→v4 期间发生 **3 次「核实值被并行追加瞬间作废」**（v2 `d909110b…`；round-2 `be51b1f8…`/266 行；v1 读旧快照 `086c019a…`/174 行），交叉核对成本已超收益。

> ⚠️ **锚定纪律（本项目已知故障模式，两条）**
> 1. **现拼优先**：2026-10-16 team-lead 的一次核实回执引用 `086c019a41c3535aa84a2140dafd81fb` / **174 行** —— 经现拼复核，该值**对应 v1 旧快照**，盘上实际已是 v2/v3。**任何验收、留痕、白名单校验一律以「现拼 `md5sum` + `stat` mtime」为唯一判据，勿沿用旧快照值**（与 §七 G-07 的「作用域限定」同属防假绿纪律）。
> 2. **核实与追加必须串行**（本轮新增）—— 并行会使核实值**瞬间过期**：同一时刻 team-lead 核实 v2（`d909110b98a83850e1aec61c6ac0b578` / 226 行 / 22907 B，**当时为真、非误读**）与我执行 v3 追加**并行发生**，故该值在本文件 v3 落盘后即失效。**规约：核实回执必须附「被核实版本的 md5」；本文件每次追加后须重报新 md5，并声明此前核实结论对旧值有效、对新值需重核。**

---

**QA_PREP_017 结束** —— 本文件仅为**验收准备**，不构成对 017 任何验收结论；017 实现须待 S1 合并后启动（`SPEC_INDEX.md:112`）。

---

## 九、行号锚点再纠偏（v5，team-lead 2026-10-17 变更后现拼）

**触发**：014 冻结件 `backend/app/services/mineru_pdf_parser.py` **再次变更** —— md5 `d8383fdedbfbe161963636b323d08f25` → **`a82a3653ec7998d30eabfd16e99b0a9e`**（现盘 **980 行**，`git diff --stat` = **+70 / −7**）→ §四、§六 M7、版本历史中的行号**第三次漂移**。以下数值**全部由本人在 2026-10-17 亲自实测**（`grep -n` + `read_file` 逐行核对 + **两轮复测 md5 恒定**），未采信任何转述值。

### 9.1 原引用 → 现盘（**唯一判据，覆盖本文件此前全部行号表述**）

| 017 原引用 | 原语义 | **现盘实测（2026-10-17）** |
|---|---|---|
| `:502` | `artifacts = build_parsed_artifact_paths(parsed_dir)`（parse 主流程内） | **`:519`**（另有一处 **`:447`** 属其它方法，**勿混用**） |
| **`:512-513`** | `if force:` → `self._clear_parsed_outputs(artifacts, assets_dir)` | **`if force:` 在 `:529`；调用在 `:530`** |
| `:563` | `_finalize_success_from_staging(... staging_dir=staging_dir ...)` 调用 | **`:571`** |
| `:566-568` | `finally:` → `if staging_dir.exists():` → `shutil.rmtree(staging_dir)` | **`:583`** / **`:584`** / **`:585`** |
| `:570` | `def _finalize_success_from_staging` | **`:587`** |
| `:758-` | `def _collect_assets` | **`:794`** |
| `:766` | 图片扩展名白名单（`image_exts`） | **`:802`** |
| `:774` | `continue`（非图片跳过） | **`:811`**（另 `:809` 为「非文件」的 `continue`，勿混） |
| **`:805-815`** | `def _clear_parsed_outputs(...)`：签名 `:805-809`、逻辑体 `:810-815` | **def 在 `:841`，签名 `:841-845`，逻辑体 `:846-851`** |
| `:810-813` / `:810` | 三键元组迭代起点（017 须在此追加 3 个新键） | **元组行 `:846`；循环体 `:846-849`** |
| `:814-815` | `assets_dir` 的 `rmtree` 范式（`ir_source/` 目录型清理参照） | **`:850-851`**（`if assets_dir.is_dir():` `:850`、`shutil.rmtree(...)` `:851`） |

**唯一点校验（仍成立）**：`mineru_pdf_parser.py` 内 `_clear_parsed_outputs` = **1 处 def（`:841`）+ 1 处 call（`:530`）**。**注意**：`backend/app/services/image_parser.py` 另有**同名独立方法**（def `:815` / call `:852`，属 015 新文件），与 014 冻结件无关，**引用时须带文件名**。

### 9.2 全 017 目录陈旧引用判定（改 / 不改清单）

- **真陈旧（须以 §9.1 覆盖，但按红线不原地改）** —— 共 **9 处，全部位于本证据文件**：`:99`、`:131`、`:197`、`:200`、`:201`、`:202`、`:204`、`:260`、`:261`。其中 `:197`~`:204` 为 M7 落点表、`:99`/`:131` 为锚点与裁定正文、`:260`/`:261` 为 v3/v4 版本历史。**处置 = 一律不删不改**（只 append），历史行号作为**留痕事实**保留；现盘判据**只认 §9.1**。
- **属历史记录、明确不改**：v3/v4 描述「行号纠偏至精确 `:805-815`」是**当时**的现拼事实，改之即篡改留痕。
- **无关数字 / 仍然有效、不改**：`plan.md:64`、`plan.md:52`、`plan.md:70`、`plan.md:104`、`tasks.md:61`、`spec.md:56`、`SPEC_INDEX.md:106` / `:112` —— 这些是**文档自身行号**，非代码行号；且相关文档现拼 md5 **未变**（`plan.md` `857083ce…`、`tasks.md` `1603d2dd…`、`acceptance.md` `293b651d…`、`test_cases.md` `69e82420…`、`spec.md` `0416ea92…`）→ **依然有效**。
- **017 五件套正文无代码行号**：`plan.md` / `acceptance.md` / `tasks.md` / `test_cases.md` / `spec.md` 对 `mineru_pdf_parser` 的引用**均为路径或符号，零 `mineru_pdf_parser.py:NNN` 形式**（`\.py:[0-9]+` 在该五件套**零命中**）→ **与 team-lead 初查一致，正文无需修正**。
- **本轮未复核（不在本次范围）**：`evidence_chain.py:349/:424/:507/:565-568/:586/:590-591`、`models/evidence.py:13/:27-28/:41/:55-56`、`parsed_paths.py:7-11`、`sql/001_init_schema_v1_1.sql:202` —— 均属**其它文件**，本次 014 变更未触及，但**未逐个现拼**，017 开工时须重测。

### 9.3 结论（硬要求，写入 017 实现规范）

**017 实现期一律改用符号引用**（`MineruPdfParser._clear_parsed_outputs` / `if force:` 分支），行号仅作参考，**避免第四次漂移**。

符号化对照（实现与 QA 一律照此表述）：清理函数 = `MineruPdfParser._clear_parsed_outputs`（签名 `(self, artifacts: dict[str, Path], assets_dir: Path) -> None`）；调用点 = `if force:` 分支内唯一调用；staging 交接 = `_finalize_success_from_staging` 的 `staging_dir=` 参数；staging 删除 = 调用方 `finally` 中的 `shutil.rmtree(staging_dir)`；图片收集 = `_collect_assets` 与其 `image_exts` 白名单；目录型清理范式 = `_clear_parsed_outputs` 末尾 `assets_dir` 的 `if … is_dir()` + `rmtree`。

### 9.4 本轮留痕与边界

- **改前**（append 前现拼）：`e0f536db585b4cc111199ddadb478c5f` / **271 行**。**改后**：**315 行**（271 + 本节 44 行，append-only），md5 **见交付回执现拼值**（自指，不可内嵌；与本文件 v3/v4 既定口径一致）。原 **v1~v4 内容逐字未动**，仅文件末尾 append 本节。
- **零写入其它文件**：未改 `backend/app/**`、`backend/tests/**`、`config/**`、`specs/017-*/plan.md|acceptance.md|test_cases.md`、`tasks.md`、`spec.md`；**未 `git add`**（本文件仍为未跟踪 `??`）。
- **异常观测（防误判留痕）**：`mineru_pdf_parser.py` 的 mtime 在本轮实测期间由 `2026-10-17 12:20:41` 变为 `13:14:08`（**内容 md5 未变**，仍 `a82a3653…` / 980 行）；其间一次 `grep -n` **瞬时返回 def `:840`**，而前后两次 `read_file` 与复测均为 `:841`，两轮 `md5sum` 恒定 → 判定为**写盘竞态下的瞬时读**，最终判据取 **`:841`**。**含义**：该冻结件存在被重复写入（内容相同）的迹象，**任何行号断言都须当场现拼**，不得跨时点复用。

### 9.5 附注：`13:14:08` mtime 变化的性质（team-lead，2026-10-17）

`anchor-017` 观察到 `mineru_pdf_parser.py` mtime 由 `12:20:41` 变为 `13:14:08` 而内容 md5 恒定，据此提示「有被重复写入（内容相同）的迹象」。**现拼归因如下**：

- 该次 mtime 变化是 **`qa-014-delta` 的变异复验还原**所致：其变异态 md5 `7f6c53f10f2651e2f766fabb244ee584`，逐字还原后 `a82a3653ec7998d30eabfd16e99b0a9e`，完成时点 13:14 CST，与本文件 mtime 吻合。
- 即：时间窗内确有一次写盘，但**内容与还原前逐字相同**；非并发写、非纪律问题。`:840` / `:841` 的瞬时读取差异属读取时序抖动。

**审计口径不变且更强**：行号断言**必须当场现拼**；017 已按 §9.3 改为符号引用，不再受此影响。
