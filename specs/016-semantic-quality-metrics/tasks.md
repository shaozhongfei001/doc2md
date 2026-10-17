# Tasks: 语义质检指标（016）

> **Spec**：`specs/016-semantic-quality-metrics/`
> **分支**：`feature/016-semantic-quality-metrics`
> **Plan**：`plan.md`（TL G0 已落地；Owner 裁定 O-1 ~ O-4 全部 RESOLVED，见 `spec.md` §11）
> **Dev 必读顺序**：本文件 → `plan.md` → `spec.md` → `acceptance.md` → `test_cases.md`

---

## Dev 文件白名单（全局）

**允许修改**：

```text
backend/app/services/parse_quality_checker.py            # 仅 plan §4 范围
backend/app/services/parse_quality_report_summarizer.py  # 仅 ISSUE_CODES / ALLOWED_PARSER_NAMES 两常量追加
backend/app/cli/main.py                                  # check-parse-quality 增 --no-semantic
config/quality_semantics.yaml                            # 新增
backend/tests/test_parse_quality_checker_semantic.py     # 新增
specs/016-semantic-quality-metrics/tasks.md              # 勾选
```

**禁止修改**：

```text
backend/app/services/mineru_pdf_parser.py  markitdown_parser.py  image_parser.py
backend/app/core/parser_routing.py
backend/app/models/**  sql/**  generated/**
raw_vault/**（只读）  parsed/**（真实产物）  data/**
specs/001..013/**（封版）  specs/014/**  specs/015/**  specs/017/**  specs/902/**
config/parser_rules.yaml  config/quality_actions.yaml
specs/016-semantic-quality-metrics/plan.md  # Dev 不改 Plan
```

---

## 全局硬约束（T001–T011 均适用）

1. **只读红线**：`parsed_text.md` / `parsed_metadata.json` 新增只读 open 开口；不写 parsed/、raw_vault/、curated/、quarantine/。
2. **DB SELECT-only**：复用 `_load_candidates` 零改动；不写 DB、不改 schema。
3. **D-2 缺数据不编造**：无 `ocr_confidence` → 只产 INFO `OCR_CONFIDENCE_UNAVAILABLE`，**绝不**产 `OCR_LOW_CONFIDENCE`；正文 < `min_sample_chars` → 乱码检查整体跳过（不是 ratio=0 通过）；无 pipe 表格 → 表格检查跳过。
4. **D-3 阈值必须真实接线**：所有阈值从 `SemanticCheckConfig` 读取，检测函数签名显式接收阈值参数；硬编码即 A016-06 变异测试失败。
5. **D-4 兼容冻结**：`--no-semantic` 或 yaml `enabled=false` 时报告与 008 现状**逐字段一致**（`schema_version="1.0"`、`issue_counts` 仅 18 既有键、无 `semantic_checks_enabled` 键）。
6. **D-5 不新增错误类 code**：读文件失败复用 008 既有 `MISSING_PARSED_TEXT` / `INVALID_PARSE_MANIFEST_JSON`；检测内部异常 → 候选级 `errors[]`，continue。
7. 幂等；单条失败不中断批处理；实现前必读 `plan.md` §4 与 §9 TL 决策表（Q1–Q8）。
8. fixture 全部现场构造（tmp_path 手工写产物 + 测试 DB 插 `kb_parse_result`），**不**提交二进制。

---

## T001 阅读 008/009 代码与本 Spec

### 目标

理解 008 检查主循环、issue/evidence 结构、报告 schema，与 009 汇总的分桶逻辑。

### 验收标准

- [x] 已读 `backend/app/services/parse_quality_checker.py`（四类既有检查、`_load_candidates`、`_resolve_parsed_dir`、`ISSUE_CODES`、`RECOMMENDATION_BY_CODE`）
- [x] 已读 `backend/app/services/parse_quality_report_summarizer.py`（`ISSUE_CODES`、`ALLOWED_PARSER_NAMES`、`_classify_noise`、`:528-529` 校验）
- [x] 已读 `backend/tests/test_parse_quality_checker.py` 的 DB fixture 模式
- [x] 已读 `plan.md` 全文与 `acceptance.md` A016-01 ~ A016-10

---

## T002 测试先行：test_parse_quality_checker_semantic.py（红）

### 目标

先写五类语义检查的双向断言测试（正例 + 对照 + 否定），此时应全红。

### 允许修改范围

- `backend/tests/test_parse_quality_checker_semantic.py`（新增）

### 验收标准

- [x] fixture 构造器：干净中文正文 / 5% 乱码 / 15% 重度乱码 / 超短正文 / 规范 pipe 表 / ragged pipe 表 / 空正文 + 非零 size
- [x] 覆盖 A016-01 ~ A016-05（此时全红）
- [x] 否定断言显式存在（如 `assert "OCR_LOW_CONFIDENCE" not in codes`）

---

## T003 `SemanticCheckConfig` 与 `config/quality_semantics.yaml`

### 目标

配置载体 + 冻结默认值，按 `plan.md` §2.3。

### 验收标准

- [x] `SemanticCheckConfig` dataclass；`ParseQualityCheckerService.__init__` 增可选注入（测试可传内存配置）
- [x] yaml 缺失 / 字段缺失 → 代码默认值 + WARNING 一次
- [x] 阈值非法（负数 / >1）→ `ValueError` fail-fast（不静默）
- [x] 默认值与 Q8 一致：garbled `0.02 / 0.10`、confidence `0.70`、`min_sample_chars=200`

---

## T004 `_is_garbled_char` 纯函数 + `OCR_GARBLED_RATIO`

### 目标

乱码字符判定表冻结在代码（不随 yaml 变），yaml 只调阈值。

### 验收标准

- [x] `_is_garbled_char(ch)` 为纯函数，单测直接覆盖：U+FFFD / C0-C1 控制符（除 `\n\t\r`）/ 私有区 / 异常符号 run
- [x] `total_chars < min_sample_chars` → **跳过**（零 issue，非 ratio=0 通过）
- [x] ratio 分档：≥ `warn_ratio` → WARNING；≥ `error_ratio` → ERROR
- [x] `evidence` 含实测值与阈值：`{"garbled_ratio", "threshold_warn", "threshold_error", "total_chars", "sampled"}`

---

## T005 `OCR_LOW_CONFIDENCE` / `OCR_CONFIDENCE_UNAVAILABLE`

### 目标

按 `plan.md` §4.2 第 4 条，仅对 `parser_name ∈ ocr_parser_names` 评估。

### 验收标准

- [x] `ocr_confidence.mean` 缺失或 None → 仅 INFO `OCR_CONFIDENCE_UNAVAILABLE`（**否定**：绝不产 `OCR_LOW_CONFIDENCE`）
- [x] `mean < min_mean` → WARNING `OCR_LOW_CONFIDENCE`，evidence 含实测值与阈值
- [x] `mean >= min_mean` → 无 issue
- [x] 非 OCR 族（`markitdown`）→ 两个 code 都不产
- [x] `parsed_metadata.json` JSON 非法 / 读取 OSError → confidence 检查跳过，不新增 code

---

## T006 `TABLE_STRUCTURE_INCOMPLETE`

### 目标

逐行扫描 pipe 表格块，列数不一致即残缺。

### 验收标准

- [x] 表头列数 ≠ 分隔行列数，或任一数据行 ≠ 表头列数 → 残缺
- [x] `incomplete > 0` → WARNING，evidence 含残缺行号（截断至前 20 个）
- [x] 规范表格 / 无表格纯文本 → 无此 issue
- [x] `tables.enabled=false` → 该检查整体跳过

---

## T007 `EMPTY_BUT_NONZERO_PAGES`

### 目标

`status=SUCCESS` 但正文全空白且体积非零 → ERROR。

### 验收标准

- [x] `text.strip() == "" and (content_size_bytes or 0) > 0` → ERROR
- [x] 页数判定：`parsed_metadata.json` 含 `pages` / `page_count` 时并入判断
- [x] 对照：manifest `status=EMPTY` → 不产此 code（既有 `REGISTRY_EMPTY_RESULT` 覆盖）；`status=SUCCESS` 且正文非空 → 不产
- [x] `empty_text.enabled=false` → 跳过

---

## T008 报告演进：schema_version 1.1 与 issue_counts

### 目标

按 `plan.md` §3.1 扩展报告结构，保证向下兼容。

### 验收标准

- [x] 语义开启：`schema_version="1.1"`、`semantic_checks_enabled=true`、`summary.checked_semantic_count`、`issue_counts` 追加 5 个新键
- [x] `--no-semantic` / `enabled=false`：`schema_version="1.0"`、`issue_counts` 仅 18 既有键、**不出现** `semantic_checks_enabled` 键（逐字段复现 008）
- [x] 既有 18 项 code 行为零变更；`SEMANTIC_ISSUE_CODES` 按既有在前、追加在后排列
- [x] 每条语义 issue 的 `RECOMMENDATION_BY_CODE` 人工建议文案已增补 5 条

---

## T009 009 summarizer 联动

### 目标

按 `plan.md` §3.2 做两常量追加，**不**动分桶逻辑。

### 验收标准

- [x] `ISSUE_CODES` 追加 5 项；`ALLOWED_PARSER_NAMES` 追加 `image_ocr`
- [x] `_classify_noise` / `:120-121` 硬编码分桶路径不动，新 code 落入"需人工审阅"默认路径
- [x] `--issue-codes OCR_GARBLED_RATIO` 过滤可用（`:528-529` 校验接受新 code）
- [x] 015 的 `parser_name="image_ocr"` manifest 经 008+016 检查**不再**产 `MANIFEST_PARSER_NAME_INVALID`
- [x] `test_parse_quality_report_summarizer.py` 既有用例通过（**白名单例外：仅 `:516` 一处 2 行等价化，TL 裁定 A**）：
  原断言 `:111` 要求 `set(issue_counts)==set(ISSUE_CODES)`（23 键）与 `:516` 要求逐键等于 18 键 fixture 互斥，
  经 TL 2026-10-17 裁定按「归一化期望」改写 `:516`，其余断言一字未动，31 项全过。详见文末裁定留痕。

---

## T010 CLI 与阈值接线验证

### 目标

`check-parse-quality --no-semantic`；并以变异测试证明阈值非装饰性配置。

### 验收标准

- [x] `--no-semantic` 默认 False；`scope` 块在语义开启时输出 `"semantic": true`
      （TL 裁定 (c)：关闭时**省略**该键，以保证与 008 逐字段一致）
- [x] 阈值变异：注入 `warn_ratio=0.50` → `OCR_GARBLED_RATIO` 消失；`min_mean=0.10` → `OCR_LOW_CONFIDENCE` 消失；恢复后复现
- [x] 否定断言：`--no-semantic` 报告运行期 `parsed_text.md` 未被读取（monkeypatch `Path.read_text` 计数为 0）

---

## T011 红线、回归与 STOP

### 目标

只读红线 + 幂等 + 全量回归，随后 STOP。

### 禁止事项

- Dev **不得**自我宣布 A016-01 ~ A016-10 通过；**不得**写 handoff；**不得** merge main

### 验收标准

- [x] 检查前后 `parsed/**` 与 `raw_vault/**` listing + sha256 不变；DB 行数快照对比 SELECT-only
- [x] 连续两次同配置：除 `generated_at` 与报告文件名外逐字段一致
- [x] 单候选读取 OSError → 该候选跳过语义检查，其余正常，批处理不中断
- [x] `中文路径` 下产物全链路通过，报告 `ensure_ascii=False` 无乱码
- [x] `test_parse_quality_checker.py` 零修改全过（30 项）；`test_parse_quality_report_summarizer.py` 全过（31 项，含 T009 的 1 处裁定改动）；
  001–013 全量回归：`4 failed（既有 --help 基线）/ 496 passed / 2 skipped`
- [x] Dev 输出：修改文件清单、pytest 命令、CLI 命令、遗留问题 → STOP → P2 DB Review → QA → E2E → TL Final Review

---

## 任务进度总览

| Task | 说明 | 状态 |
|------|------|------|
| T001 | 阅读 008/009 与规格 | [x] |
| T002 | 测试先行（红测 + fixture 构造器） | [x] |
| T003 | SemanticCheckConfig + quality_semantics.yaml | [x] |
| T004 | `_is_garbled_char` + OCR_GARBLED_RATIO | [x] |
| T005 | OCR_LOW_CONFIDENCE / UNAVAILABLE | [x] |
| T006 | TABLE_STRUCTURE_INCOMPLETE | [x] |
| T007 | EMPTY_BUT_NONZERO_PAGES | [x] |
| T008 | 报告 schema 1.1 与 issue_counts | [x] |
| T009 | 009 summarizer 联动 | [x]（含 TL 裁定 A 的 1 处白名单例外） |
| T010 | CLI --no-semantic + 阈值接线变异 | [x] |
| T011 | 红线 / 回归 / STOP | [x] |

---

---

## 实现留痕（Dev，2026-10-17）

### 交付文件与 md5

| 文件 | 操作 | md5 |
|------|------|-----|
| `backend/app/services/parse_quality_checker.py` | 修改（+721/−23） | `cec00d45c3cc87521c5acbbc22ac6283` |
| `backend/app/services/parse_quality_report_summarizer.py` | 修改（+26/−6） | `7577f54a16cf29ae65c761f807215150` |
| `backend/app/cli/main.py` | 修改（+6/−0，纯追加） | `49f2446293f88ee67177be5126d4e3ea` |
| `config/quality_semantics.yaml` | 新增 | `d172689c00923ee0106c2a07e2bbc1e4` |
| `backend/tests/test_parse_quality_checker_semantic.py` | 新增（59 用例） | `4482e85e0f72b0b7037bca182b2dbeaf` |
| `backend/tests/test_parse_quality_report_summarizer.py` | 改（+2/−1，**白名单例外**，TL 裁定 A） | `c9246bd4205282312cc4408f48e7c3e7`（改前 `b77429121f2b29da3ffdd17c8243c9c3`） |

### 实测成绩

- 新测试文件：`59 passed`
- `test_parse_quality_checker.py`（008，零修改）：`30 passed`
- `test_parse_quality_report_summarizer.py`（009）：`31 passed`（含裁定 A 的 1 处等价化）
- 全量：`4 failed（既有 --help 基线）/ 496 passed / 2 skipped`（437 基线 + 59 新增）

### 变异复验（证明断言非空转）

| 变异 | 内容 | 捕获结果 |
|------|------|----------|
| M1 | 乱码阈值硬编码 0.02/0.10 | `test_garbled_threshold_mutation` FAILED |
| M2 | 置信度阈值硬编码 0.70 | `test_confidence_threshold_mutation` FAILED |
| M3 | 判定表移除 U+FFFD | 11 项 FAILED |
| M4 | 缺置信度时改产 `OCR_LOW_CONFIDENCE` | 3 项 FAILED（反编造） |
| M5 | 删除 `total_chars < min_sample_chars` 跳过 | `test_garbled_skipped_when_sample_too_small` FAILED |
| M6 | 去掉 `semantic_config.enabled` 门禁 | 3 项 FAILED（`--no-semantic` 冻结） |

全部变异均已恢复原文件（恢复后 md5 = `8077827533014f084cdd50d3618fa911`；此后按 TL 裁定 (c) 再改一处，
现行 md5 = `cec00d45c3cc87521c5acbbc22ac6283`，与新测试全绿一致）。

### 已报 TL 并获裁定的偏离（详见下节）

1. **T009 断言互斥** → TL 裁定 **A（方案 ①）**：`:516` 一处 2 行等价化（其余断言零改动）。
2. **summarizer 除两常量外的最小兼容改动** → **偏离批准**，附 4 条硬约束（未知版本仍 raise / legacy 18 键仍必填 /
   009 输出 `SCHEMA_VERSION` 不动 / 分桶逻辑不动）。
3. **`scope.semantic`** → TL 裁定 **(c)**：`--no-semantic` 时**省略**该键（验收项 A016-07 优先于 plan §4.3 设计描述），
   `--no-semantic` 输出与 008 **完全一致（零差异）**，已按此改动并用 008 原版代码逐字段对照验证。

---

## TL 裁定：规格内部冲突处理（2026-10-17）

> 裁定人：team-lead（TL）。Dev 于实现期上报，两处冲突均被认定成立。本记录为 016 唯一白名单例外的授权依据。

### 冲突 1：`test_parse_quality_report_summarizer.py` 两条断言互斥

| 位置 | 断言 | 隐含要求 |
|------|------|----------|
| `:111` | `assert set(data["issue_counts"]) == set(ISSUE_CODES)` | 输出 issue_counts **必须 23 键**（ISSUE_CODES 追加 5 项后） |
| `:516` | `assert data["issue_counts"] == payload["issue_counts"]` | 输出必须**逐键等于输入 18 键 fixture** |

同一输出字段、同一 fixture，两条断言对同一 dict 的键集要求相反 → 「009 既有用例零修改全过」在
`ISSUE_CODES` 追加 5 项后**不可达**（Dev 实测：输出 23 键挂 `:516`，输出 18 键挂 `:111`）。

**裁定：A（改 `:516`，不改 `:111`）**。理由：`:111` 本身就是 009 对扩展后 ISSUE_CODES 的集合相等期望；
改 `:111` 等于放松更强断言。`:516` 只把期望从「硬编码 18 键」改为「按 ISSUE_CODES 归一化、缺键补 0」，
保留其原意「severity 过滤不改变计数」。

改法（已落盘，2 行）：

```python
expected = {code: payload["issue_counts"].get(code, 0) for code in ISSUE_CODES}
assert data["issue_counts"] == expected
```

约束遵守情况：仅此一处；未把 23 硬编码（引用 `ISSUE_CODES`）；未放松该文件其它任何断言；
`assert result.filtered_issue_count == 1` 原样保留。diff = `+2/−1`，md5 `b7742912…` → `c9246bd4…`。

**取 ① 不取 ② 的理由（TL 复核 fixture 后补充裁定）**：`parse_quality_report_valid.json` 现为
18 键 / 2 非零 / schema 1.0，被该测试文件 **11 处**引用。方案 ②（把 fixture 升成 23 键）会**抹掉
「18 键 legacy 输入仍被接受」这条覆盖** —— 而 A016-07 恰恰要求 008 的 18 键产出能被 009 读，该路径必须保留。
方案 ① 只改期望的构造方式，`18 键输入 → 23 键输出（缺键补 0）`这条真实路径反而被显式校验。

### 冲突 2：plan §3.2「summarizer 仅两常量追加」 vs Owner 裁定 O-4「增量键兼容」

只改两常量会导致真实链路断裂：016 产出的 `schema_version="1.1"` 报告被 009 的
`validate_input_report` 直接 `ValueError`，且 009 自己的 18 键 fixture 因「issue_counts 必须全含」
被全部拒绝。TL 批准做**输入侧**兼容，附 4 条硬约束，Dev 逐条自证：

| # | 约束 | 实现 | 守卫用例 |
|---|------|------|----------|
| 1 | 未知版本（如 "2.0"）仍 raise，不得变白名单放行 | `INPUT_SCHEMA_VERSIONS=frozenset({"1.0","1.1"})`，`not in` 即 raise | `test_summarizer_rejects_unknown_schema_version` + 009 既有 `test_invalid_schema_version_rejected` |
| 2 | legacy 18 键仍必填；语义 5 键可缺席 | 必填循环改用 `LEGACY_ISSUE_CODES`；`ISSUE_CODES` 循环对 `not in` 跳过、present 则校验 int≥0 | `test_summarizer_still_requires_legacy_issue_codes`、`test_summarizer_semantic_keys_optional_and_zero_filled`、`test_summarizer_rejects_invalid_semantic_count` |
| 3 | `SCHEMA_VERSION` 输出常量不动（009 仍输出 "1.0"） | 未改 | `test_summarizer_schema_version_constant_unchanged` |
| 4 | `_classify_noise` / 分桶 / `:120-121` 一行不动 | 未改 | 009 既有分桶用例 |

注：约束 2 的「可选」按「语义键缺席不报错、present 则必须合法」实现；未额外加「1.0 报告不得出现语义键」
的更强规则（现有 1.0 生产者不会产出该组合），若 QA 要求 fail-closed 可再加。

### 冲突 3：`scope.semantic`（plan §4.3 / T010 vs spec §7.5 / A016-07）

- plan §4.3 + T010：`scope` 块增加 `"semantic": true|false`（设计描述）。
- spec §7.5 + A016-07：`--no-semantic` 输出必须与 008 **逐字段一致**（验收项）。
- 二者对 1.0 报告的 scope 键集要求相反。

**TL 裁定 (c)：`--no-semantic` 时省略 `scope.semantic`**（验收项优先于设计描述；给冻结的 1.0 schema 加键属额外键）。
实现：`scope` 先构 5 个 008 键，`if semantic_config.enabled: scope["semantic"] = True`。

零差异证据（**不是只断言键集**，而是与 008 原版代码逐字段对照）：

| 验证 | 方法 | 结果 |
|------|------|------|
| 服务层 | `git show HEAD:backend/app/services/parse_quality_checker.py`（1035 行，md5 `b3ed5c84…` = 016 改动前 md5）导入为独立模块，同一批 4 个 fixture（中文路径 / 乱码+ragged 表 / 空正文+页数 / EMPTY / FAILED）分别跑 008 原版与现行 `semantic_enabled=False` | `to_dict()`（去 `generated_at`）**完全相等 = True**；顶层 12 键、scope 恰 5 键、schema `1.0` |
| CLI 层 | `/tmp/p16_cli/app.yaml`（storage 全指向 `/tmp`，mysql 指向真实库只读）跑 `check-parse-quality --limit 5` 与 `--no-semantic` | 默认：schema 1.1 / 14 顶键 / 23 issue_counts / scope 6 键 / summary 10 键；`--no-semantic`：1.0 / 13 键 / 18 / 5 / 9；把默认报告的 016 增量裁掉后与 `--no-semantic` **完全相等 = True** |

> 注：`parser_name="image_ocr"` 的候选**必然**与 008 有差异（008 会产 `MANIFEST_PARSER_NAME_INVALID`，
> 016 按 A016-09 不再产）。这是 016 的**授权变更**，不是冻结失效，零差异对照因此用 markitdown/mineru 候选。

### 附加自证（TL 要求）

**(d) 新增代码无 SQL 写语句**：`grep -nE '\b(INSERT|UPDATE|DELETE|REPLACE)\b'` 扫
`parse_quality_checker.py` / `parse_quality_report_summarizer.py` / `cli/main.py` → **0 命中**；
对 `git diff` 的**新增行**再扫 → **0 命中**。真实库不插测试行（TL 认同）。

**(e) CLI E2E**：`check-parse-quality` 的候选**只能**来自 `kb_parse_result`（无 fixture 入口），故用
「真实库 + `/tmp` 隔离 storage」跑通全链路，再把中文乱码 fixture 落到某条 DB 行指向的 `/tmp/parsed/...` 目录：

- 运行结果：`checked_semantic_count=1`，产出 `OCR_GARBLED_RATIO` WARNING，
  evidence = `{"garbled_ratio":0.0476,"garbled_chars":40,"total_chars":840,"threshold_warn":0.02,"threshold_error":0.1,"min_sample_chars":200,"sampled":true}`
- 报告文件 `ensure_ascii=False` 生效（全文无 `\u` 转义）
- 同批 5 个候选中有 4 个 parsed 目录不存在 → 语义检查逐条 skip（`logger.warning`）且**批处理不中断**（A016-08 容错实证）
- 局限：该 DB 行的 `parsed_dir` 是 `by_hash/99/99/...`（ASCII），**中文路径**只由 `test_chinese_path_semantic` 在服务层覆盖；
  若要 CLI 层中文路径需往真实库插候选行（违反「不写 DB」），故该项记为 **NOT_COVERED（CLI×中文路径组合）**
- 临时注入的 fixture 目录已清理；`/tmp/p16_cli/` 下保留 config 与两份报告作为证据

### 白名单例外登记

- 例外文件：`backend/tests/test_parse_quality_report_summarizer.py`（009 测试，非 016 白名单）
- 例外范围：**仅 `:516` 一条 assert**（`+2/−1`）
- 授权：TL 2026-10-17 裁定 A（方案 ①）
- TL 侧登记：`specs/S1_WHITELIST_CONFLICT_MATRIX.md` §7（提交 `15fcfc6`，由 TL 记录，Dev 未改矩阵）

**Tasks 结束** — Dev 从 T001 开始，严格在白名单内实现。
