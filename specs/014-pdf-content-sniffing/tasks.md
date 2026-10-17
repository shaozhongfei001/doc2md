# Tasks: PDF 内容嗅探路由修复（014）

> **Spec**：`specs/014-pdf-content-sniffing/`
> **分支**：`feature/014-pdf-content-sniffing`
> **Plan**：`plan.md`（TL G0 已落地；Owner 裁定 2026-10-16 全部 RESOLVED，见 `spec.md` §12）
> **Dev 必读顺序**：本文件 → `plan.md` → `spec.md` → `acceptance.md` → `test_cases.md`

---

## Dev 文件白名单（全局）

**允许修改**：

```text
backend/app/core/pdf_sniffing.py            # 新增
backend/app/core/parser_routing.py          # 仅 plan §7.2 范围
backend/app/services/parser_router.py       # 仅 plan §7.3 范围
backend/app/services/mineru_pdf_parser.py   # 仅 plan §7.4 的 _build_plan 注入
backend/app/cli/main.py                     # route-parsers 新增选项
backend/app/core/config.py                  # storage.sniff_cache_root 可选字段
config/pdf_sniffing.yaml                    # 新增
config/app.example.yaml                     # storage.sniff_cache_root
backend/requirements.txt                    # +pypdf（Owner O-2 已批准）
backend/tests/test_pdf_sniffing.py          # 新增
backend/tests/test_parser_router_v2.py      # 新增
specs/014-pdf-content-sniffing/tasks.md     # 勾选
```

**禁止修改**：

```text
backend/app/services/inventory_scanner.py
backend/app/services/file_content_vault.py
backend/app/services/duplicate_governance.py
backend/app/services/markitdown_parser.py
backend/app/services/parse_quality_checker.py            # 016 范围
backend/app/services/parse_quality_report_summarizer.py  # 016 范围
backend/app/models/**  sql/**  generated/**
raw_vault/**（真实产物）  parsed/**  curated/**  quarantine/**  data/**
config/parser_rules.yaml
specs/001..013/**  specs/015/**  specs/016/**  specs/017/**  specs/902/**
specs/014-pdf-content-sniffing/plan.md  # Dev 不改 Plan
```

---

## 全局硬约束（T001–T011 均适用）

1. 原始文件只读；raw_vault 只读引用；仅 `.pdf` 的 `original.bin` 允许只读打开做文本层嗅探（Owner O-3 限定开口）。
2. 不调用 MinerU / MarkItDown / 任何 OCR 引擎；不渲染 PDF 页面为图像。
3. 不写 DB、不改 schema、无 migration；不 upsert `kb_parse_job`、不 update `parse_status`。
4. 不写 `parsed/`、`curated/`、`quarantine/`。
5. fail-closed：嗅探任何不确定 → `PDF_SCANNED_OR_IMAGE`，绝不默认 `PDF_DIGITAL`（plan §8.2 D-2）。
6. 幂等；单条失败记 `errors[]` continue；dry-run 零磁盘写入。
7. `match_route_type()` 签名与 v1 行为零变更（004 既有测试必须零修改通过）。
8. 实现前必读 `plan.md` §13 TL 决策表（Q1–Q8）。
9. PDF fixture 一律由测试代码用 pypdf 现场生成，**不**提交二进制 fixture。

---

## T001 阅读 004/007 规格、代码与本 Spec

### 目标

理解 004 路由契约、007 `_build_plan` 现状与 014 边界修订范围。

### 验收标准

- [x] 已读 `backend/app/core/parser_routing.py`、`backend/app/services/parser_router.py`
- [x] 已读 `backend/app/services/mineru_pdf_parser.py`（重点 `_build_plan` 与 `IN_SCOPE_ROUTE_TYPES`）
- [x] 已读 `backend/tests/test_parser_router.py` 风格（fixture / cleanup / 中文路径）
- [x] 已读 `plan.md` 全文与 `acceptance.md` A014-01 ~ A014-12

---

## T002 测试先行：test_pdf_sniffing.py（红）

### 目标

先写嗅探纯函数与 fixture 生成器的失败测试：digital / scanned / encrypted / corrupted / empty 五类 fixture。

### 允许修改范围

- `backend/tests/test_pdf_sniffing.py`（新增）

### 验收标准

- [x] fixture 生成器可重复生成五类 PDF（pypdf 现场生成，无二进制入库）
- [x] 用例覆盖 `acceptance.md` A014-01 ~ A014-05 的断言（此时应全红）
- [x] 含否定断言（scanned 不得判 digital 等）

---

## T003 实现 core/pdf_sniffing.py

### 目标

`sniff_pdf` / `load_sniff_cache` / `save_sniff_cache` 与 `PdfSniffResult` / `SniffStatus`，按 `plan.md` §7.1。

### 验收标准

- [x] T002 测试转绿
- [x] pypdf 缺失 / 加密 / 损坏 / 空页全部 fail-closed，不抛未捕获异常
- [x] 缓存读取三重校验（cache_type / sha256 / sniffer_version）全部实现
- [x] 阈值来自 `PdfSniffConfig`，冻结值：100 / 20 / 20（Owner O-5 FROZEN）

---

## T004 parser_routing.py 演进

### 目标

`ROUTING_RULES_VERSION="004_mvp_v2"`、`ROUTING_RULES_VERSION_V1`、`PDF_SNIFF_RULE_NAMES`、`route_pdf_with_sniff()`，按 `plan.md` §7.2。

### 验收标准

- [x] `match_route_type()` 零 diff 行为（004 既有单测原样通过）
- [x] `route_pdf_with_sniff` 六状态映射与 `spec.md` §6 一致，hint 均 `MINERU_FAMILY`
- [x] （建议项1，TL 认可）import 期三条运行时护栏：映射覆盖全部 `SniffStatus`、`rule_name ⊆ PDF_SNIFF_RULE_NAMES`、仅 `TEXT_LAYER_PRESENT` 可判 `PDF_DIGITAL`；变异 `ENCRYPTED → PDF_DIGITAL` 后 import 即 `AssertionError`

---

## T005 ParserRouterService 集成 + dry-run + v1 回退

### 目标

`route_parsers(..., rules_version, use_sniff_cache, dry_run)`；PDF 分支 v2 嗅探管线；报告 `routing_rules_version` 与 `pdf_sniff` 证据块。

### 验收标准

- [x] v2 下 PDF 决策含 `pdf_sniff` 块；非 PDF decision 不含该键
- [x] `--routing-rules-version 004_mvp_v1` 完全不嗅探（A014-08：monkeypatch sniff 即 fail 仍通过）
- [x] dry-run 不写缓存/报告（A014-09）
- [x] vault bin 缺失回退 v1 判定 + WARNING（plan §8.3 D-3）；另补 `test_skip_reason_is_visible_in_route_report` 验报告层 `reason` 可读性（TL [2'] 义务 b）

---

## T006 007 `_build_plan` 嗅探注入

### 目标

按 `plan.md` §7.4 注入同一嗅探管线；`IN_SCOPE_ROUTE_TYPES` 不动。

### 验收标准

- [x] scanned fixture 经 `_build_plan` 产出 `route_type=PDF_SCANNED_OR_IMAGE`（否定断言：不得为 `PDF_DIGITAL`）
- [x] `test_mineru_pdf_parser.py` 既有用例零修改通过
- [x] 另补：`test_mineru_in_scope_route_types_unchanged`（Q8 不动 `IN_SCOPE_ROUTE_TYPES`）、digital 反假绿对照、router 缓存被 007 复用（monkeypatch `sniff_pdf` 即 fail 仍通过）、bin 缺失不嗅探且不写缓存、dry-run 不写缓存

---

## T007 CLI、配置与依赖

### 目标

`route-parsers` 新增 `--routing-rules-version` / `--no-sniff-cache` / `--dry-run`；`config.py` 增 `sniff_cache_root`；新增 `config/pdf_sniffing.yaml`；requirements +pypdf。

### 验收标准

- [x] `--help` 输出三新选项；Rich 汇总追加 PDF sniffed 行（`--help` 断言受环境 Typer/Click 基线故障限制，改为读 Typer 注册声明 + 直接调用回调双重验证）
- [x] `app.example.yaml` 与 `pdf_sniffing.yaml` 默认值与冻结阈值一致
- [x] requirements diff 仅 `pypdf` 一行

---

## T008 幂等与缓存测试（test_parser_router_v2.py）

### 目标

A014-06 / A014-07：缓存命中幂等、三种缓存投毒变异全部被拒。

### 验收标准

- [x] 第二次 route `from_cache=true` 且决策逐字段一致；monkeypatch sniff_pdf 为 fail 仍通过
- [x] 篡改 sha256 / sniffer_version / 非法 JSON 三种变异均触发重算，脏缓存内容不出现在报告
- [x] 另补（TL [MUST-FIX]，判据 1-4 全过）：`DEPENDENCY_MISSING` / `SNIFF_ERROR` 两类**永不落盘**；白名单为闭集；四类内容派生态正对照仍落盘且可命中；变异（去掉白名单）→ 2 用例 FAIL，恢复后 md5 逐字节一致

---

## T009 红线验证测试

### 目标

A014-10 / A014-11：原始文件与 raw_vault stat/hash/listing 不变；中文路径全链路。

### 验收标准

- [x] 原始 fixture 与 vault bin 前后 hash 一致
- [x] 中文目录 + 中文文件名 PDF 全链路通过，缓存文件名按 sha256（ASCII）

---

## T010 回归与边界审查

### 目标

004/007 既有测试零修改通过；grep 边界审查。

### 验收标准

- [x] `pytest -q tests/`：**4 failed, 253 passed, 129 skipped**（原记 247 为 T006 补测**前**旧值，已更正为 253 = 247 + 6）；4 红 = `test_evidence_chain.py::test_cli_help_documents_contract` / `test_markitdown_parser.py::test_cli_parse_markitdown_help` / `test_parser_router.py::test_cli_route_parsers_help` / `test_search_service.py::test_cli_help_documents_contract`，全为环境性 `--help` 基线（Typer/Click `make_metavar() missing ctx`；`specs/013-streamlit-admin/p4_fix_click_typer_report.md:14` 已记改动前即为「288 passed, 4 failed」），已用 `git show HEAD:backend/app/cli/main.py` 复跑同批用例对照证明与 014 无关 → **014 零新增失败**
- [x] 无 `magic-pdf` / `markitdown` 调用；无 DB 写；无 parsed/curated/quarantine 写入（grep 实测：`pdf_sniffing.py` 无 session/commit/parsed_root/curated_root/quarantine_root；`parser_router.py` 无 commit/add/flush/delete；`cli/main.py` 命中项全部为**既有** `parse-markitdown` / `parse-mineru` 命令）
- [x] 001–013 全量回归通过（同上，除环境基线 4 红外无新增失败）

---

## T011 验收与 STOP

### 目标

Dev 完成后 STOP → DB Review → QA → E2E → TL Final Review。

### 禁止事项

- Dev **不得**自我宣布 A014-01 ~ A014-12 通过；**不得**写 handoff；**不得** merge main

### 验收标准

- [x] Dev 输出：修改文件清单、pytest 命令、CLI 命令、遗留问题 → 已交付 `DEV_SELF_CHECK`（发 `team-lead`，含 7 项未覆盖项/已知风险 R-1~R-8）
- [x] STOP → 下游门禁（**等待 TL 派独立 QA**；Dev 不自我宣布 A014-01 ~ A014-12 通过） → 2026-10-16 QA-VERDICT: **PASS**（独立 QA = ir-architect；A014-01~12 全 PASS；M1 白名单变异 → 恰 2 failed、M2 守卫变异 → import AssertionError，两项均已恢复原 md5）

---

## 任务进度总览

| Task | 说明 | 状态 |
|------|------|------|
| T001 | 阅读 004/007 与规格 | [x] |
| T002 | 测试先行（嗅探红测） | [x] |
| T003 | core/pdf_sniffing.py | [x] |
| T004 | parser_routing.py 演进 | [x] |
| T005 | router service 集成 + dry-run + v1 回退 | [x] |
| T006 | 007 _build_plan 注入 | [x] |
| T007 | CLI / 配置 / requirements | [x] |
| T008 | 幂等与缓存防投毒 | [x] |
| T009 | 红线验证 | [x] |
| T010 | 回归与边界审查 | [x] |
| T011 | 验收与 STOP | [x] PASS（QA = ir-architect，2026-10-16） |

---

## Dev 执行记录（pilot-014，2026-10-16）

**修改文件清单**（均在白名单内）：

| 文件 | 动作 | 规模 |
|------|------|------|
| `backend/app/core/pdf_sniffing.py` | 新增 | 335 行 |
| `backend/app/core/parser_routing.py` | 改（plan §7.2 + 建议项1 护栏） | +87/-1 |
| `backend/app/services/parser_router.py` | 改（plan §7.3） | +149/-7 |
| `backend/app/services/mineru_pdf_parser.py` | 改（plan §7.4） | +31/-3 |
| `backend/app/cli/main.py` | 改（T007） | +42/-0（纯新增） |
| `backend/app/core/config.py` | 改 | +8/-0 |
| `config/pdf_sniffing.yaml` | 新增 | 冻结阈值 100/20/20，本批次未改动 |
| `config/app.example.yaml` | 改 | +1/-0 |
| `backend/requirements.txt` | 改 | +1/-0 |
| `backend/tests/test_pdf_sniffing.py` | 新增 | 719 行 |
| `backend/tests/test_parser_router_v2.py` | 新增 | 1129 行 |

**pytest 命令**：

```bash
cd backend && PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q tests/
# 4 failed(环境基线 --help), 253 passed, 129 skipped
PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q tests/test_pdf_sniffing.py        # 27 passed
PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q tests/test_parser_router_v2.py    # 34 passed
```

**CLI 命令**：

```bash
python -m app.cli.main route-parsers --routing-rules-version 004_mvp_v2 --sniff-cache
python -m app.cli.main route-parsers --routing-rules-version 004_mvp_v1 --no-sniff-cache --dry-run
python -m app.cli.main route-parsers --routing-rules-version 004_mvp_v9   # → ERROR + exit 1
```

**遗留问题**：见 `DEV_SELF_CHECK` R-1（MySQL 真实集成未覆盖）/ R-3（D-3 未嗅探仍报 PDF_DIGITAL，规格原判）/ R-7（CLI 三选项未做 CliRunner 真实解析，受 `--help` 基线限制）/ R-8（007 bin 缺失时 `skip_reason` 被 `missing_original_bin` 覆盖，`sniff skipped` 文案不显现——信息由更具体的字符串承载）/ **R-9**（低，QA 2026-10-16 登记、本轮不改：`parser_routing.py:211/214/217` 三条模块级 `assert` 在 `python -O` / `PYTHONOPTIMIZE=1` 下被整体剥离 → 护栏 fail-open；处方=抽 `_assert_route_map_invariants(route_map)` + 改 `raise RuntimeError(...)`（消息逐字保留）；护栏非规格要求且 CI 非 `-O`，**不阻断 014**）/ **R-10**（提示，QA 2026-10-16 判为**非缺陷**、无需补测：`test_dependency_missing_is_never_cached` 的「未命中」单独看无法排除「读路径失效」混淆，该因素已由 `test_pdf_sniffing.py:691-719` 四参数正对照（`cached.status is status`）与 `:442`/`:542` 的 `from_cache is True` 跨用例闭合）。/ **R-11**（建议，QA 2026-10-16 登记、**非阻断**：v1 模式下 JSON 报告字段 `report["routing_rules_version"]` 未直接断言（仅 v2 有 `test_parser_router_v2.py:633`/`:637`；v1 值仅经 stdout `:977`）。风险极低（`parser_router.py:143` 白名单校验 + `:146` 原值透传 + `:404` 原样写入），1 行可闭合。）

> 注：R-9 / R-10 / R-11 属**遗留问题**条目（非 tasks 勾选项），不影响 `- [x]` 计数；014 代码维持冻结。

---

**Tasks 结束** — Dev 从 T001 开始，严格在白名单内实现。
