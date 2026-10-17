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
