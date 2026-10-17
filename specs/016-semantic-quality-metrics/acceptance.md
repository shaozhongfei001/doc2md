# Acceptance: 语义质检指标（016）

> **Spec**：`specs/016-semantic-quality-metrics/`
> **Plan 对照**：`plan.md` §10 / §9
> **测试文件**：`backend/tests/test_parse_quality_checker_semantic.py`
> **反假绿原则**：每个 issue code 必须同时证明「该报时报」与「不该报时不报」，并用**阈值变异**证明断言路径真实接线、非空转。

---

## A016-01 OCR_GARBLED_RATIO 双向断言

- 乱码 fixture：`parsed_text.md` 为 2000 字符中文段落混入 5%（100 个）U+FFFD + 控制符 → 报告含 `OCR_GARBLED_RATIO`，severity=WARNING（≥0.02 且 <0.10），`evidence.garbled_ratio` 实测值 ∈ [0.04, 0.06]。
- 重度乱码 fixture（15%）→ severity=ERROR。
- **对照**：干净中文 fixture → 无 `OCR_GARBLED_RATIO`。
- **否定断言**：干净 fixture 的 issue 列表为空集断言必须显式存在（`assert "OCR_GARBLED_RATIO" not in codes`）。

## A016-02 样本不足不评估（反假绿）

- 正文 50 字符且全为 U+FFFD 的 fixture → **无** `OCR_GARBLED_RATIO`（`total_chars < min_sample_chars=200` 整体跳过）。
- **对照**：同内容重复填充至 ≥200 字符 → 必须产生 issue。证明「跳过」是显式逻辑而非检测器失效。

## A016-03 OCR_LOW_CONFIDENCE 双向断言 + 反编造

- `ocr_confidence.mean=0.55` → `OCR_LOW_CONFIDENCE`（WARNING），evidence 含实测值与阈值。
- `ocr_confidence.mean=0.95` → 无此 issue。
- `parser_name ∈ {mineru, image_ocr}` 且 metadata **无** `ocr_confidence` 字段 → 仅 `OCR_CONFIDENCE_UNAVAILABLE`（INFO）。
- **否定断言（反编造核心）**：缺字段情形下 `assert "OCR_LOW_CONFIDENCE" not in codes` 必须显式存在。
- `parser_name="markitdown"` 且无 confidence 字段 → 两个 code 都不产（非 OCR 族不评估）。

## A016-04 TABLE_STRUCTURE_INCOMPLETE 双向断言

- ragged fixture：一个 3 列 pipe 表，其中一行只有 2 列 → `TABLE_STRUCTURE_INCOMPLETE`（WARNING），evidence 含残缺行号。
- **对照**：规范表格 fixture（列数全一致）→ 无此 issue；纯文本无表格 fixture → 无此 issue（不评估）。

## A016-05 EMPTY_BUT_NONZERO_PAGES

- manifest `status=SUCCESS`、`content_size_bytes=54321`，`parsed_text.md` 全空白 → ERROR `EMPTY_BUT_NONZERO_PAGES`。
- **对照 A**：manifest `status=EMPTY` 同条件 → 不产此 code（既有 `REGISTRY_EMPTY_RESULT` 已覆盖）。
- **对照 B**：`status=SUCCESS` 且正文非空 → 不产。

## A016-06 阈值接线变异复验（反假绿核心）

- 对乱码 fixture（5%）：注入 `warn_ratio=0.50`（调高）→ `OCR_GARBLED_RATIO` **消失**；恢复 0.02 → 复现。
- 对 confidence fixture（0.55）：注入 `min_mean=0.10` → `OCR_LOW_CONFIDENCE` **消失**；恢复 0.70 → 复现。
- 两组变异任一未被捕获（阈值改了 issue 还在），验收不通过——证明阈值不是装饰性配置。

## A016-07 `--no-semantic` 冻结 008 行为

- 同一批候选分别跑默认与 `--no-semantic`：后者 `schema_version="1.0"`、`issue_counts` 仅 18 个既有键、不含任何语义 code、无 `semantic_checks_enabled` 键。
- **否定断言**：`--no-semantic` 报告中 `parsed_text.md` 内容未被读取（monkeypatch `Path.read_text` 计数器为 0 或等价守卫）。

## A016-08 只读红线与幂等

- 检查前后：`parsed/**` 与 `raw_vault/**` 的 listing + 全文件 sha256 不变；DB 会话 SELECT-only（审查 + 测试 DB 行数快照对比）。
- 连续两次 `check-parse-quality`（同配置）：除 `generated_at` 与报告文件名外，issue 集合逐字段一致。
- 单候选 `parsed_text.md` 读取 OSError（monkeypatch）：该候选跳过语义检查，其余候选语义 issue 正常产出，批处理不中断。

## A016-09 009 summarizer 联动

- 含 5 个新 code 的报告喂给 009 `summarize-parse-quality`：不抛 unknown code 错误，新 code 出现在汇总计数中。
- `--issue-codes OCR_GARBLED_RATIO` 过滤可用（`parse_quality_report_summarizer.py:528-529` 的合法性校验接受新 code）。
- 015 的 `parser_name="image_ocr"` manifest 经 008+016 检查：**不**再产 `MANIFEST_PARSER_NAME_INVALID`。
- **回归**：`test_parse_quality_checker.py`、`test_parse_quality_report_summarizer.py` 既有用例零修改全过。

## A016-10 中文路径与全链路

- `中文路径` 目录下的 parsed 产物（中文正文 + 乱码混入）走 `check-parse-quality`：issue evidence 中文无乱码（`ensure_ascii=False`），路径字段正确。
- 001–013 全量 pytest 回归通过。

---

## 验收门禁汇总

| 门禁 | 通过条件 |
|------|----------|
| 五类语义检测 | A016-01 ~ A016-05 全过（每条含对照/否定断言） |
| 反假绿 | A016-02、A016-03（反编造）、A016-06（阈值变异）全过 |
| 兼容冻结 | A016-07、A016-09 全过 |
| 安全红线 | A016-08、A016-10 全过 |

---

## QA 独立验收结论（qa-016，2026-10-17）

> 角色：**独立验收 QA**（非实现者）。结论只采信本人实测，不采信实现者自述数字。
> 验收对象：`5821c9c feat(016): 008 质检第五类语义检查`（QA 期间另有 `72078ab`，仅改
> `specs/016-semantic-quality-metrics/tasks.md`，`git show --stat 72078ab` 确认零代码改动）。
> 环境：`/home/szf/anaconda3/bin/python` 3.11.7（系统 python3 3.10 不可用）；
> `cd backend && PYTHONPATH=. <py> -m pytest -q`；MySQL 段由 `config/app.yaml` 注入
> `PKB_MYSQL_*`（密码未落盘、未输出）。

### 0. 基线复现（我实测，不是转述）

| 项 | 命令 | 实测 |
|----|------|------|
| 016 新文件 | `pytest -q tests/test_parse_quality_checker_semantic.py` | **61 passed** |
| 008 零修改 | `pytest -q tests/test_parse_quality_checker.py` | **30 passed** |
| 009 | `pytest -q tests/test_parse_quality_report_summarizer.py` | **31 passed** |
| 三文件合跑 | 同上三文件 | **122 passed** |
| 全量 | `pytest -q` | **4 failed / 498 passed / 2 skipped** |

- 4 failed 全部是既有 click 8.4.1 × typer 0.15.1 `--help` 基线，**不计入 016**：
  `test_evidence_chain::test_cli_help_documents_contract`、
  `test_markitdown_parser::test_cli_parse_markitdown_help`、
  `test_parser_router::test_cli_route_parsers_help`、
  `test_search_service::test_cli_help_documents_contract`。
- 用例数更正：`5821c9c` 提交信息写「59 用例 / 496 passed」是实现方异步写盘的中间态；
  实测 `--collect-only` = **61**（59 个 `def test_` + `test_missing_confidence_info_only` 参数化 2 例），
  全量 **498 passed**。`72078ab` 已把留痕更正为 61/498，与实测一致。

### 1. 逐条判定（A016-01 ~ A016-10）

| 项 | 判定 | 实测证据（本人跑出的数字） |
|----|------|--------------------------|
| A016-01 乱码率双向 | **PASS** | 5%（100/2000）→ `OCR_GARBLED_RATIO` WARNING，`evidence.garbled_ratio=0.05`、`threshold_warn=0.02`/`threshold_error=0.1`、`sampled=True`、`issue_counts=1`；15%（300/2000）→ ERROR，`ratio=0.15`；干净 2000 字 → `issues==[]`。否定断言 `test_clean_text_no_garbled_issue:388` 显式存在 |
| A016-02 样本不足整体跳过 | **PASS** | 50×U+FFFD → `issues==[]`（`test_parse_quality_checker_semantic.py:412-414`）；同内容填至 250 字 → `ratio=0.20` ERROR；`min_sample_chars=100` 注入后 150 字同正文变红（`:429-440`）。变异 M4 证明「跳过」是显式分支 |
| A016-03 置信度双向 + 反编造 | **PASS** | `mean=0.55` → `OCR_LOW_CONFIDENCE` WARNING（`confidence_mean=0.55`、`threshold_min_mean=0.7`）；`0.95` → 零 issue；`image_ocr` 无 `ocr_confidence` → 仅 `OCR_CONFIDENCE_UNAVAILABLE` INFO（`ocr_confidence_present=False`），`OCR_LOW_CONFIDENCE` 缺席；`markitdown`（含 `mean=0.10`）→ 两 code 均不产。变异 M3a/M3b 打穿否定断言 |
| A016-04 表格残缺双向 | **PASS** | ragged（3 列表头 + 2 列体行）→ `TABLE_STRUCTURE_INCOMPLETE` WARNING，`total_tables=1`/`incomplete_tables=1`/`incomplete_table_lines=[1]`；规范表与纯文本 → 不产；`tables_enabled=False` → 跳过 |
| A016-05 空正文 | **PASS** | `content_size_bytes=54321` + 全空白正文 → `EMPTY_BUT_NONZERO_PAGES` ERROR（evidence `content_size_bytes=54321`,`result_status=SUCCESS`）；`status=EMPTY` 同条件 → 只有 `REGISTRY_EMPTY_RESULT`；正文非空 → 不产；`pages=[p1,p2,p3]` → `page_count=3` 也报 |
| A016-06 阈值真接线 | **PASS** | 注入 `garbled_warn/error=0.50` → `OCR_GARBLED_RATIO` True→False；注入 `confidence_min_mean=0.10` → `OCR_LOW_CONFIDENCE` True→False；恢复默认复现（测试内断言 + 我独立复跑）。反向硬编码变异 M1a/M1b 均 FAIL |
| A016-07 `--no-semantic` 冻结 008 | **PASS** | 独立取 `git show 5821c9c~1:backend/app/services/parse_quality_checker.py`（1035 行，md5 `b3ed5c84a04738a8b3cf5ca530e9d4d5`）为独立模块 `pqc_008`，同一批 5 个 fixture（中文路径 / 乱码+ragged / 空正文+页数 / EMPTY / FAILED）跑 008 原版 vs 现行 `semantic_enabled=False`：`to_dict()` 去 `generated_at` 后 **全字段相等 = True**，顶层 12 键、`scope` 恰 5 键、`schema_version=1.0`、`issue_counts` 18 键；同时段语义开启产出 4 类语义 issue（证明不是检测器失效）。`parsed_text.md` `read_text` 计数：关闭态 **0**、开启态 **3**（= 3 个 SUCCESS 候选） |
| A016-08 只读/幂等/容错 | **PASS** | 三文件 `grep -nE '\b(session\.add\|session\.add_all\|\.commit\(\|\.flush\(\|\.delete\(\|INSERT \|UPDATE \|DELETE \|REPLACE )'` → **0 命中**；checker 只有 4 处 `select()`（`:672/:735/:820/:1528`），summarizer 无任何 DB 符号；`_load_candidates`/`_resolve_parsed_dir`/`_check_parsed_files`/`_check_registry_status_files` 与 008 原版 `inspect.getsource` **逐字一致**；真实 `parsed/**`+`raw_vault/**` 7 个文件 sha256 快照检查前后同为 `466ccb64ecc7997dabfad1874d7fa91a3744f434d51d12a7cc78d99b7054a8dd`；OSError 隔离：3 候选人为注入读失败（候选 b）→ 仅正常 2 个产 `OCR_GARBLED_RATIO`、`checked_semantic_count=2`、报告落盘、批处理不中断 |
| A016-09 009 联动 | **PASS** | 1.1 报告 `summarize` 通过并计入 `OCR_GARBLED_RATIO=1`；`--issue-codes OCR_GARBLED_RATIO` → `filtered=1`，换 `TABLE_STRUCTURE_INCOMPLETE` → `filtered=0`（过滤真生效）；`image_ocr` manifest → `MANIFEST_PARSER_NAME_INVALID` 缺席且 `issues==[]`，`--parser-name image_ocr` 过滤可用；009 既有 31 项全过、唯一改动为 `:516` 的 `+2/−1`；未知版本 `2.0` 仍 raise、legacy 18 键仍必填、`SCHEMA_VERSION` 仍 `"1.0"`、`_classify_noise` 未动 |
| A016-10 中文路径/全链路 | **PASS（提交内 CLI 组合 NOT_COVERED，QA 已补测）** | 服务层 `test_chinese_path_semantic` 通过：`issue.path` 含 `中文路径`，报告文件含中文、无 `\u4e2d\u6587` 转义。CLI 层：见 §3，**我实测可覆盖**（不写库、不插行），已跑通；但该组合**未进入提交内测试集**，故原 `NOT_COVERED` 描述成立、判据不成立（可解除），建议后续补 1 条 CLI 用例（**不阻塞**） |

验收门禁：五类语义检测 PASS / 反假绿 PASS / 兼容冻结 PASS / 安全红线 PASS。

### 2. 变异记录（变异 → 期望 FAIL → 实测 → 逐字还原 → md5 复核）

统一基线 md5：`parse_quality_checker.py = cec00d45c3cc87521c5acbbc22ac6283`、
`parse_quality_report_summarizer.py = 7508f7136d53282c3aff69e9737ff018`。
每次变异后立刻从 `/tmp` 逐字节备份还原，并复核 md5 与 `git status`。

| # | 变异点（文件:行） | 变异态 md5 | 期望 | 实测 | 还原后 md5 |
|---|------------------|-----------|------|------|-----------|
| M1a | `parse_quality_checker.py:1042/1044` 乱码阈值改为硬编码 `0.10/0.02` | `6f9dde63db6a108eba449a5f6d446563` | `test_garbled_threshold_mutation` FAIL | **FAILED**（`assert 'OCR_GARBLED_RATIO' not in {...}` :678） | `cec00d45…` match |
| M1b | `:1146` `confidence_min_mean` 改硬编码 `0.70` | `4728a8d6dbdc918bd5a578459216f605` | `test_confidence_threshold_mutation` FAIL | **FAILED**（:701） | `cec00d45…` match |
| M2 | `:993` 空正文检测、`:1079` 表格检测分别插入 `return None`（永不产出） | `934d86383ce2c509cbaec50beb20e622` | 4 项 FAIL | **4 FAILED**：`test_ragged_pipe_table`/`test_table_missing_separator_row`/`test_empty_but_nonzero_pages`/`test_empty_text_with_pages_metadata` | `cec00d45…` match |
| M3a | `:1124` 缺 `ocr_confidence` 分支额外编造 `OCR_LOW_CONFIDENCE` | `9593b8a82cc2123f034bca0ecb72cb26` | 反编造否定断言 FAIL | **3 FAILED**：`test_missing_confidence_info_only[mineru]/[image_ocr]`、`test_null_confidence_info_only`（:487/:501 断言 `not in`） | `cec00d45…` match |
| M3b | `:1116` 删除 `parser_name not in ocr_parser_names` 早退（markitdown 也走 OCR 族） | `ff876efa2fdcd469d5215a3f7f2a471b` | 2 项否定断言 FAIL | **2 FAILED**：`test_markitdown_not_evaluated`、`test_markitdown_low_confidence_not_evaluated`（:512/:524） | `cec00d45…` match |
| M4 | `:1036` 删除 `total_chars < min_sample_chars` 跳过分支 | `2af9ff33b5af888015c6ac232cdd52e2` | A016-02 FAIL | **2 FAILED**：`test_garbled_skipped_when_sample_too_small`、`test_min_sample_chars_threshold_is_wired`（:412/:435） | `cec00d45…` match |
| M5 | `:711` 删除 `semantic_config.enabled and` 门禁 | `8695b481ec7df6455110a48242935d97` | A016-07 冻结组 FAIL | **4 FAILED**：`test_no_semantic_freezes_008_report`、`test_no_semantic_payload_equals_008_reference`、`test_no_semantic_does_not_read_text`、`test_semantic_checks_disabled_by_yaml` | `cec00d45…` match |
| M6 | `summarizer:233-237` 删除「1.0 报告含语义键 → raise」守卫 | `7577f54a16cf29ae65c761f807215150` | 守卫用例 FAIL | **FAILED**：`test_summarizer_rejects_semantic_codes_in_1_0_report`（`DID NOT RAISE ValueError` :1186） | `7508f713…` match |
| M7（QA 追加） | `checker:32` 收回 `image_ocr` 白名单 | `1ffd85fea2d89e10153058fa2990a203` | A016-09 白名单 FAIL | **FAILED**：`test_image_ocr_parser_name_allowed`（`'MANIFEST_PARSER_NAME_INVALID' not in {...}` :1220） | `cec00d45…` match |
| M8（QA 追加） | `:711` 删除 `result_status == "SUCCESS"` 门禁 | `4743c03fda1a3c97469ed611186aba03` | A016-05 对照 A FAIL | **FAILED**：`test_empty_status_no_issue`（`EMPTY_BUT_NONZERO_PAGES` 误入 :624） | `cec00d45…` match |

结论：**本节 10 次变异全部被捕获，0 空转**（加上 §6b 的 M9/M10，全程序共 12 次变异 12/12 命中）；全部还原后 016 文件 `61 passed`、全量 `4 failed/498 passed/2 skipped`、
`git status --porcelain -- backend config` 为空。（QA 期间 `specs/.../tasks.md` 出现过实现方
异步写盘的 `M` 状态，随后由 `72078ab` 提交，与本 QA 的代码变异无关。）

### 3. A016-10 CLI × 中文路径：原 NOT_COVERED 的可覆盖性实测

实现者记为 `NOT_COVERED（CLI×中文路径）`，理由是「候选只能来自 `kb_parse_result`，插行会写库」。
**该理由不成立**：不写库也能在 CLI 层覆盖。我实测通过（`--config` 指向 `/tmp` 隔离配置 +
把 `create_db_engine`/`create_session_factory` 换为只读 FakeSession + 替换 `_load_candidates`）：

```text
[cli-zh] exit_code = 0
[cli-zh] schema_version = 1.1 | checked_semantic_count = 1 | scope.semantic = True
[cli-zh] OCR_GARBLED_RATIO 条数 = 1 | evidence.garbled_ratio = 0.05
[cli-zh] issue.path = /tmp/qa016_cli_*/parsed/中文路径/样例目录/parsed_text.md
[cli-zh] 报告含中文路径 = True | 无 \u 转义 = True
[cli-zh] --no-semantic exit = 0 | schema = 1.0 | issue_counts 键数 = 18 | semantic 键 = False
```

即：CLI 命令、中文路径产物、`ensure_ascii=False`、`--no-semantic` 回落四件事**均可零写库验证**。
该验证脚本未纳入仓库（QA 侧临时脚本），因此判定维持 **PASS** 但建议 016 后续补一条
「CLI × 中文路径」用例（不阻塞）。

### 4. 白名单审计（`git show --stat 5821c9c`）

实际改动 **7 个文件**，与 plan §7.1 白名单（6）+ 唯一授权例外（1）**完全对应，无多余文件**：

| 文件 | 增删 | 是否在白名单 |
|------|------|--------------|
| `backend/app/services/parse_quality_checker.py` | +744/− | ✅ §7.1 |
| `backend/app/services/parse_quality_report_summarizer.py` | +41/− | ⚠ 字面超「仅两常量」，见下 |
| `backend/app/cli/main.py` | +6/−0（纯追加，仅 `--no-semantic`，014/015 命令零触碰） | ✅ §7.1 |
| `config/quality_semantics.yaml` | 新增 34 行 | ✅ §7.1 |
| `backend/tests/test_parse_quality_checker_semantic.py` | 新增 1368 行（61 例） | ✅ §7.1 |
| `backend/tests/test_parse_quality_report_summarizer.py` | **+2/−1，仅 `:513-517` 一处** | ✅ 授权例外（TL 裁定 A） |
| `specs/016-semantic-quality-metrics/tasks.md` | +27/−20（留痕） | ✅ §7.1 |

- 例外文件逐字核对：diff 只有 `expected = {code: payload["issue_counts"].get(code, 0) for code in ISSUE_CODES}`
  + 改一行断言，`assert result.filtered_issue_count == 1` 原样保留，**无第二处改动**。
- summarizer 严格说超出 §7.1「仅 `ISSUE_CODES` / `ALLOWED_PARSER_NAMES` 两常量追加」：
  另有 `INPUT_SCHEMA_VERSIONS`（`:18`/`:200`）、`LEGACY_ISSUE_CODES` 拆分（`:26`）、
  1.0+语义键 fail-closed 守卫（`:231-237`）、`_copy_issue_counts` 缺键补 0（`:272-275`）。
  但该偏离有 **TL 明示授权**（`tasks.md` 「冲突 2」+ 5 条硬约束、矩阵 §7 登记），
  5 条约束我逐条实测成立（未知版本 raise / legacy 18 键必填 / `SCHEMA_VERSION` 不动 / 分桶不动 / 1.0 含语义键 raise），
  故**不判 FAIL**，仅登记为「已授权偏离」。

### 5. NOT_COVERED 清单

| # | 项 | 状态 | 说明 |
|---|----|------|------|
| N-1 | CLI × 中文路径 | **已由 QA 覆盖（§3）**，提交内仍缺用例 | 不阻塞；建议补用例 |
| N-2 | 真实库 `kb_parse_result` 插行直连 E2E | 维持 NOT_COVERED | 违反「不写 DB」红线，替代方案已由 §3 + 服务层覆盖 |
| N-3 | `--no-semantic` 在生产 1.0 消费者处的端到端回归（009 以外的下游） | 维持 NOT_COVERED | 016 范围内无其它消费者；009 已覆盖 |

### 6. 真实缺陷

**无阻塞缺陷、无不阻塞缺陷需返工。** 以下为观察项（非缺陷，不要求修复）：

- O-1：`_detect_empty_but_nonzero_pages`（`:998-1007`）依赖 manifest `content_size_bytes`；
  manifest 缺该字段且 metadata 无页数时，全空白正文**不报**（`test_empty_text_without_source_evidence_no_issue` 显式覆盖）。
  与 plan §4.2 口径一致、符合 D-2「缺数据不编造」，但属已知漏报面。
- O-2：`extract_confidence_mean`（`:440-448`）对 `ocr_confidence` 为裸数值也接受（015 契约为 dict），属宽松接受，非缺陷。
- O-3：`count_garbled_chars` 对 Box Drawing / Block Elements 等区块只在**连续 ≥3 字符 run** 时计入，
  单字符不误报；该容差未在 acceptance 中定义，实测不影响验收项。

### 6b. TL 追加复核（qa-016，2026-10-17 追加）

TL 更正基线并指定两处「请复核而非采信」。复核实测如下（锚定 md5 与 TL 清单**逐项一致**：
checker `cec00d45…`、summarizer `7508f713…`、016 测试 `fe627f21…`、009 测试 `c9246bd4…`、
cli `49f24462…`、yaml `d172689c…`；全量 `4 failed/498 passed/2 skipped`、016 文件 61 passed）。

| # | 变异点（文件:行） | 变异态 md5 | 期望 | 实测 | 还原后 md5 |
|---|------------------|-----------|------|------|-----------|
| M9 | `parse_quality_checker.py:651-653` 语义关闭时**也写** `scope["semantic"]=False` | `d2fa65a09b251f0aba117e2cc316ca06` | 零差异用例 FAIL | **3 FAILED**：`test_no_semantic_payload_equals_008_reference`、`test_no_semantic_freezes_008_report`、`test_semantic_checks_disabled_by_yaml` | `cec00d45…` match |
| M10 | `parse_quality_report_summarizer.py:231` 守卫短路为 `if False and data.get("schema_version") == "1.0":` | `1d262a465984c7291c6b098b03c4eda2` | 守卫用例 FAIL | **1 FAILED**：`test_summarizer_rejects_semantic_codes_in_1_0_report` | `7508f713…` match |

- **A016-07 判据 = 零差异（不是「唯一差异是 semantic=false」）**，已独立验证：
  008 原版模块 vs 现行 `semantic_enabled=False`，5 fixture 去 `generated_at` 后**全字段相等 True**；
  1.0 报告 `scope` 恰 5 键、无 `semantic` 键；`test_no_semantic_payload_equals_008_reference:843-845`
  是 `json.dumps(..., sort_keys=True)` **整串相等**而不是只比键集，M9 证明该整串比较非空转。
- **fail-closed 加强项**计入判定：`schema_version="1.0"` 且 `issue_counts` 含语义键 → `ValueError`
  （`summarizer:231-237`）；M6（删守卫）与 M10（短路守卫）两次变异均被
  `test_summarizer_rejects_semantic_codes_in_1_0_report` 捕获，**断言非空转**。
- `tests/test_parse_quality_report_summarizer.py:513-517` 的 `+2/−1` 属**授权例外**，不计越界（§4 已核）。

### 7. 最终 verdict

**QA_PASS（验收通过）**：A016-01~10 全部 **PASS**（10/10，0 FAIL，0 未覆盖的硬项）；
10 组变异（6 组指定 + 2 组 QA 追加 + 2 组 TL 追加复核）**变异必 FAIL、还原必 PASS**，否定断言无空转；
白名单 7 文件与授权例外完全对应；只读红线（无 SQL 写、产物 sha256 不变、OSError 隔离）实测成立；
全量回归 `4 failed（既有 --help 基线）/ 498 passed / 2 skipped`。
唯一建议（不阻塞）：补一条「CLI × 中文路径」用例，把本次 §3 的覆盖固化进测试集。
