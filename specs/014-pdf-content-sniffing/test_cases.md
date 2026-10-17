# Test Cases: PDF 内容嗅探路由修复（014）

> **Plan 对照**：`plan.md` §8 / §13
> **验收对照**：`acceptance.md` A014-01 ~ A014-12

---

| 编号 | 场景 | 输入 | 预期 |
|------|------|------|------|
| **TC014-01** | 数字版 PDF 判 digital | pypdf 现场生成含文本层 PDF（≥100 字符、页均 ≥20） | `route_type=PDF_DIGITAL`；`rule_name=pdf_sniff_digital`；`pdf_sniff.status=TEXT_LAYER_PRESENT` |
| **TC014-02** | 扫描版 PDF 判 scanned（死枚举激活） | pypdf 空白页 PDF（无文本层） | `route_type=PDF_SCANNED_OR_IMAGE`；`rule_name=pdf_sniff_scanned`；**否定**：不得为 `PDF_DIGITAL` |
| **TC014-03** | 阈值边界 | 字符数恰在阈值上下各一档的两个 fixture | 上档 digital、下档 scanned；阈值改自 yaml 后结果翻转（阈值真实接线） |
| **TC014-04** | 加密 PDF fail-closed | `writer.encrypt("pw")` PDF | `pdf_sniff_encrypted` → scanned；不抛异常；不尝试口令（monkeypatch `decrypt` 被调用即 fail） |
| **TC014-05** | 损坏 PDF fail-closed 且批处理继续 | 截断字节 `.pdf` + 同批正常 `.txt` | `pdf_sniff_error` → scanned；`.txt` 正常路由；exit 0 |
| **TC014-06** | 空页 PDF | 0 页 PDF | `pdf_sniff_empty` → scanned |
| **TC014-07** | pypdf 缺失 | monkeypatch import 抛 ImportError | `pdf_sniff_unavailable` → scanned；CLI 不崩溃 |
| **TC014-08** | 缓存命中幂等 | 同 sha256 两次 route-parsers（v2） | 第二次 `from_cache=true`；决策逐字段一致；monkeypatch sniff 为 fail 仍通过 |
| **TC014-09** | 缓存防投毒（三变异） | 篡改缓存 sha256 / sniffer_version / 非法 JSON | 三种均弃用重算；脏数据不进报告 `pdf_sniff` |
| **TC014-10** | 缓存写失败降级 | monkeypatch 缓存目录不可写 | WARNING；路由结果正常产出 |
| **TC014-11** | v1 回退逐字节复现 | `--routing-rules-version 004_mvp_v1` + scanned fixture | `PDF_DIGITAL` / `ext_pdf_digital`；`routing_rules_version=004_mvp_v1`；monkeypatch sniff 为 fail 仍通过（v1 不读 bin） |
| **TC014-12** | dry-run 零副作用 | `route-parsers --dry-run`（v2） | sniff_cache 与 reports 无新增；raw_vault 不变；stdout 含 decision 摘要 |
| **TC014-13** | vault bin 缺失回退 | DB 行 COPIED 但 bin 被移走（测试环境） | 回退 `ext_pdf_digital` + WARNING reason；不抛异常 |
| **TC014-14** | 007 联动一致 | scanned fixture 经 `MineruPdfParserService._build_plan` | plan `route_type=PDF_SCANNED_OR_IMAGE`；**否定**：不得为 `PDF_DIGITAL` |
| **TC014-15** | 原始文件与 raw_vault 保护 | 全链路前后 stat/hash/listing | 完全一致；`ensure_readonly()` 已调用 |
| **TC014-16** | 中文路径 | `中文路径/` 中文名 PDF 全链路 | 路由正常；缓存文件名为 sha256（ASCII）；报告中文无乱码 |
| **TC014-17** | 004 零回归 | `pytest -q tests/test_parser_router.py` | 既有用例零修改全过（`match_route_type` v1 行为冻结） |

---

## pytest 映射（建议）

| Test Case | 建议 test function |
|-----------|--------------------|
| TC014-01 / 02 / 03 | `test_pdf_sniffing.py::test_sniff_digital_pdf` / `test_sniff_scanned_pdf` / `test_sniff_threshold_boundary` |
| TC014-04 | `test_sniff_encrypted_fail_closed` |
| TC014-05 | `test_sniff_corrupted_fail_closed_batch_continues` |
| TC014-06 / 07 | `test_sniff_empty_pdf` / `test_sniff_pypdf_missing` |
| TC014-08 | `test_parser_router_v2.py::test_sniff_cache_hit_idempotent` |
| TC014-09 | `test_sniff_cache_poisoning_rejected`（三变异参数化） |
| TC014-10 | `test_sniff_cache_write_failure_degrades` |
| TC014-11 | `test_v1_rules_version_no_sniff` |
| TC014-12 | `test_dry_run_zero_side_effects` |
| TC014-13 | `test_missing_original_bin_fallback_v1` |
| TC014-14 | `test_mineru_build_plan_scanned_consistent` |
| TC014-15 / 16 | `test_original_files_unchanged` / `test_chinese_path_pdf`（v2 版） |
| TC014-17 | 既有 `test_parser_router.py` 全量 |
