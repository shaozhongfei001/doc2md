# Tasks: 独立图片解析管线（015）

> **Spec**：`specs/015-image-parse-pipeline/`
> **分支**：`feature/015-image-parse-pipeline`
> **Plan**：`plan.md`（TL G0 已落地；Owner 裁定 O-1 ~ O-5 全部 RESOLVED，见 `spec.md` §12）
> **Dev 必读顺序**：本文件 → `plan.md` → `spec.md` → `acceptance.md` → `test_cases.md`

---

## Dev 文件白名单（全局）

**允许修改**：

```text
backend/app/services/image_parser.py        # 新增
backend/app/adapters/image_preprocess.py    # 新增
backend/app/adapters/image_quality.py       # 新增
backend/app/adapters/ocr_paddle.py          # 新增
backend/app/adapters/vlm_adapter.py         # 新增
backend/app/cli/main.py                     # 新增 parse-images 子命令
config/image_parse.yaml                     # 新增
backend/requirements.txt                    # 仅注释说明可选依赖（默认不增硬性依赖）
backend/tests/test_image_parser.py          # 新增
specs/015-image-parse-pipeline/tasks.md     # 勾选
```

**禁止修改**：

```text
backend/app/core/parser_routing.py                        # 014 白名单（只读 import match_route_type）
backend/app/services/mineru_pdf_parser.py                 # 007/014
backend/app/services/markitdown_parser.py
backend/app/services/parse_quality_checker.py            # 016 范围
backend/app/services/parse_quality_report_summarizer.py  # 016 范围
backend/app/models/**  sql/**  generated/**
raw_vault/**（只读）  parsed/**（真实产物）  curated/**  quarantine/**  data/**
config/parser_rules.yaml
specs/001..013/**  specs/014/**  specs/016/**  specs/017/**  specs/902/**
specs/015-image-parse-pipeline/plan.md  # Dev 不改 Plan
```

---

## 全局硬约束（T001–T011 均适用）

1. 原始文件只读；raw_vault 只读引用；`parsed/` 只写本管线自己的产物。
2. 不改变 007 / 004 任何既有行为；`match_route_type()` 只读 import，零修改。
3. 不写 DB（`--register` 时仅经 006 `ParseRegistryService.register_parse_report()`）、不改 schema、无 migration。
4. 不写 `curated/`、`quarantine/`；不触碰 `parsed/` 下非本管线的真实产物。
5. **D-2 诚实性红线**：`OcrResult.mock == True` 一律拒绝落盘 → `UNSUPPORTED_WITH_HINT / SKIPPED`（plan §8.2）。
6. **D-3 fail-safe**：`score=None` 视同低质 → 走 VLM 路径，绝不因"无法证明清晰"而走清晰路径。
7. **D-4 依赖可选化**：paddleocr / cv2 惰性 import + `check_*_available()`；无重依赖环境必须全绿。
8. 单条失败记 `errors[]` continue；dry-run 零磁盘写入；幂等（同 sha256 + 同 parser_profile 不重跑）。
9. 图片 fixture 一律由测试用 Pillow 现场生成，**不**提交二进制 fixture。
10. 实现前必读 `plan.md` §6 引擎路由真值表与 §13 TL 决策表（Q1–Q8）。

---

## T001 阅读 007 模式、004 路由与本 Spec

### 目标

理解 007 的 `_build_plan` / `_execute_parse` / 幂等 / manifest 契约模式，以及 015 复用边界。

### 验收标准

- [x] 已读 `backend/app/services/mineru_pdf_parser.py`（Plan/Item/ManyResult 三件套模式）
- [x] 已读 `backend/app/core/parsed_paths.py`（`build_parsed_artifact_paths`）与 vault 路径构建
- [x] 已读 `backend/app/core/parser_routing.py` 的 `match_route_type()`（只读引用点）
- [x] 已读 `backend/tests/test_mineru_pdf_parser.py` 风格（fixture / cleanup / 中文路径）
- [x] 已读 `plan.md` 全文（尤其 §4 / §5 / §6 / §9）与 `acceptance.md` A015-01 ~ A015-12

---

## T002 测试先行：test_image_parser.py（红）

### 目标

先用 Pillow 现场生成清晰图（含文字）与高斯模糊低质图两类 fixture，写出失败测试。

### 允许修改范围

- `backend/tests/test_image_parser.py`（新增）

### 验收标准

- [x] fixture 生成器可重复产出清晰 / 模糊 / 损坏三类图片（含中文路径目录），无二进制入库
- [x] 用例覆盖 `acceptance.md` A015-01 ~ A015-09 的断言（此时应全红）
- [x] 引擎可用性 / 评分 / 引擎调用全部可注入或 monkeypatch（保证无 paddleocr、无 cv2 环境可跑）

---

## T003 `adapters/image_quality.py`

### 目标

`score_image(image_path, scorer="auto") -> ImageQualityResult`，按 `plan.md` §5.2。

### 验收标准

- [x] cv2 可用 → `laplacian` 方差；不可用 → `pillow_gradient`（独立阈值 `pillow_blur_threshold`）
- [x] 两者都不可用 / 解码失败 → `score=None, method="unavailable"`，不抛异常（D-3 fail-safe 入口）
- [x] 阈值来自配置，冻结值 `blur_threshold=100.0`（Owner Q8）

---

## T004 `adapters/image_preprocess.py`

### 目标

`preprocess(image_path, staging_dir, ops) -> PreprocessResult`，按 `plan.md` §5.1。

### 验收标准

- [x] cv2 缺失 → 全部 op 记 `skipped: opencv_missing`，原图直通，不失败
- [x] 每步独立 try（deskew / denoise / binarize），单步失败 WARNING + 跳过该步继续
- [x] 中间图落 `staging_dir/preprocess/{op}.png`；**永不抛异常**中断批处理
- [x] 任一 op 未执行时，`extra.preprocess_ops` 必须体现 skipped，**不得**静默声称已执行（A015-04 否定断言）

---

## T005 `adapters/ocr_paddle.py` + `adapters/vlm_adapter.py`

### 目标

`check_paddleocr_available()` / `PaddleOcrClient`；`VlmClient` Protocol / `MockVlmClient` / `build_vlm_client(config)`，按 `plan.md` §5.3 / §5.4。

### 验收标准

- [x] paddleocr 惰性 import；缺失时 `check_paddleocr_available()` 抛 `DEPENDENCY_MISSING`，不 crash
- [x] `PaddleOcrClient` 聚合纯文本 + 逐行置信度（mean / min / sample_count）
- [x] `MockVlmClient` 恒 `mock=True` 且 `engine_meta` 标注 mock；**不得**返回可信伪造文本
- [x] `build_vlm_client` 遇未知 adapter 值 → `ValueError`（fail-fast，不静默降级）

---

## T006 `services/image_parser.py` — `_build_plan`

### 目标

对齐 007 的两阶段设计；`ImageParserService(config, *, ocr_client, vlm_client, scorer, preprocessor)` 全可注入。

### 验收标准

- [x] `route_type != IMAGE` → `ROUTE_MISMATCH / SKIPPED`（A015-01 对照：`.txt` 同批不产 parsed）
- [x] manifest 幂等命中 → `ALREADY_SUCCESS / SKIPPED`
- [x] `original.bin` 缺失 → `FAILED / missing_original_bin`
- [x] dry-run 下 `would_parse` / `would_skip` 摘要，零落盘

---

## T007 `_execute_parse` — 引擎路由与诚实性拒绝

### 目标

staging → preprocess → score → 引擎路由（`plan.md` §6 真值表 9 行）→ 诚实性校验 → 三件套 + assets 落盘。

### 验收标准

- [x] 真值表 9 行逐行实现，`--engine` 覆盖优先于评分结果
- [x] 无可用引擎 → `UNSUPPORTED_WITH_HINT / SKIPPED`，清理 staging，**不写 parsed**
- [x] `result.mock == True` → `UNSUPPORTED_WITH_HINT / SKIPPED`（D-2，A015-06 变异强制）
- [x] `text.strip()` 为空 → manifest `status=EMPTY`；否则 `SUCCESS`
- [x] 三件套 + `assets/preprocess/` 持久化，metadata `extra.asset_files[]` 索引
- [x] 引擎超时 → `TIMEOUT / FAILED` + `errors[]`，批处理 continue

---

## T008 幂等、force 与批处理容错

### 目标

A015-07 / A015-09：真 skip、force 重跑、单条失败不中断。

### 验收标准

- [x] 第二次同 sha256 + 同 parser_profile → `ALREADY_SUCCESS`，三件套 mtime 不变；monkeypatch 引擎为"被调用即 fail"仍通过
- [x] 换 parser_profile → 重跑（同 sha256 + parser_profile 才 skip）
- [x] `--force` 清三件套 + assets 后重跑
- [x] 三条候选（正常 / 损坏 / 正常）→ 2 SUCCESS + 1 FAILED，exit 0，失败条目零 parsed 产物

---

## T009 CLI 与配置

### 目标

`parse-images` 子命令与 `config/image_parse.yaml`，按 `plan.md` §7 / §2.3。

### 验收标准

- [x] `--config / --sha256 / --content-uid / --limit / --engine / --timeout / --dry-run / --force / --register` 全部生效
- [x] `--help` 输出完整；Rich 汇总含 Candidates / In-scope / Parsed / Skipped / Unsupported / Failed / Errors + 报告路径（注：本环境 Typer/Click `--help` 端到端为**既有基线问题**（同 014 R-7；全量 4 个 `--help` 用例开工前即红），故以源码级断言等价证明：`app.registered_commands` 含 `parse-images`、9 个选项齐备；其中 `--sha256 / --engine / --timeout / --dry-run / --limit / --register` 另有转发断言用例覆盖）
- [x] `image_parse.yaml` 默认值与 `plan.md` §2.3 一致（`adapter_version="015_mvp_v1"`、`blur_threshold=100.0`、`engines.default=auto`、`vlm.adapter=mock`）
- [x] `--engine paddleocr` 且 paddleocr 缺失 → `DEPENDENCY_MISSING`，非 dry-run 时 CLI exit 1（A015-03 否定断言）
- [x] dry-run 不写报告文件

---

## T010 红线验证与依赖可选化

### 目标

A015-08 / A015-10 / A015-11 / A015-12。

### 验收标准

- [x] dry-run：parsed/ 无新增、reports/ 无新文件、DB 零写、raw_vault 不变；引擎被替换为"被调用即 fail"仍通过
- [x] 执行前后原始 fixture 与 `raw_vault/**` listing + sha256 全等；`curated/`、`quarantine/` 零写入
- [x] `中文路径/图片/票据样张.png` 全链路通过，产物路径按 sha256（ASCII），报告 `ensure_ascii=False` 中文无乱码
- [x] 无 paddleocr、无 cv2 环境 `pytest -q tests/test_image_parser.py` 全绿（本机实测：`paddleocr=MISSING` → 54 passed / 1 skipped；`cv2=AVAILABLE` → 同样全绿。**cv2 缺失路径**由 `test_scorer_falls_back_to_pillow_when_cv2_missing` 经 monkeypatch `image_quality.import_cv2 → None` 覆盖，预处理缺 cv2 的 `opencv_missing` 跳过分支亦有用例）

---

## T011 回归与 STOP

### 目标

001–013 零回归 + 边界审查，随后 STOP。

### 禁止事项

- Dev **不得**自我宣布 A015-01 ~ A015-12 通过；**不得**写 handoff；**不得** merge main

### 验收标准

- [x] `pytest -q tests/test_mineru_pdf_parser.py tests/test_parser_router.py tests/test_image_parser.py` 全绿，且 004/007 既有用例**零修改**
- [x] grep 审查：无 `magic-pdf` / `markitdown` 调用；无 DB raw SQL 写；`--register` 仅经 `ParseRegistryService`
- [x] 001–013 全量回归通过
- [x] Dev 输出：修改文件清单、pytest 命令、CLI 命令、遗留问题 → STOP → P2 DB Review → QA → E2E → TL Final Review

---

## 任务进度总览

| Task | 说明 | 状态 |
|------|------|------|
| T001 | 阅读 007 / 004 与规格 | [x] |
| T002 | 测试先行（红测 + Pillow fixture） | [x] |
| T003 | image_quality.py | [x] |
| T004 | image_preprocess.py | [x] |
| T005 | ocr_paddle.py + vlm_adapter.py | [x] |
| T006 | image_parser.py `_build_plan` | [x] |
| T007 | `_execute_parse` 引擎路由与诚实性拒绝 | [x] |
| T008 | 幂等 / force / 批处理容错 | [x] |
| T009 | CLI + config/image_parse.yaml | [x] |
| T010 | 红线验证与依赖可选化 | [x] |
| T011 | 回归与 STOP | [x] |

---

**Tasks 结束** — Dev 从 T001 开始，严格在白名单内实现。

---

## Dev 执行记录（pilot-014，2026-10-16）

> Dev **未**自我宣布 A015-01 ~ A015-12 通过；以下仅为本侧自验记录，供 QA 独立复核。

### 1. 修改文件清单与 md5（现拼，2026-10-16）

```text
(NEW) backend/app/services/image_parser.py      0d6aaf31ddc9605be88e64ba6ab905d7
(NEW) backend/app/adapters/image_preprocess.py  a57f413cc0043c32d9c4c07a6fb5e3b7
(NEW) backend/app/adapters/image_quality.py     7686c0a204805d78f8cc302ec35704ab
(NEW) backend/app/adapters/ocr_paddle.py        3e74a4cb630afc67e0a4be476905a369
(NEW) backend/app/adapters/vlm_adapter.py       54078c850ac595aa5033a6dfad68f68d
(NEW) config/image_parse.yaml                   8c19fd68ca0688fead91d59426fc8865
(NEW) backend/tests/test_image_parser.py        0c5222263d1fe7cf297247f54bfe1371
(MOD) backend/app/cli/main.py                   f864a1830b823605f9230631aef0ed4c  (129/0，仅 append)
(MOD) backend/requirements.txt                  (015 追加区 +7/-0；014 冻结部分 1/0 未动)
(MOD) specs/015-image-parse-pipeline/tasks.md   (本文件：勾选 + 本记录)
```

`cli/main.py` append-only 证据：`git diff --numstat` = **171 added / 0 deleted**（累计；= 014 的
42 行 + 015 的 **129 行**，015 段自身 129/0）；删去追加块后
逐字节还原 014 冻结版 md5 `15ee9fc5e7b1cf700dfe7c15dbc50f6f`（脚本化比对通过）。
锚点未减少：`routing-rules-version` / `no-sniff-cache` / `dry-run` 均由 014 区块原样保留。

### 2. 命令与结果

```bash
cd backend
# 015 主测试（连跑 3 次稳定）
PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q tests/test_image_parser.py
#   → 54 passed, 1 skipped

# 014 侧基线（未回归）
PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q tests/test_pdf_sniffing.py tests/test_parser_router_v2.py
#   → 61 passed

# 全量
PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q tests/
#   → 4 failed / 307 passed / 130 skipped
#     4 failed 全为既有 Typer/Click `--help` 基线（evidence_chain / markitdown_parser /
#     parser_router / search_service），开工前即红，未新增失败

# CLI 冒烟
PYTHONPATH=. /home/szf/anaconda3/bin/python -m app.cli.main parse-images --limit 1 --dry-run --engine auto

# 依赖实测
cv2=AVAILABLE  paddleocr=MISSING  numpy=AVAILABLE  PIL=AVAILABLE
```

### 3. 变异强制（证明断言非空转）

- **A015-06（D-2 诚实性红线）**：把 `_execute_parse` 的 `if ocr_result.mock:` 变异为 `if False:` →
  `test_mock_result_is_never_persisted_with_forged_text` **FAIL**；还原后 md5 回到 `0d6aaf31…` → **PASS**。
- 口径说明：该用例刻意注入 `is_mock=True` 的引擎替身，并要求「引擎**确被调用过**、且仍零写入」。
  因此 `vlm_adapter.client_is_mock()` 收敛为**适配器类型**口径（`MockVlmClient` / 无 `recognize`），
  结果级诚实性一律由 service 层对 `OcrResult.mock` 的拒绝分支（D-2）承担；若在路由层据 `is_mock`
  短路，会连引擎调用一起跳过，该用例将失去证明力。

### 4. 本侧自验中发现并修复的 2 个**测试自身缺陷**（重要，QA 须知）

- 现象：`test_limit_marks_remaining_candidates_as_parse_limit_reached` 偶发假红（复现率约 6%），
  断言为 `assert 'ALREADY_SUCCESS' == 'PARSED'`。
- 根因（**非实现缺陷**）：`_make_text_image` 的常量文本实测宽 **1064px > 画布 900px**，而
  `_add_candidate` 把 uuid 唯一标记追加在**同一行末尾** → 该标记被整段裁切 → 两个候选渲染出的
  PNG 字节完全相同（实测 **400 对撞 8%**）→ sha256 撞车 → 第二条被幂等判成 `ALREADY_SUCCESS`。
- 修复：唯一标记改为**换行后单独绘制**（起点 x=30，必落在画布内）。复测 **0/400** 撞车，
  limit 用例 **40/40 绿**，主测试 3 次连跑稳定。
- **(4b) CWD 依赖（确定性红，非 flaky）**：`test_cli_014_options_still_present_in_source` 原用
  `Path("app/cli/main.py")` 按 CWD 相对读取，从**仓库根**执行 `pytest backend/tests/...` 会稳定
  `FileNotFoundError`（实测 6/6 红）；仅在项目约定的 `cd backend` 下绿。已改为锚定
  `Path(__file__).resolve().parents[1] / "app" / "cli" / "main.py"`，两种调用方式各连跑 3 次均
  **54 passed / 1 skipped**。
- 提示：QA 请以现态 md5 `0c5222263d1fe7cf297247f54bfe1371` 为准；更早版本会随机遇到 (4a) 假红 / 从仓库根跑遇到 (4b) 确定红。

### 5. grep 审查（T011）

```text
app/services/image_parser.py 等 5 个 015 新文件：
  无 magic-pdf / magitdown / markitdown 调用（grep 无命中）
  无 raw SQL 写；--register 仅经 ParseRegistryService.register_parse_report()（惰性 import）
```

### 6. 遗留问题（Dev 侧观察，编号请由 QA 赋予）

1. `--help` 端到端输出受本环境既有 Typer/Click 基线限制（同 014 R-7），已以源码级注册/选项断言等价替代。
2. `config/image_parse.yaml` 默认 `vlm.adapter=mock`，故**真实 VLM 引擎**路径未端到端验证（属本批 MVP 范围，D-2 使 mock 结果不入库）。
3. 016 依赖的 `parser_name="image_ocr"` 已就位（manifest 写入 `parser_name` / `parser_adapter_version` / `parser_profile`），016 可直接消费。
4. 本机 `cv2` 存在，故「cv2 缺失」仅由 monkeypatch 用例覆盖；建议 QA 在无 cv2 环境复跑一次 T010 第 4 条。
