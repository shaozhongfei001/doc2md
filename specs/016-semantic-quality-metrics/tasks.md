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

- [ ] 已读 `backend/app/services/parse_quality_checker.py`（四类既有检查、`_load_candidates`、`_resolve_parsed_dir`、`ISSUE_CODES`、`RECOMMENDATION_BY_CODE`）
- [ ] 已读 `backend/app/services/parse_quality_report_summarizer.py`（`ISSUE_CODES`、`ALLOWED_PARSER_NAMES`、`_classify_noise`、`:528-529` 校验）
- [ ] 已读 `backend/tests/test_parse_quality_checker.py` 的 DB fixture 模式
- [ ] 已读 `plan.md` 全文与 `acceptance.md` A016-01 ~ A016-10

---

## T002 测试先行：test_parse_quality_checker_semantic.py（红）

### 目标

先写五类语义检查的双向断言测试（正例 + 对照 + 否定），此时应全红。

### 允许修改范围

- `backend/tests/test_parse_quality_checker_semantic.py`（新增）

### 验收标准

- [ ] fixture 构造器：干净中文正文 / 5% 乱码 / 15% 重度乱码 / 超短正文 / 规范 pipe 表 / ragged pipe 表 / 空正文 + 非零 size
- [ ] 覆盖 A016-01 ~ A016-05（此时全红）
- [ ] 否定断言显式存在（如 `assert "OCR_LOW_CONFIDENCE" not in codes`）

---

## T003 `SemanticCheckConfig` 与 `config/quality_semantics.yaml`

### 目标

配置载体 + 冻结默认值，按 `plan.md` §2.3。

### 验收标准

- [ ] `SemanticCheckConfig` dataclass；`ParseQualityCheckerService.__init__` 增可选注入（测试可传内存配置）
- [ ] yaml 缺失 / 字段缺失 → 代码默认值 + WARNING 一次
- [ ] 阈值非法（负数 / >1）→ `ValueError` fail-fast（不静默）
- [ ] 默认值与 Q8 一致：garbled `0.02 / 0.10`、confidence `0.70`、`min_sample_chars=200`

---

## T004 `_is_garbled_char` 纯函数 + `OCR_GARBLED_RATIO`

### 目标

乱码字符判定表冻结在代码（不随 yaml 变），yaml 只调阈值。

### 验收标准

- [ ] `_is_garbled_char(ch)` 为纯函数，单测直接覆盖：U+FFFD / C0-C1 控制符（除 `\n\t\r`）/ 私有区 / 异常符号 run
- [ ] `total_chars < min_sample_chars` → **跳过**（零 issue，非 ratio=0 通过）
- [ ] ratio 分档：≥ `warn_ratio` → WARNING；≥ `error_ratio` → ERROR
- [ ] `evidence` 含实测值与阈值：`{"garbled_ratio", "threshold_warn", "threshold_error", "total_chars", "sampled"}`

---

## T005 `OCR_LOW_CONFIDENCE` / `OCR_CONFIDENCE_UNAVAILABLE`

### 目标

按 `plan.md` §4.2 第 4 条，仅对 `parser_name ∈ ocr_parser_names` 评估。

### 验收标准

- [ ] `ocr_confidence.mean` 缺失或 None → 仅 INFO `OCR_CONFIDENCE_UNAVAILABLE`（**否定**：绝不产 `OCR_LOW_CONFIDENCE`）
- [ ] `mean < min_mean` → WARNING `OCR_LOW_CONFIDENCE`，evidence 含实测值与阈值
- [ ] `mean >= min_mean` → 无 issue
- [ ] 非 OCR 族（`markitdown`）→ 两个 code 都不产
- [ ] `parsed_metadata.json` JSON 非法 / 读取 OSError → confidence 检查跳过，不新增 code

---

## T006 `TABLE_STRUCTURE_INCOMPLETE`

### 目标

逐行扫描 pipe 表格块，列数不一致即残缺。

### 验收标准

- [ ] 表头列数 ≠ 分隔行列数，或任一数据行 ≠ 表头列数 → 残缺
- [ ] `incomplete > 0` → WARNING，evidence 含残缺行号（截断至前 20 个）
- [ ] 规范表格 / 无表格纯文本 → 无此 issue
- [ ] `tables.enabled=false` → 该检查整体跳过

---

## T007 `EMPTY_BUT_NONZERO_PAGES`

### 目标

`status=SUCCESS` 但正文全空白且体积非零 → ERROR。

### 验收标准

- [ ] `text.strip() == "" and (content_size_bytes or 0) > 0` → ERROR
- [ ] 页数判定：`parsed_metadata.json` 含 `pages` / `page_count` 时并入判断
- [ ] 对照：manifest `status=EMPTY` → 不产此 code（既有 `REGISTRY_EMPTY_RESULT` 覆盖）；`status=SUCCESS` 且正文非空 → 不产
- [ ] `empty_text.enabled=false` → 跳过

---

## T008 报告演进：schema_version 1.1 与 issue_counts

### 目标

按 `plan.md` §3.1 扩展报告结构，保证向下兼容。

### 验收标准

- [ ] 语义开启：`schema_version="1.1"`、`semantic_checks_enabled=true`、`summary.checked_semantic_count`、`issue_counts` 追加 5 个新键
- [ ] `--no-semantic` / `enabled=false`：`schema_version="1.0"`、`issue_counts` 仅 18 既有键、**不出现** `semantic_checks_enabled` 键（逐字段复现 008）
- [ ] 既有 18 项 code 行为零变更；`SEMANTIC_ISSUE_CODES` 按既有在前、追加在后排列
- [ ] 每条语义 issue 的 `RECOMMENDATION_BY_CODE` 人工建议文案已增补 5 条

---

## T009 009 summarizer 联动

### 目标

按 `plan.md` §3.2 做两常量追加，**不**动分桶逻辑。

### 验收标准

- [ ] `ISSUE_CODES` 追加 5 项；`ALLOWED_PARSER_NAMES` 追加 `image_ocr`
- [ ] `_classify_noise` / `:120-121` 硬编码分桶路径不动，新 code 落入"需人工审阅"默认路径
- [ ] `--issue-codes OCR_GARBLED_RATIO` 过滤可用（`:528-529` 校验接受新 code）
- [ ] 015 的 `parser_name="image_ocr"` manifest 经 008+016 检查**不再**产 `MANIFEST_PARSER_NAME_INVALID`
- [ ] `test_parse_quality_report_summarizer.py` 既有用例零修改通过

---

## T010 CLI 与阈值接线验证

### 目标

`check-parse-quality --no-semantic`；并以变异测试证明阈值非装饰性配置。

### 验收标准

- [ ] `--no-semantic` 默认 False；`scope` 块输出 `"semantic": true|false`
- [ ] 阈值变异：注入 `warn_ratio=0.50` → `OCR_GARBLED_RATIO` 消失；`min_mean=0.10` → `OCR_LOW_CONFIDENCE` 消失；恢复后复现
- [ ] 否定断言：`--no-semantic` 报告运行期 `parsed_text.md` 未被读取（monkeypatch `Path.read_text` 计数为 0）

---

## T011 红线、回归与 STOP

### 目标

只读红线 + 幂等 + 全量回归，随后 STOP。

### 禁止事项

- Dev **不得**自我宣布 A016-01 ~ A016-10 通过；**不得**写 handoff；**不得** merge main

### 验收标准

- [ ] 检查前后 `parsed/**` 与 `raw_vault/**` listing + sha256 不变；DB 行数快照对比 SELECT-only
- [ ] 连续两次同配置：除 `generated_at` 与报告文件名外逐字段一致
- [ ] 单候选读取 OSError → 该候选跳过语义检查，其余正常，批处理不中断
- [ ] `中文路径` 下产物全链路通过，报告 `ensure_ascii=False` 无乱码
- [ ] `test_parse_quality_checker.py`、`test_parse_quality_report_summarizer.py` 既有用例**零修改**通过；001–013 全量回归通过
- [ ] Dev 输出：修改文件清单、pytest 命令、CLI 命令、遗留问题 → STOP → P2 DB Review → QA → E2E → TL Final Review

---

## 任务进度总览

| Task | 说明 | 状态 |
|------|------|------|
| T001 | 阅读 008/009 与规格 | [ ] |
| T002 | 测试先行（红测 + fixture 构造器） | [ ] |
| T003 | SemanticCheckConfig + quality_semantics.yaml | [ ] |
| T004 | `_is_garbled_char` + OCR_GARBLED_RATIO | [ ] |
| T005 | OCR_LOW_CONFIDENCE / UNAVAILABLE | [ ] |
| T006 | TABLE_STRUCTURE_INCOMPLETE | [ ] |
| T007 | EMPTY_BUT_NONZERO_PAGES | [ ] |
| T008 | 报告 schema 1.1 与 issue_counts | [ ] |
| T009 | 009 summarizer 联动 | [ ] |
| T010 | CLI --no-semantic + 阈值接线变异 | [ ] |
| T011 | 红线 / 回归 / STOP | [ ] |

---

**Tasks 结束** — Dev 从 T001 开始，严格在白名单内实现。
