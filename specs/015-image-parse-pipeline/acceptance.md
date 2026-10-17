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

---

## QA 独立验收结论（qa-015，2026-10-17）

**角色**：独立验收 QA（非实现者，不对实现作辩解）。**执行环境**：`/home/szf/anaconda3/bin/python`（3.11.7）；命令一律 `cd /home/szf/dev/doc2md/backend && PYTHONPATH=. <py> -m pytest ...`；已注入真实 MySQL 参数（`PKB_MYSQL_*`，密码不回显）。
**验收代码基线**：`HEAD=f269a13`，015 实现提交 `8d4decc`（父提交 `9b70ea0`=014 tip）。`8d4decc` 改动文件 = 015 白名单 8 个 + 015 spec 文档 6 个，**未触及** `parser_router.py` / `mineru_pdf_parser.py` / `test_parser_router.py` / `test_mineru_pdf_parser.py`（后 4 者最后一次改动是 014 提交 `9b70ea0`）。

**影响判定口径的环境事实（必须先看）**：本机 `paddleocr 2.7.3`、`cv2 4.11.0`、`Pillow` **均已安装**。因此 `tests/test_image_parser.py` 默认跑法为 **53 passed / 2 skipped**，2 个 skip 恰好是 `DEPENDENCY_MISSING` 的两个分支（`test_check_paddleocr_available_reports_dependency_missing:404`、`test_paddleocr_client_recognize_without_dependency_raises:415`）。只看这组数字会漏掉缺依赖分支 —— 已用导入拦截器复验（见 A015-12 与 D-5）。

### 1. 逐条判定表（A015-01 ~ A015-12）

| 条目 | 判定 | 实测证据（命令/数字/文件:行号） |
|------|------|-----------------------------------|
| A015-01 清晰图→PaddleOCR 三件套 | **PASS** | 独立脚本（Pillow 现场生成 900x300 文字图）→ `decision=PARSED`、`manifest.status=SUCCESS`、`parser_name=image_ocr`、`route_type=IMAGE`、`metadata.image_quality.score=3957.6`（laplacian）、`ocr_confidence.mean=0.91`，三件套均存在。同批对照 `.txt` → `ROUTE_MISMATCH / SKIPPED`，其 `parsed_dir.exists()=False` |
| A015-02 低质图→VLM；Mock VLM 必须被拒 | **PASS** | 高斯模糊 blur=10 实测 `score=0.87 < 100`；**不注入 vlm_client**（走 `build_vlm_client(config)` 真实默认路径，实测类型 `MockVlmClient`）→ `UNSUPPORTED_WITH_HINT / SKIPPED`，`skip_reason=no_trusted_engine`，`parsed_dir.exists()=False`，`errors=0`；对 `parsed_root/**/parsed_text.md` 全量搜索 `mock`/`伪造` → **命中 0** |
| A015-03 引擎路由真值表 | **PASS** | 纯函数 `resolve_engine_route()` 11 组合全对；端到端（注入 score/可用性/引擎）11 组合全对：<br>`auto·clear·avail→paddleocr`、`auto·low·real→vlm`、`auto·None·real→vlm`、`auto·low·mock→UNSUPPORTED`、`auto·None·mock→UNSUPPORTED`、`auto·clear·missing·real→vlm(fallback_reason=paddleocr_missing)`、`auto·clear·missing·mock→UNSUPPORTED`、`paddleocr·missing→DEPENDENCY_MISSING(FAILED,零落盘)`、`paddleocr·avail·low→paddleocr(warnings 非空)`、`vlm·mock→UNSUPPORTED`、`vlm·real→vlm`。<br>CLI 实测（拦截 paddleocr）：`parse-images --engine paddleocr --limit 5` → **exit_code=1**、stdout `ERROR: DEPENDENCY_MISSING: paddleocr 不可导入…`，5 个 storage 根文件集零变化（无报告、无产物）→ **未静默降级到 mock VLM 落盘** |
| A015-04 预处理尽力而为 | **PASS** | 只把 `deskew` 打成抛异常（monkeypatch `image_preprocess._apply_op`）→ `status=SUCCESS`（未变 FAILED）、`extra.preprocess_ops=['denoise','binarize']`（不含 deskew）、`extra.preprocess_skipped_ops=['deskew:error']`、捕获到 1 条 WARNING（`image_preprocess.py:199`「预处理 op 失败已跳过 op=deskew」）→ 未静默声称 deskew 已执行 |
| A015-05 评分失败 fail-safe 到 VLM | **PASS** | 三种情形均**未**路由到 paddleocr：① score 注入 `None`+真实 VLM → `engine_actual=vlm`、paddleocr 客户端调用 0 次；② score=`None`+mock VLM → `UNSUPPORTED_WITH_HINT`；③ 损坏 PNG 字节走真实评分器 → `score=None`（`unavailable`）→ `PARSED/vlm` |
| A015-06 Mock 诚实性变异复验 | **PASS** | 见 §2。两个变异均被捕获，逐字还原后 md5 与还原前一致、用例恢复 PASS |
| A015-07 幂等与 force | **PASS** | 同 sha+同 profile 第二次 → `ALREADY_SUCCESS / SKIPPED`、`skip_reason=idempotent_success_manifest`、三件套 `st_mtime_ns` 逐文件相等、引擎调用次数=1；**否定断言**：换「被调用即 fail」引擎重跑 → `ALREADY_SUCCESS` 且 `called=0`（证明真 skip）；`image_paddleocr_v1→image_vlm_v1` → 重跑（vlm.calls=1）；`force=True` → 重跑（ocr.calls=2）且预置的 `stale.txt` 被清空 |
| A015-08 dry-run 零副作用 | **PASS** | ① service 级：引擎换「被调用即 fail」→ `called=0`、`report_path=None`、`parsed_root/reports_root/raw_vault/curated/quarantine` 五棵树的文件集与逐文件 sha256 **完全相等**、无残留 `.staging`、`dry_run_action=would_parse`；② **真实配置 + 真实 DB** 实测 `parse-images --dry-run --limit 10` → exit 0，21 张表逐表行数（合计 183）不变、5 个 storage 根零变化，stdout 输出 `Plan: … (SKIPPED)` 与 `Dry run: no parsed artifacts or batch report written`。代码侧核对：`main.py:615 if not dry_run:` 之后才写报告/注册；`_preview_route()` 用 `tempfile.TemporaryDirectory()`（`image_parser.py:1100`） |
| A015-09 单条失败不中断批处理 | **PASS**（含口径偏差 D-3） | 三条候选（正常/损坏字节/正常）。场景 A（默认 mock VLM+auto）：`PARSED/SUCCESS`、`UNSUPPORTED_WITH_HINT/SKIPPED`、`PARSED/SUCCESS`；场景 B（paddleocr+引擎在损坏图抛异常）：`PARSED/SUCCESS`、`ENGINE_ERROR/FAILED`、`PARSED/SUCCESS`，`errors=['ENGINE_ERROR']` 含该 sha256+message，FAILED 条目 `parsed_dir.exists()=False`，另两条产物正常。exit 0 依据：`main.py:1189-1206` 无任何基于 `failed_count` 的 `typer.Exit`（代码审查）+ dry-run CLI 实测 exit 0 |
| A015-10 原始文件与 raw_vault 保护 | **PASS** | 解析前后 `sources/**`（2 文件）与 `raw_vault/**`（2 文件）的 listing + 逐文件 sha256 **全等**；`curated/`、`quarantine/` 零写入；`ensure_readonly(config)` 位于 `image_parser.py:501`（构造函数首行），早于 `create_db_engine(config)`（`:517`）；`core/config.py:112-114` 在 `original_files_readonly != true` 时 `raise RuntimeError` |
| A015-11 中文路径兼容 | **PASS**（含口径偏差 D-4） | 中文 workspace/vault 根 + 中文来源文件名（`中文路径/图片/票据样张.png`）+ 中文 `content_uid`：产物目录名 `1c18dde281c8…`（64 位纯 ASCII hex）、`manifest.source_vault_path`/`metadata.source_vault_path` 保留中文原文（`…/中文工作区_015/raw_vault/by_hash/1c/1c18…/original.bin`）、报告 JSON 中中文原文保留且 **无 `\uXXXX` 转义**（`image_parser.py:1007/1034/1185` 均 `ensure_ascii=False`）、`status=SUCCESS` |
| A015-12 依赖可选化与回归 | **FAIL** | ① 缺 paddleocr（导入拦截）→ **55 passed / 0 skipped**（2 个原本 skip 的用例真正执行并通过）✓；② **缺 paddleocr + cv2 → 10 failed / 45 passed ✗**（失败用例：`test_clear_image_scores_above_frozen_threshold`、`test_blurred_image_scores_below_frozen_threshold`、`test_scorer_auto_uses_laplacian_when_cv2_available`、`test_preprocess_applies_configured_ops`、`test_preprocess_degrades_when_every_op_fails`、`test_preprocess_unreadable_source_is_marked_skipped`、`test_auto_clear_paddleocr_available_uses_paddleocr`、`test_auto_clear_paddleocr_missing_real_vlm_fallback`、`test_success_run_writes_spec_metadata_contract`、`test_assets_copied_and_indexed_in_metadata`）；③ 回归 ✓：全量 **437 passed / 4 failed / 2 skipped**，4 个 failed 在 015 父提交 `9b70ea0` 上**同样 4 failed**（该基线为 383 passed / 4 failed / 1 skipped）→ 015 净增 55 用例、零新增失败；④ 静态审查 ✓：015 五文件中 `magic-pdf`/`markitdown` **0 命中**、`INSERT/UPDATE/DELETE/REPLACE` **0 命中**（仅 `from sqlalchemy import select` 读路径，`:657/671/691`）、`--register` 仅经 `_register_report()` → `ParseRegistryService.register_parse_report()`（`:1189-1195`） |

**计数：PASS 11 / FAIL 1 / NOT_COVERED 见 §5（A015-09、A015-11 虽有口径偏差但功能断言成立，计 PASS 并留痕）。**

### 2. 变异复验记录（A015-06 反假绿核心）

**先定位真判定点**（不是测试注释）：`backend/app/services/image_parser.py:918-926`

```919:926:backend/app/services/image_parser.py
            if ocr_result.mock:
                item.decision = DECISION_UNSUPPORTED_WITH_HINT
                item.status = STATUS_SKIPPED
                item.skip_reason = SKIP_REASON_MOCK_RESULT
                item.warnings.append(
                    "引擎返回 mock 结果（无真实识别能力），已拒绝落盘"
                )
                return item, None
```

**分支真的会被进入吗（防「断言空转」）**：不接受测试注释作证据，改为读取只有该分支体会写入的两个字段——实测命中条目 `skip_reason=mock_result_rejected`、`warnings` 含「已拒绝落盘」，且 `forged.calls=1`（引擎确被调用过，排除「路由层提前短路导致断言恒真」）。`vlm_adapter.client_is_mock()` 刻意不读运行时 `is_mock`（docstring `:99-102` 说明理由），故 mock 结果一定进入 service 层的这条 `if`。

| 变异 | 变异点 | 变异态 md5 | 期望 | 实测结果 | 还原 md5 | 还原后实测 |
|------|--------|-----------|------|----------|----------|-----------|
| A（无条件放行） | `image_parser.py:919` `if ocr_result.mock:` → `if False and ocr_result.mock:` | `0281c020ec1014c07aabdd20def0d315` | 必须 FAIL | **2 failed**：`test_mock_result_is_never_persisted_with_forged_text`（`test_image_parser.py:588` `assert item.decision == DECISION_UNSUPPORTED_WITH_HINT`）、`test_mock_paddleocr_result_is_also_rejected`（`:605` `assert 'PARSED' == 'UNSUPPORTED_WITH_HINT'`）。变异态另做实证：`decision=PARSED/status=SUCCESS`、`parsed_text.md` 内容 = `'伪造的识别结果，不得落盘'`、`manifest.status=SUCCESS` → 正是假绿形态 | `927b9029776d4221e1113142936236de`（= 变异前） | **3 passed** |
| B（Mock 伪造非空文本） | `vlm_adapter.py:66` `text=""` → `text="伪造的 mock 识别文本，不得落盘"`（`mock=True` 保持不变） | `bb92b08cd22dc5d5629a3e5fef4ea510` | 必须 FAIL 且不得写出 `parsed_text.md` | **1 failed**：`test_mock_vlm_client_flags_mock_and_yields_no_text`（`:383` `assert result.text == ""`）。变异态管线实证 3 场景（`engine=vlm` 用配置构建的变异 mock / `auto` 走 vlm 回退 / 直接注入变异 mock）→ 全部 `UNSUPPORTED_WITH_HINT / SKIPPED`，`parsed_text.exists=False`，`parsed_root` 下 `parsed_text.md` 落盘总数 **0** | `54078c850ac595aa5033a6dfad68f68d`（= 变异前） | **53 passed / 2 skipped**（注入 DB env） |

变异前后 `git status --porcelain` 均为空，`grep -n "if ocr_result.mock:"` 回到 `919` 行 → **逐字还原、无残留改动**。结论：**变异必 FAIL、还原必 PASS，两个变异全部被捕获**，A015-06 的反假绿目标达成。

### 3. 真值表逐行实测明细（A015-03）

| plan §6 行 | 实测 decision | 实测 actual/profile | 附加断言 |
|---|---|---|---|
| auto·≥阈值·可用·* | PARSED | paddleocr / image_paddleocr_v1 | 引擎调用 1 次 |
| auto·<阈值·real | PARSED | vlm / image_vlm_v1 | paddleocr 调用 0 次 |
| auto·None·real | PARSED | vlm | 同上（D-3 fail-safe） |
| auto·<阈值·mock | UNSUPPORTED_WITH_HINT | — | `parsed_dir` 不存在 |
| auto·None·mock | UNSUPPORTED_WITH_HINT | — | `parsed_dir` 不存在 |
| auto·≥阈值·缺失·real | PARSED | vlm | `metadata.engine.fallback_reason=paddleocr_missing` |
| auto·≥阈值·缺失·mock | UNSUPPORTED_WITH_HINT | — | `parsed_dir` 不存在 |
| paddleocr·缺失·* | DEPENDENCY_MISSING | — | `status=FAILED`、`errors[].code=DEPENDENCY_MISSING`、零落盘、CLI exit 1 |
| paddleocr·可用·* | PARSED | paddleocr | 低质亦执行且 `warnings` 非空 |
| vlm·mock | UNSUPPORTED_WITH_HINT | — | 不调用引擎（路由层拦截） |
| vlm·real | PARSED | vlm / image_vlm_v1 | vlm 调用 1 次 |

### 4. 发现的问题

**D-1【阻塞 A015-12，非生产阻塞】无 cv2 时 015 测试 10 个失败，「CI 不装重依赖全绿」目标未达成。**
`requirements.txt:19-21` 明确把 `paddleocr`、`opencv-python-headless` 列为可选（注释掉）并声明「缺失 cv2 时自动降级 Pillow 梯度法，功能不中断」；plan §8.4 D-4 亦要求「CI 环境不装重依赖也必须全绿」。实测缺 cv2 时 **10 failed / 45 passed**，根因是测试把 cv2 路径写成了硬断言（`method == "laplacian"`、`applied_ops == ("deskew","denoise","binarize")`、`metadata.image_quality["method"] == "laplacian"`、`asset_files` 非空）。**实现本身不崩**（已实测：缺 cv2 时仍 `SUCCESS`、`method=pillow_gradient`、三件套齐全、3 个 op 诚实标 `opencv_missing`），故这是**测试移植性缺陷**而非实现缺陷。建议（不在本次 QA 内修改）：把上述断言改为「按 scorer 可用性分支」或给 cv2 路径用例加 `skipif`。

**D-2【非阻塞，真实风险】无 cv2 时 Pillow 回退阈值的区分度不足，会把清晰图判为低质。**
实测 900x300 单行文字清晰图：`pillow_gradient=9.91 < 阈值 20.0` → `auto` 路由判低质 →（015 唯一 VLM 是 mock）→ `UNSUPPORTED_WITH_HINT / SKIPPED`，`parsed_text.exists=False`，即**清晰图完全不被解析**。同图 `laplacian=2090.36`，余量 20 倍以上。清晰度区分度对比（blur 0→10）：laplacian `2090.36→0.48`（约 4300 倍），pillow `9.91→0.63`（约 15 倍）。→ 无 cv2 部署下阈值需重校准；因无真实语料，**无法**给出可用阈值（见 N-1）。

**D-3【非阻塞，口径偏差】A015-09 的「损坏图字节→FAILED」在默认配置下不成立。**
损坏 PNG 字节 + `auto` + 默认 mock VLM → `UNSUPPORTED_WITH_HINT / SKIPPED`（`no_trusted_engine`），不是 acceptance 写的 `FAILED`。`FAILED` 只在引擎抛异常（`ENGINE_ERROR`）或超时（`TIMEOUT`）或 `missing_original_bin` 时出现。批次隔离、`errors[]` 承载 sha256+message、FAILED 零产物三项语义均成立。若要求「损坏字节必为 FAILED」，需改 spec（把「无法解码」映射为 FAILED）或改断言口径 —— 属规格 vs 实现的口径分歧，非功能缺陷。

**D-4【非阻塞，口径偏差】A015-11 的 `source_vault_path` 记录的是 vault 路径，不是中文原始路径。**
`manifest.source_vault_path` 按 007 契约恒为 `…/by_hash/<sha>/original.bin` 形态；中文原始文件名/目录**不进入 015 任何产物**，而是保存在 001 的 `kb_file_instance.source_path`（DB）。本次验证采用 014 的既有口径（TC014-16：vault 路径含中文 → 报告 `ensure_ascii=False` 保留原文），该口径下 PASS。若验收本意是「015 产物内必须能追溯到中文原始文件名」，则当前实现不满足，属规格措辞问题。

**D-5【环境类，非缺陷】本机装了 paddleocr，2 个 `DEPENDENCY_MISSING` 用例默认 skip。**
默认跑法 `53 passed / 2 skipped` 会**静默跳过**缺依赖分支；只有用导入拦截（`QA_BLOCK=paddleocr`）才得到 `55 passed / 0 skipped`。建议 CI 增加一次「依赖缺失」矩阵跑法，否则 A015-03/A015-12 的依赖分支在生产 CI 中长期不可见。

**D-6【非阻塞，plan 未实现】plan §8.6 D-6 的 GIF 首帧策略未实现**：全仓 `gif_first_frame` **0 命中**。acceptance 未列该项，故不影响本次判定，仅登记为 plan/实现差距。

### 5. NOT_COVERED 清单（如实登记，不得掩盖）

| 编号 | 未覆盖内容 | 原因 |
|------|-----------|------|
| N-1 | 真实图像质量下的 OCR 命中率 / 预处理有效性（deskew·denoise·binarize 对真实票据是否有增益）/ 阈值在真实质量分布下的合理性 | 仓内真实图片语料 **0 张**（`find . -iname '*.png' -o -iname '*.jpg' …` = 0）；真实 DB 13 条内容中 `file_ext` 分布为 `.pdf 2 / .json 7 / .docx 1 / .md 1 / .csv 1 / .html 1`，**IMAGE 0 条**。全部验证只能基于 Pillow 现场合成的图像（D-2 的阈值问题正因此无法定量） |
| N-2 | 真实 PaddleOCR 推理链路（`PaddleOcrClient.recognize` → `_to_result` 对 2.x/3.x 嵌套结构的拍平、真实置信度统计） | 全部用例注入假引擎；本机虽装 `paddleocr 2.7.3`，但未跑真实模型推理（015 设计 O-3：真实引擎集成另开 Spec），`_to_result` 的 `pragma: no cover` 分支未覆盖 |
| N-3 | 真实 VLM 解析能力 | 015 只交付 `MockVlmClient`（`SUPPORTED_VLM_ADAPTERS=("mock",)`），真实 HTTP/SDK 客户端不存在，VLM 侧识别质量无法评估 |
| N-4 | CLI 级「批处理中出现单条 FAILED 仍 exit 0」端到端实测 | 真实 DB **无 IMAGE 候选**，无法在不写库的前提下构造含 FAILED 的 CLI 批次；仅以代码审查（`main.py:1189-1206`）＋service 层实测支撑 |
| N-5 | GIF 首帧路径（plan §8.6） | 未实现（D-6），无法验证 |

### 6. 最终 verdict

> **FAIL**（唯一阻塞项 = **A015-12 的「无 cv2 环境全绿」子项**；其余 11 条 PASS，A015-09 / A015-11 带非阻塞口径偏差记录，NOT_COVERED 5 项）

**判定说明**
- 引擎路由正确性门禁：**通过**（A015-01~05 全 PASS，真值表 11 组合逐行 + 纯函数逐行）。
- 反假绿门禁：**通过**（A015-06 两个变异均被捕获且实证「变异态确实会把伪造文本写进 `parsed_text.md` 且 manifest=SUCCESS」；A015-02/03/05/07/08/09 的否定断言全部实测为真）。
- 幂等与批处理门禁：**通过**（A015-07、A015-09）。
- 安全红线门禁：**通过**（A015-08 真实 DB 逐表行数不变 + 五棵 storage 树全等；A015-10 listing+sha256 全等；A015-11 中文来源）。
- 回归门禁：**部分通过** —— 全量 `437 passed / 4 failed / 2 skipped`，4 个 `--help` 失败在 015 父提交上逐字复现（既有基线，非 015 引入），静态审查四项全清；但 A015-12 的「无 cv2 全绿」子项实测 **10 failed**，故该条目判 FAIL。

**建议（交由 team-lead / 实现方决定，QA 未作任何修改）**
1. 修 10 个 cv2 硬断言（改为按 `image_quality.import_cv2()` 可用性分支或 `skipif`），使 A015-12 成立；这是本次唯一的阻塞项，改动只在 `tests/test_image_parser.py`。
2. 重校准 `quality.pillow_blur_threshold`（当前 20.0 实测把清晰图判 9.91）—— 需真实语料，建议并入 016 一起做（N-1）。
3. D-3 / D-4 属规格措辞，建议在 spec 侧确认口径后决定是改文档还是改实现。
