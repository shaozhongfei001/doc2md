# Test Cases: 独立图片解析管线（015）

> **Plan 对照**：`plan.md` §6 / §10 / §13
> **验收对照**：`acceptance.md` A015-01 ~ A015-12
> **反假绿**：每条正例均配对照或否定断言；Mock 引擎不得制造"管线通的"假象

---

| 编号 | 场景 | 输入 | 预期 |
|------|------|------|------|
| **TC015-01** | 清晰图主路径 | Pillow 生成含文字清晰 PNG + mock `OcrResult(mock=False, engine="paddleocr", confidence_mean=0.9)` | 三件套存在；`status=SUCCESS`、`parser_name="image_ocr"`、`route_type="IMAGE"`；metadata 含 `image_quality.score` + `ocr_confidence.mean` |
| **TC015-02** | 路由隔离（对照） | 同批混入 `.txt` | `.txt` 条目 `ROUTE_MISMATCH / SKIPPED`；其 parsed 目录**不存在** |
| **TC015-03** | 低质图 + Mock VLM 必须被拒 | 高斯模糊图（score < 阈值）+ `--engine auto` + `MockVlmClient` | `UNSUPPORTED_WITH_HINT / SKIPPED`；**否定**：parsed 目录不存在、无 mock 文本落盘 |
| **TC015-04** | 引擎真值表 9 行 | monkeypatch 引擎可用性 + score 注入 + `--engine` 覆盖 | 逐行符合 `plan.md` §6；`paddleocr` 主路径 / VLM 兜底 / `fallback_reason=paddleocr_missing` 各就位 |
| **TC015-05** | `--engine paddleocr` 缺依赖 | paddleocr 不可用 + 非 dry-run | `DEPENDENCY_MISSING`；CLI exit 1；**否定**：不得静默降级 Mock VLM 落盘 |
| **TC015-06** | `--engine vlm` + mock | `MockVlmClient` | `UNSUPPORTED_WITH_HINT / SKIPPED`；零落盘 |
| **TC015-07** | 预处理单步失败 | monkeypatch `deskew` 抛异常 | 解析仍完成；`extra.preprocess_ops` 标 skipped；WARNING；**否定**：条目不得变 FAILED、不得声称 deskew 已执行 |
| **TC015-08** | cv2 全缺失 | monkeypatch cv2 import 失败 | 全 op `skipped: opencv_missing`，原图直通，解析不失败 |
| **TC015-09** | 评分失败 fail-safe | `score=None`（解码失败 fixture） | 路由至 VLM（或 mock VLM 下 `UNSUPPORTED_WITH_HINT`）；**否定**：不得路由 paddleocr |
| **TC015-10** | Mock 诚实性变异 A | 注释掉 service 层 `result.mock` 拒绝逻辑 | 测试必须 **FAIL**（出现 mock 文本落盘断言） |
| **TC015-11** | Mock 诚实性变异 B | `MockVlmClient` 返回非空伪造文本 | 管线仍**不得**写出 `parsed_text.md`；恢复后全绿 |
| **TC015-12** | 幂等命中 | 同 sha256 + 同 parser_profile 连续两次 | 第二次 `ALREADY_SUCCESS`，三件套 mtime 不变；monkeypatch 引擎"被调用即 fail"仍通过 |
| **TC015-13** | parser_profile 变更重跑 | `image_paddleocr_v1` → `image_vlm_v1` | 重跑并更新产物 |
| **TC015-14** | `--force` 重跑 | 已 SUCCESS 条目 + `--force` | 清三件套 + assets 后重跑，产物更新 |
| **TC015-15** | dry-run 零副作用 | `parse-images --dry-run` | parsed/ 无新增、reports/ 无新文件、DB 零写；引擎"被调用即 fail"仍通过；stdout 含 `would_parse/would_skip` |
| **TC015-16** | 单条失败不中断 | 正常图 / 损坏字节 `.png` / 正常图 | 2 SUCCESS + 1 FAILED（`errors[]` 含 sha256 + message）；exit 0；**否定**：FAILED 条目零 parsed 产物 |
| **TC015-17** | 引擎超时 | mock 引擎 sleep 超 `timeout_seconds` | `TIMEOUT / FAILED` + `errors[]`；批处理 continue |
| **TC015-18** | 空文本 | 引擎返回 `text=""` | manifest `status=EMPTY`（对齐 007 语义）；不产伪造正文 |
| **TC015-19** | 原始文件与 raw_vault 保护 | 全链路前后 stat/hash/listing | 完全一致；`ensure_readonly()` 已调用；`curated/`、`quarantine/` 零写入 |
| **TC015-20** | 中文路径 | `中文路径/图片/票据样张.png` 全链路 | 产物路径按 sha256（ASCII）；manifest `source_vault_path` 记录中文来源；报告 JSON 无乱码 |
| **TC015-21** | GIF 首帧 | `.gif` fixture | Pillow 取首帧处理；`engine_meta["gif_first_frame"]=true` |
| **TC015-22** | 未知 vlm adapter | `vlm.adapter: "unknown"` | `build_vlm_client` → `ValueError`（fail-fast，不静默降级） |
| **TC015-23** | 依赖可选化 | 无 paddleocr、无 cv2 环境 | `pytest -q tests/test_image_parser.py` 全绿（引擎全注入 mock） |
| **TC015-24** | 004/007 零回归 | `pytest -q tests/test_parser_router.py tests/test_mineru_pdf_parser.py` | 既有用例**零修改**全过 |

---

## pytest 映射（建议）

| Test Case | 建议 test function |
|-----------|--------------------|
| TC015-01 / 02 | `test_image_parser.py::test_clear_image_paddleocr_success` / `test_non_image_route_mismatch` |
| TC015-03 | `test_low_quality_mock_vlm_rejected` |
| TC015-04 / 05 / 06 | `test_engine_routing_truth_table`（参数化 9 行）/ `test_paddleocr_missing_dependency_exit1` / `test_vlm_mock_unsupported` |
| TC015-07 / 08 | `test_preprocess_step_failure_continues` / `test_cv2_missing_passthrough` |
| TC015-09 | `test_score_failure_fail_safe_to_vlm` |
| TC015-10 / 11 | `test_mock_rejection_mutation_a` / `test_mock_rejection_mutation_b` |
| TC015-12 / 13 / 14 | `test_idempotent_skip` / `test_parser_profile_change_rerun` / `test_force_rerun` |
| TC015-15 | `test_dry_run_zero_side_effects` |
| TC015-16 / 17 / 18 | `test_batch_continues_on_failure` / `test_engine_timeout` / `test_empty_text_status_empty` |
| TC015-19 / 20 / 21 | `test_original_files_unchanged` / `test_chinese_path_image` / `test_gif_first_frame` |
| TC015-22 | `test_build_vlm_client_unknown_adapter_raises` |
| TC015-23 | CI 环境整体全绿（无重依赖） |
| TC015-24 | 既有 `test_parser_router.py` / `test_mineru_pdf_parser.py` 全量 |
