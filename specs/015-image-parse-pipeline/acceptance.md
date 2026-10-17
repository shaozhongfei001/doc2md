# Acceptance: 独立图片解析管线（015）

> **Spec**：`specs/015-image-parse-pipeline/`
> **Plan 对照**：`plan.md` §12 / §13
> **测试文件**：`backend/tests/test_image_parser.py`
> **反假绿原则**：每条「必须产生 X」均配「变异/对照必须不产生 X 或必须失败」；Mock 引擎绝不允许制造「管线通的」假象。

---

## A015-01 清晰图 → PaddleOCR 主路径产出三件套

- Pillow 现场生成含文字的清晰 PNG（mock `OcrResult(text=..., confidence_mean=0.9, mock=False, engine="paddleocr")`）→ `parsed_text.md` / `parsed_metadata.json` / `parse_manifest.json` 三件套存在；manifest `status=SUCCESS`、`parser_name="image_ocr"`、`route_type="IMAGE"`；metadata 含 `image_quality.score` 与 `ocr_confidence.mean`。
- **对照**：非 IMAGE content（`.txt`）同批处理 → `ROUTE_MISMATCH / SKIPPED`，其 parsed 目录**不存在**。

## A015-02 低质图路由到 VLM；Mock VLM 必须被拒

- 高斯模糊 fixture（评分 < 阈值）+ `--engine auto` + 默认 `MockVlmClient` → 条目 `decision=UNSUPPORTED_WITH_HINT`、`status=SKIPPED`。
- **否定断言**：该 sha256 的 parsed 目录**不存在**；报告中 `errors` 不含伪造成功；`parsed_text.md` 全局搜索无 mock 文本。

## A015-03 引擎路由真值表逐行验证

- 覆盖 plan §6 的 9 行组合（monkeypatch 引擎可用性 / score 注入 / `--engine` 覆盖）。
- **否定断言**：`--engine paddleocr` 且 paddleocr 缺失 → `DEPENDENCY_MISSING`，非 dry-run CLI exit 1；不得静默降级到 Mock VLM 落盘。

## A015-04 预处理尽力而为 + 失败不中断

- 预处理 deskew 步骤被 monkeypatch 为抛异常 → 解析仍完成，`parsed_metadata.json.extra.preprocess_ops` 不含 deskew 或标记 `skipped`，WARNING 日志。
- **否定断言**：不得因预处理失败把条目变成 FAILED；也不得静默声称 deskew 已执行。

## A015-05 评分失败 fail-safe 到 VLM 路径

- 评分器返回 `score=None`（解码失败 fixture）→ 引擎路由选择 VLM（`engine.actual="vlm"` 或 UNSUPPORTED_WITH_HINT 当 VLM=mock）。
- **否定断言**：不得路由到 paddleocr（无法证明清晰时不得走清晰路径）。

## A015-06 Mock 诚实性变异复验（反假绿核心）

- 变异 A：把 service 层 `result.mock` 拒绝逻辑注释掉 → 测试必须 FAIL（出现 mock 文本落盘断言）。
- 变异 B：`MockVlmClient` 返回非空伪造文本 → 管线仍不得写出 `parsed_text.md`。
- 恢复后全绿。两个变异都必须被捕获，缺一则验收不通过。

## A015-07 幂等与 force

- 同 sha256 + 同 parser_profile 连续两次 `parse-images`：第二次 `ALREADY_SUCCESS / SKIPPED`，三件套 mtime 不变。
- 换 `parser_profile`（如 `image_vlm_v1` → `image_paddleocr_v1`）→ 重跑（符合「同 sha256+parser_profile 不重跑」的逆命题）。
- `--force` → 重跑且产物更新。
- **否定断言**：第二次运行 monkeypatch 引擎为「被调用即 fail」→ 测试仍须通过（证明真 skip）。

## A015-08 dry-run 零副作用

- `parse-images --dry-run`：parsed/ 无新增、reports/ 无新文件、DB 零写、raw_vault 不变；引擎 mock 被替换为「被调用即 fail」仍通过（证明零引擎调用）；stdout 输出 `would_parse/would_skip`。

## A015-09 单条失败不中断批处理

- 三条候选：正常图 / 损坏图字节（`.png` 扩展非法内容）/ 正常图 → 2 条 SUCCESS、1 条 FAILED（`errors[]` 含 sha256+message），exit 0。
- **否定断言**：FAILED 条目不产生 parsed 产物；另两条不受影响的断言必须存在且为真。

## A015-10 原始文件与 raw_vault 保护

- 执行前后原始 fixture 与 `raw_vault/**` 的 listing + sha256 全等；`ensure_readonly()` 调用代码审查；`curated/`、`quarantine/` 零写入。

## A015-11 中文路径兼容

- `中文路径/图片/票据样张.png` 风格 fixture 全链路（scan → copy-to-vault → parse-images mock 引擎）：产物路径按 sha256（ASCII），manifest `source_vault_path` 正确记录中文来源链，报告 JSON 中文无乱码（`ensure_ascii=False`）。

## A015-12 依赖可选化与回归

- 在**无 paddleocr、无 cv2** 环境跑 `pytest -q tests/test_image_parser.py` 全绿（引擎全部注入 mock）。
- 001–013 既有测试回归通过（特别 `test_mineru_pdf_parser.py`、`test_parser_router.py` 零修改通过——证明 015 未碰 004/007）。
- grep 审查：015 代码无 `magic-pdf` / `markitdown` 调用；无 DB raw SQL 写；`--register` 路径仅经 `ParseRegistryService`。

---

## 验收门禁汇总

| 门禁 | 通过条件 |
|------|----------|
| 引擎路由正确性 | A015-01 ~ A015-05 全过（真值表 9 行全覆盖） |
| 反假绿 | A015-06 两个变异均被捕获；A015-02/03/05/07/08/09 否定断言全真 |
| 幂等与批处理 | A015-07、A015-09 全过 |
| 安全红线 | A015-08、A015-10、A015-11 全过 |
| 回归 | A015-12 全过 |
