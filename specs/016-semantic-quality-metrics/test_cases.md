# Test Cases: 语义质检指标（016）

> **Plan 对照**：`plan.md` §4 / §8 / §9
> **验收对照**：`acceptance.md` A016-01 ~ A016-10
> **反假绿**：每个 code 必须同时证明"该报时报"与"不该报时不报"；阈值以变异证明真实接线

---

| 编号 | 场景 | 输入 | 预期 |
|------|------|------|------|
| **TC016-01** | 乱码 WARNING 档 | 2000 字符中文正文混入 5% U+FFFD + 控制符 | 含 `OCR_GARBLED_RATIO`，severity=WARNING；`evidence.garbled_ratio ∈ [0.04, 0.06]` |
| **TC016-02** | 乱码 ERROR 档 | 同构 15% 乱码 | severity=ERROR |
| **TC016-03** | 干净正文对照 | 无乱码中文正文 | **否定**：`assert "OCR_GARBLED_RATIO" not in codes` |
| **TC016-04** | 样本不足整体跳过 | 50 字符且全为 U+FFFD | **无** `OCR_GARBLED_RATIO`（`total_chars < min_sample_chars`） |
| **TC016-05** | 样本不足的对照（证明跳过是显式逻辑） | 同内容填充至 ≥200 字符 | 必须产生 `OCR_GARBLED_RATIO` |
| **TC016-06** | 低置信度正例 | `ocr_confidence.mean=0.55`、`parser_name="image_ocr"` | `OCR_LOW_CONFIDENCE`(WARNING)，evidence 含实测值与阈值 |
| **TC016-07** | 高置信度对照 | `mean=0.95` | 无 `OCR_LOW_CONFIDENCE` |
| **TC016-08** | 缺字段反编造（核心） | `parser_name="mineru"` 且 metadata **无** `ocr_confidence` | 仅 `OCR_CONFIDENCE_UNAVAILABLE`(INFO)；**否定**：`assert "OCR_LOW_CONFIDENCE" not in codes` |
| **TC016-09** | 非 OCR 族不评估 | `parser_name="markitdown"` 且无 confidence | 两个 code 均不产 |
| **TC016-10** | metadata JSON 非法 | 损坏的 `parsed_metadata.json` | confidence 检查跳过；不新增 code；批处理不中断 |
| **TC016-11** | ragged pipe 表 | 3 列表头，其中一数据行 2 列 | `TABLE_STRUCTURE_INCOMPLETE`(WARNING)，evidence 含残缺行号 |
| **TC016-12** | 规范表 / 无表对照 | 列数全一致的表；纯文本无表 | 均无 `TABLE_STRUCTURE_INCOMPLETE` |
| **TC016-13** | 表格检查关闭 | `tables.enabled=false` | 整体跳过，零 issue |
| **TC016-14** | 空正文非零体积 | `status=SUCCESS`、`content_size_bytes=54321`、正文全空白 | ERROR `EMPTY_BUT_NONZERO_PAGES` |
| **TC016-15** | 对照 A：EMPTY 状态 | manifest `status=EMPTY` 同条件 | 不产 `EMPTY_BUT_NONZERO_PAGES`（既有 `REGISTRY_EMPTY_RESULT` 覆盖） |
| **TC016-16** | 对照 B：正文非空 | `status=SUCCESS` + 正文非空 | 不产该 code |
| **TC016-17** | 阈值变异：乱码 | 5% 乱码 fixture + 注入 `warn_ratio=0.50` → 恢复 0.02 | 注入时 issue **消失**；恢复后**复现**（未捕获即验收不通过） |
| **TC016-18** | 阈值变异：置信度 | `mean=0.55` + 注入 `min_mean=0.10` → 恢复 0.70 | 注入时 issue **消失**；恢复后**复现** |
| **TC016-19** | `--no-semantic` 冻结 008 | 同批候选分别跑默认 / `--no-semantic` | 后者 `schema_version="1.0"`、`issue_counts` 仅 18 键、无语义 code、无 `semantic_checks_enabled` 键 |
| **TC016-20** | `--no-semantic` 零读取 | 同上一用例 | **否定**：`Path.read_text` monkeypatch 计数器为 0（未读 parsed_text.md） |
| **TC016-21** | 报告 schema 1.1 | 默认（语义开启） | `schema_version="1.1"`、`semantic_checks_enabled=true`、`summary.checked_semantic_count` 存在、`issue_counts` 含 5 新键 |
| **TC016-22** | 幂等 | 同配置连续两次 | 除 `generated_at` 与报告文件名外，issue 集合逐字段一致 |
| **TC016-23** | 只读红线 | 检查前后 `parsed/**`、`raw_vault/**` listing + sha256 | 完全一致；DB 仅 SELECT（行数快照对比） |
| **TC016-24** | 单候选读取失败容错 | monkeypatch 某候选 `parsed_text.md` 读取 OSError | 该候选跳过语义检查；其余候选 issue 正常；批处理不中断 |
| **TC016-25** | 009 summarizer 联动 | 含 5 个新 code 的报告喂给 009 | 不抛 unknown code；新 code 进入汇总计数 |
| **TC016-26** | 009 code 过滤 | `--issue-codes OCR_GARBLED_RATIO` | 过滤可用（合法性校验接受新 code） |
| **TC016-27** | image_ocr 白名单 | 015 的 `parser_name="image_ocr"` manifest | 经 008+016 检查**不产** `MANIFEST_PARSER_NAME_INVALID` |
| **TC016-28** | 中文路径全链路 | `中文路径` 下 parsed 产物（中文正文 + 乱码混入） | evidence 中文无乱码、路径字段正确 |
| **TC016-29** | 既有测试零回归 | `pytest -q tests/test_parse_quality_checker.py tests/test_parse_quality_report_summarizer.py` | 既有用例**零修改**全过 |
| **TC016-30** | 阈值非法 fail-fast | yaml `warn_ratio=-1` 或 `>1` | `ValueError` 启动即失败（不静默） |

---

## pytest 映射（建议）

| Test Case | 建议 test function |
|-----------|--------------------|
| TC016-01 / 02 / 03 | `test_parse_quality_checker_semantic.py::test_garbled_warning` / `test_garbled_error` / `test_clean_text_no_garbled_issue` |
| TC016-04 / 05 | `test_garbled_skipped_when_sample_too_small` / `test_garbled_detected_after_padding` |
| TC016-06 / 07 / 08 / 09 | `test_low_confidence` / `test_high_confidence_no_issue` / `test_missing_confidence_info_only` / `test_markitdown_not_evaluated` |
| TC016-10 | `test_invalid_metadata_json_skips_confidence` |
| TC016-11 / 12 / 13 | `test_ragged_pipe_table` / `test_valid_table_no_issue` / `test_tables_disabled_skips` |
| TC016-14 / 15 / 16 | `test_empty_but_nonzero_pages` / `test_empty_status_no_issue` / `test_nonempty_text_no_issue` |
| TC016-17 / 18 | `test_garbled_threshold_mutation` / `test_confidence_threshold_mutation` |
| TC016-19 / 20 | `test_no_semantic_freezes_008_report` / `test_no_semantic_does_not_read_text` |
| TC016-21 | `test_report_schema_1_1` |
| TC016-22 / 23 | `test_idempotent_issue_set` / `test_readonly_redline` |
| TC016-24 | `test_read_failure_isolated_to_candidate` |
| TC016-25 / 26 / 27 | `test_summarizer_accepts_new_codes` / `test_issue_code_filter` / `test_image_ocr_parser_name_allowed` |
| TC016-28 / 29 / 30 | `test_chinese_path_semantic` / 既有测试全量 / `test_invalid_threshold_fail_fast` |
