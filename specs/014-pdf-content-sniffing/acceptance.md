# Acceptance: PDF 内容嗅探路由修复（014）

> **Spec**：`specs/014-pdf-content-sniffing/`
> **Plan 对照**：`plan.md` §12 / §13
> **测试文件**：`backend/tests/test_pdf_sniffing.py`、`backend/tests/test_parser_router_v2.py`
> **反假绿原则**：以下用例均为**否定式可执行**——每个「必须产生 X」的断言都配有「改变输入后必须不再产生 X」或「变异后必须失败」的对照，防止空转断言。

---

## A014-01 数字版 PDF 判 PDF_DIGITAL

- 测试内用 pypdf 生成含充足文本层（总字符 ≥ 阈值）的 PDF fixture → 嗅探后 `route_type == PDF_DIGITAL`、`rule_name == "pdf_sniff_digital"`。
- **对照**：同一 fixture 用 `--routing-rules-version 004_mvp_v1` 路由，`rule_name == "ext_pdf_digital"`（证明 v2 分支确实走了嗅探而非旧路径）。

## A014-02 扫描版 PDF 必须判 PDF_SCANNED_OR_IMAGE（死枚举激活）

- 无文本层的空白页 PDF fixture → `route_type == PDF_SCANNED_OR_IMAGE`、`rule_name == "pdf_sniff_scanned"`。
- **否定断言**：`route_type != PDF_DIGITAL`；且报告中 `pdf_sniff.status == "TEXT_LAYER_ABSENT"`。
- **反假绿对照**：把 fixture 换成 digital PDF，同一断言路径必须得到 `PDF_DIGITAL`（证明断言非恒真）。

## A014-03 加密 PDF fail-closed

- `writer.encrypt(...)` 生成的加密 PDF → `route_type == PDF_SCANNED_OR_IMAGE`、`rule_name == "pdf_sniff_encrypted"`。
- **否定断言**：不得判 `PDF_DIGITAL`；不得抛未捕获异常；不得尝试任何口令（代码审查 + 测试中 pypdf `decrypt` 被 monkeypatch 为「被调用即 fail」）。

## A014-04 损坏 PDF fail-closed 且批处理不中断

- 截断/非法字节的 `.pdf` → `rule_name == "pdf_sniff_error"`、`route_type == PDF_SCANNED_OR_IMAGE`。
- 同批混入一个正常 `.txt`：`.txt` 正常路由，`errors` 不因此清空已有 decisions。
- **否定断言**：损坏 PDF 不得判 `PDF_DIGITAL`；批处理退出码 0。

## A014-05 空页 PDF 与 pypdf 缺失 fail-closed

- 0 页 PDF → `pdf_sniff_empty` → `PDF_SCANNED_OR_IMAGE`。
- monkeypatch 使 `import pypdf` 抛 `ImportError` → `pdf_sniff_unavailable` → `PDF_SCANNED_OR_IMAGE`，且 CLI 不崩溃。
- **否定断言**：两种情形均不得判 `PDF_DIGITAL`。

## A014-06 嗅探缓存命中与幂等

- 同一 sha256 连续两次 `route-parsers`（v2）：第一次 `from_cache=false`，第二次 `from_cache=true`；两次 `route_type` / `rule_name` / `reason` 逐字段相等。
- **否定断言**：第二次运行时 monkeypatch `sniff_pdf` 为「被调用即 fail」，测试仍须通过（证明确实走了缓存而非重算）。

## A014-07 缓存防投毒（变异复验）

- 手工篡改缓存文件：改 `sha256` 为别的值 / 改 `sniffer_version` 为旧版本 / 写入非法 JSON → 三种变异均须**弃用缓存并重算**（`from_cache=false`，结果与无缓存一致）。
- **否定断言**：篡改后的缓存内容绝不出现在报告 `pdf_sniff` 块中。

## A014-08 v1 回退逐字节复现

- `--routing-rules-version 004_mvp_v1`：扫描版 PDF fixture 仍判 `PDF_DIGITAL`、`rule_name == "ext_pdf_digital"`；报告 `routing_rules_version == "004_mvp_v1"`。
- **否定断言**：v1 模式下 monkeypatch `sniff_pdf` 为「被调用即 fail」，测试须通过（证明 v1 完全不嗅探、不读 bin）。

## A014-09 dry-run 零副作用

- `route-parsers --dry-run`（v2）：执行前后 `sniff_cache_root` 不存在或为空、`reports_root` 无新增文件、`raw_vault` listing 与 hash 不变。
- stdout 含本将产生的 decision 摘要。

## A014-10 原始文件与 raw_vault 保护

- route 前后：原始 fixture 文件 stat（size/mtime）+ sha256 不变；`raw_vault/**` 目录 listing 与 `original.bin` hash 不变。
- CLI 入口调用 `ensure_readonly()`（代码审查）。

## A014-11 中文路径兼容

- 使用 `backend/tests/fixtures/中文路径/` 风格的中文目录 + 中文文件名 PDF fixture 走全链路（scan → copy-to-vault → route v2）：路由正常、缓存文件名仍按 sha256（ASCII）、报告 `pdf_sniff` 证据完整。

## A014-12 回归与依赖边界

- `pytest -q tests/test_parser_router.py`（004 既有）全部通过、断言无需修改——证明 `match_route_type` v1 行为零回归。
- `pytest -q tests/test_mineru_pdf_parser.py` 通过；新增用例：`_build_plan` 对 scanned fixture 产出 `route_type=PDF_SCANNED_OR_IMAGE` 的 plan（**否定断言**：不得为 `PDF_DIGITAL`）。
- grep 审查：014 代码路径无 `magic-pdf` / `markitdown` / OCR 引擎调用；无 DB INSERT/UPDATE；无 `parsed/`、`curated/`、`quarantine/` 写入。
- 新增依赖仅 `pypdf`（requirements.txt diff 审查）。

---

## 验收门禁汇总

| 门禁 | 通过条件 |
|------|----------|
| 功能正确性 | A014-01 ~ A014-05 全过，含全部对照断言 |
| 缓存正确性 | A014-06、A014-07 全过（变异必须被捕获） |
| 兼容与安全 | A014-08 ~ A014-11 全过 |
| 回归 | A014-12 全过；001–013 全量 pytest 回归通过 |
| 反假绿 | 每个「必须产生」断言均有对照路径证明非空转（A014-02/06/07/08 内置） |

---

## 独立 QA 结论（2026-10-16）

> **QA session**：`qa-014-001`（独立 QA / ir-architect）· **verdict = PASS**
> **方式**：只读 + 瞬态变异（同一条命令内 `cp` 备份 → 改 → 跑 → `cp` 还原 → `md5sum`/`diff` 对账）
> **声明**：本小节为 **append-only** 追加，未修改上文任何既有验收条文（`grep -c 'A014-'` 改前 = 改后 = 17）。

### 1. 冻结制品 md5（QA 受测基线，实测）

| 制品 | md5 |
|------|-----|
| `backend/app/cli/main.py` | `15ee9fc5e7b1cf700dfe7c15dbc50f6f` |
| `backend/app/core/pdf_sniffing.py` | `f6799625eccfaac3e4d7078b0099cf03` |
| `backend/app/core/parser_routing.py` | `f41c6441c84b73c579bd55e11e4173bf` |
| `backend/app/services/parser_router.py` | `e25a20b2d6a09ac82fa4016a35ad0bf1` |
| `backend/app/services/mineru_pdf_parser.py` | `d8383fdedbfbe161963636b323d08f25` |
| `backend/tests/test_pdf_sniffing.py` | `cdd0bc72d1ccf9f24e7957c1d5e3c094` |
| `backend/tests/test_parser_router_v2.py` | `7db8dcb4ef395f12c19c848eb3b4f18f` |
| `config/pdf_sniffing.yaml` | `8a790c352a61f26b4288fb1b6134f5f0` |

（解释器 `/home/szf/anaconda3/bin/python` 3.11.7；本机 `python3` 为 3.10，不可用。）

### 2. 变异复验（证明断言非空转）

**M1 白名单证伪** — `pdf_sniffing.py:327` 由 `if result.status in CACHEABLE_SNIFF_STATUSES:` 改为 `if True:`
- 实测 `tests/test_pdf_sniffing.py`：`2 failed, 25 passed, 1 warning in 0.19s`
- 失败恰为 `test_dependency_missing_is_never_cached` + `test_sniff_error_is_never_cached`
- 还原 md5 = `f6799625eccfaac3e4d7078b0099cf03`，`diff` 与备份无差异

**M2 fail-closed 护栏证伪** — `parser_routing.py:203` 由 `SniffStatus.ENCRYPTED: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_encrypted")` 改为 `RouteType.PDF_DIGITAL`
- 实测 `tests/test_parser_router_v2.py`：collection 阶段 `1 error in 0.08s`；`app/core/parser_routing.py:217: in <module>` → `AssertionError: 只有 TEXT_LAYER_PRESENT 允许判 PDF_DIGITAL（fail-closed 不变式）`
- 还原 md5 = `f41c6441c84b73c579bd55e11e4173bf`，`diff` 与备份无差异
- 附注：被拦下的是**第三条等值断言**（`parser_routing.py:217`）；第二条 `<=` 子集断言按设计不误伤新增 rule_name → 粒度正确。

### 3. A014-01 ~ A014-12 逐条结论

| 条目 | 判定 | 证据 |
|------|------|------|
| A014-01 | PASS | `test_parser_router_v2.py::test_v2_digital_pdf_uses_sniff_rule_and_emits_evidence`(:300-313)；v1 对照 `::test_v1_route_is_byte_frozen_and_never_touches_vault:408`（rule_name=`ext_pdf_digital`）、`::test_v1_scanned_pdf_still_routes_digital:434`；闭集对撞 `test_pdf_sniffing.py::test_pdf_sniff_route_mapping_is_closed_set`(:580-599) |
| A014-02 | PASS | `::test_v2_scanned_pdf_routes_scanned_with_control`(:327-342：:328 否定断言、:331 `status=="TEXT_LAYER_ABSENT"`、:333-342 digital 反假绿对照)；`test_sniff_scanned_pdf_text_layer_absent`(:258-269) |
| A014-03 | PASS | `test_sniff_encrypted_pdf_fail_closed`(:324-338：:326 先证 fixture 真加密；:328-331 `PdfReader.decrypt` monkeypatch 为「被调用即 fail」→「不猜口令」为执行级证明)；rule_name 侧 `_NON_PRESENT_RULE`(:274) + `::test_router_fail_closed_for_every_non_present_status[ENCRYPTED]`(:552-556) |
| A014-04 | PASS（CLI 端到端受限，见 R-7） | `test_sniff_corrupted_pdf_fail_closed`(:341-348)；`::test_route_parsers_batch_continues_past_broken_pdf`(:586-642：4 类混批、`errors==[]`、`routed==4`、顺序保持、:624 `pdf_sniff_error`、:627 txt 不带 pdf_sniff) |
| A014-05 | PASS（CLI 端到端受限，见 R-7） | `test_sniff_empty_pdf_fail_closed`(:351-359)；`test_sniff_pypdf_missing_fail_closed`(:362-378，:377 `monkeypatch.undo()` 自愈对照)；router 侧 `[EMPTY_DOCUMENT]`/`[DEPENDENCY_MISSING]`(:527-556) |
| A014-06 | PASS | `test_sniff_cache_hit_is_idempotent`(:419-452：:438 计数 sniff、:443 `calls == []` 否定断言、:444-452 六字段相等)；`::test_route_parsers_second_run_hits_cache_and_is_field_identical`(:645-679：:661-664 sniff 被调用即 raise、:671 `from_cache is True`、:672-679 逐字段 + evidence 相等)；007 侧 `::test_mineru_build_plan_reuses_router_sniff_cache` |
| A014-07 | PASS（超集：4 变异 ⊃ 规格 3 变异） | `test_sniff_cache_poisoning_rejected[sha256\|sniffer_version\|cache_type\|invalid_json]`(:494-543：:524-526 伪装 PRESENT、:529 `load_sniff_cache is None`、:533-537 未采纳、:539-543 重算覆盖)；`::test_poisoned_cache_never_reaches_report` |
| A014-08 | PASS | `::test_v1_route_is_byte_frozen_and_never_touches_vault`(:381-413：双 boom monkeypatch（`resolve_pdf_sniff` + `build_vault_artifact_paths`）、:406-412 route/decision/rule_name/reason/future_parser_hint 与 `_V1_PDF_BASE`(:267-270) 全等、:413 `snapshot==before` 零写入)；`::test_v1_scanned_pdf_still_routes_digital`(:416-437)；stdout 版本串 `::test_cli_route_parsers_forwards_v2_options:977`(`Routing rules version: 004_mvp_v1`) |
| A014-09 | PASS | `::test_dry_run_zero_disk_writes`(:744-770：:755-759 dry_run/无 report_path/`reports_root` 不存在/无缓存文件/`snapshot()==before`；:764-770 非 dry-run 对照必落缓存+报告)；stdout 摘要 `::test_cli_route_parsers_forwards_v2_options:975-978` |
| A014-10 | PASS | `test_sniff_is_read_only`(:397-411：size/mtime_ns/sha256/目录 listing 四不变)；`::test_route_parsers_never_mutates_vault_artifacts`(:773-790)；`::test_service_refuses_writable_original_files`；`ensure_readonly` 调用点 `parser_router.py:128`、`mineru_pdf_parser.py:223`、定义 `config.py:112` |
| A014-11 | PASS（口径偏窄） | `::test_chinese_vault_path_routes_without_mangling`(:793-818：中文目录、缓存名纯 ASCII `{sha256}.json`、报告 `ensure_ascii=False`)。**注**：条文措辞为「scan → copy-to-vault → route 全链路」，实测仅覆盖 route 段（fixture 直接投放 vault）；scan/copy 属 001–004 职责，非 014 缺口 |
| A014-12 | PASS | 全量 `pytest -q tests/`：**4 failed / 253 passed / 129 skipped**，4 红全为 CLI `--help`（Typer/Click 版本不兼容，基线既有）；`test_parser_router.py` 零修改通过；007 六条 `_build_plan` 用例。grep 审查：014 路径无 `magic-pdf`/OCR 调用（4 处 `MARKITDOWN_FAMILY` 经 `git diff -U0` 证实为未改动上下文行）、无 DB 写、无 `parsed/`/`curated/`/`quarantine/` 写；`requirements.txt` diff = `+1/-0`，仅 `pypdf==6.20.1` |

### 4. 残留风险

- **① R-1（环境）** 无 MySQL，DB 集成链路未覆盖（129 项 skip 主要构成）。
- **② R-7（环境）** CLI `--help`/CliRunner 不可用 → A014-04「退出码 0」、A014-05「CLI 不崩溃」、A014-08/09 的 CLI 端到端，以 service 层不中断 + CLI 转发/stdout 用例等效替代。
- **③ R-8（已裁定非缺陷）** 007 侧 `sniff skipped` 文案被更具体的 `missing_original_bin` 覆盖（`mineru_pdf_parser.py:484`）；信息由 router `reason`(`test_parser_router_v2.py:513` 断言) + 007 `MISSING_ORIGINAL_BIN`(:342-347) + 008 `MISSING_RAW_VAULT_OBJECT` 三重承载，行为已被 `test_parser_router_v2.py:1111-1119` 固化。
- **④ R-9（登记不阻断）** `parser_routing.py:210-224` 三条模块级 `assert` 在 `python -O` 下被剥离（纵深防御护栏，**非规格要求**）。处方：抽 `_assert_route_map_invariants(route_map)` + 改 `raise RuntimeError` 便于直测；由 pilot-014 写入 tasks.md 遗留段。
- **补充 R-10（informational，已收口）** 「DEPENDENCY_MISSING 用例读路径混淆」经跨用例证据闭合（`test_content_derived_statuses_are_still_cached` 四参数：`cache_path_for(...).is_file()` + `load_sniff_cache(...)` 非 None + `cached.status is status`；另 `:428`/`:442`、`:540-542` 的 `from_cache is True`）→ 无混淆，无需修复。
- **补充 R-11（建议，非阻断）** v1 模式下 JSON 报告字段 `report["routing_rules_version"]` 未直接断言（仅 v2 有 `:633`/`:637`；v1 值仅经 stdout `:977`）。风险极低（`parser_router.py:143` 白名单校验 + `:146` 原值透传 + `:404` 原样写入），1 行可闭合。

### 5. N1 ~ N3（建议级，经 team-lead 独立复核，不影响 verdict）

- **N1（死赋值，成立）** `mineru_pdf_parser.py:450` 的 `reason = f"{reason}; {REASON_SNIFF_SKIPPED}"`：该 `reason` 仅被 `:471` 消费，而 `:470` 分支条件为 `decision != DECISION_ROUTE or route_type not in IN_SCOPE_ROUTE_TYPES`；D-3 路径（`:448-453`，`original_bin` 不可达）下 `route_type` 保持 `PDF_DIGITAL`（in-scope）→ `:470` 不可达 → 该赋值在 in-scope 路径上确为死赋值（且 `MineruParsePlan` 无 `reason` 字段，`:103-117`）。
- **N2（无条件覆写）** `:481-485` 以字面量 `skip_reason = "missing_original_bin"` 覆写前序更具体原因；对照 `:477` 使用具名常量 `SKIP_REASON_IDEMPOTENT`。行为已被测试固化（`:1119`），属命名/信息保真建议。
- **N3（重复与魔法串）** `REASON_SNIFF_SKIPPED` 的文案拼接在 `parser_router.py:295` 与 `mineru_pdf_parser.py:450` 重复；同一语义串 `"missing_original_bin"` 在 `mineru_pdf_parser.py:342`/`:484` 两处以字面量出现。建议收敛为单一构造点 + 具名常量。

### 6. verdict 与声明

```
verdict: PASS (fail-closed)
```

依据：fail-closed 不变式（仅 `TEXT_LAYER_PRESENT` 可判 `PDF_DIGITAL`）在「模块级护栏 + 路由闭集 + 六状态参数化」三处独立成立；M1/M2 两组证伪变异均被捕获且逐字节还原；A014-01~12 无 FAIL；N1~N3 与 R-8~R-11 均为建议级，不构成阻断。

声明：本次 QA 只读，唯一的文件写入即本条 append（未修改任何既有条文）；`backend/app/**`、`backend/tests/**`、`config/**` 全部 md5 与第 1 节受测基线一致。

---

### 7. 冻结后增量变更登记（team-lead，2026-10-17）

> **性质**：append-only 留痕。本节**不改写**第 1 节 QA 受测基线，也不推翻 `verdict: PASS` 的历史事实；但**受测 md5 已漂移**，后续 QA / 合并 / 017 引用须以本节现盘值为锚。
> **触发**：S1 期间处理「测试 DB 环境变量可配 + 失败信息可诊断」时发现——magic-pdf 在解析器内部抛异常时**仍以 exit 0 退出**，只把 traceback 打到 stdout，原消息 `magic-pdf output missing markdown content` 会把真实原因完全掩盖。

| 制品 | QA 受测基线（2026-10-16） | 现盘值（2026-10-17 现拼） | 判定 |
|---|---|---|---|
| `backend/app/services/mineru_pdf_parser.py` | `d8383fdedbfbe161963636b323d08f25` | `a82a3653ec7998d30eabfd16e99b0a9e` | **已变更（本节登记）** |
| `backend/app/cli/main.py` | `15ee9fc5e7b1cf700dfe7c15dbc50f6f` | `f864a1830b823605f9230631aef0ed4c` | 015 的 171/0 纯 append（015 `p2_db_review.md` 已登记），**非本次** |
| `core/pdf_sniffing.py` / `core/parser_routing.py` / `services/parser_router.py` / `test_pdf_sniffing.py` / `test_parser_router_v2.py` / `config/pdf_sniffing.yaml` | 见 §1 | 逐字不变 | 未变 |

**变更内容（3 处，均不触碰判定逻辑）**

1. 新增模块级 `_process_output_tail()`（`:212`）：把子进程 stdout/stderr 尾部（≤800 字符、单行化）附加到错误消息。
2. `DECISION_SUBPROCESS_ERROR` 消息（`:561-565`）：原来拼接 `stderr or stdout` 全文，改为统一 `returncode=N | output_tail=...`。
3. `DECISION_OUTPUT_CONTRACT_VIOLATION` 消息（`:606-610`）：`missing markdown content` 之后追加 `returncode` + `output_tail`。

**不变量（可据以判定 A014-01~12 不受影响）**：判定码闭集未变；`_clear_parsed_outputs` 三键 + `assets_dir` 语义未变（现 `:846-851`，调用点 `:530`）；fail-closed 不变式（仅 `TEXT_LAYER_PRESENT` 判 `PDF_DIGITAL`）未变；嗅探注入点与路由闭集未变。

**证据**

- 新增回归 `test_contract_violation_message_carries_magic_pdf_output`：断言 `returncode=0` 且真实异常串（`Unknown file suffix: .bin`）可见。
- 变异复验：摘掉尾部拼接 → 该用例 **FAIL**；逐字还原 → **PASS**（已用 md5 复核还原）。
- 014 基线复跑：`tests/test_pdf_sniffing.py` + `tests/test_parser_router_v2.py` = **61 passed**（27 + 34）。

**待办（不代签）**：请 ir-architect 按上表现盘 md5 重新锚定，并把复跑结论追加在本节之下。

#### 7.1 独立 QA 复核结论（qa-014-delta，2026-10-17 13:14 CST）

**范围**：仅复核第 7 节登记的增量变更。本节 append-only，未修改第 1~7 节任何既有行（已用「追加前后 `head -190 | md5sum` 相等」实测自证）。

**(1) 现拼 md5（`cd backend && md5sum ...`，以下全部为本人实测输出）**

| 制品 | 应然值 | 实测值 | 结论 |
|---|---|---|---|
| `app/services/mineru_pdf_parser.py` | `a82a3653ec7998d30eabfd16e99b0a9e` | `a82a3653ec7998d30eabfd16e99b0a9e` | 与第 7 节现盘锚一致 |
| `app/core/pdf_sniffing.py` | `f6799625eccfaac3e4d7078b0099cf03` | `f6799625eccfaac3e4d7078b0099cf03` | 未变 |
| `app/core/parser_routing.py` | `f41c6441c84b73c579bd55e11e4173bf` | `f41c6441c84b73c579bd55e11e4173bf` | 未变 |
| `app/services/parser_router.py` | `e25a20b2d6a09ac82fa4016a35ad0bf1` | `e25a20b2d6a09ac82fa4016a35ad0bf1` | 未变 |
| `tests/test_pdf_sniffing.py` | `cdd0bc72d1ccf9f24e7957c1d5e3c094` | `cdd0bc72d1ccf9f24e7957c1d5e3c094` | 未变 |
| `tests/test_parser_router_v2.py` | `7db8dcb4ef395f12c19c848eb3b4f18f` | `7db8dcb4ef395f12c19c848eb3b4f18f` | 未变 |
| `../config/pdf_sniffing.yaml` | `8a790c352a61f26b4288fb1b6134f5f0` | `8a790c352a61f26b4288fb1b6134f5f0` | 未变 |

被变更文件 `stat`：35364 B，mtime `2026-10-17 12:20:41 +0800`。

**(2) 变更面判定：只影响消息文本**

- 新符号引用面实测 `grep -rn "_process_output_tail"`：仅 3 条——定义 `:212`、调用 `:564`（`DECISION_SUBPROCESS_ERROR` 的 `message=` f-string 内）、调用 `:609`（`DECISION_OUTPUT_CONTRACT_VIOLATION` 的 `message=` f-string 内）。无任何判定条件、返回值或退出码消费该函数结果。
- 判定码闭集 `:48-54` 仍为 PARSED / ALREADY_SUCCESS / ROUTE_MISMATCH / TIMEOUT / SUBPROCESS_ERROR / OUTPUT_CONTRACT_VIOLATION / ASSET_INCOMPLETE，无新增/改名；两处分支的 `item.decision` 赋值（`:568`、`:614`）与 `code=` 取值（`:561`、`:606`）未变，分支条件仍是 `run_result.returncode != 0`（`:557`）与 `markdown_path is None or not markdown_path.is_file()`（`:602`），均早于消息构造，不依赖消息内容。
- 新函数无副作用：只读 `run_result.stdout/stderr/returncode`，无写盘/无状态变更；且入参已在 `_invoke_magic_pdf`（`:750-763`）归一化为 `str`（bytes 先解码、`or ""` 兜底），`tail[-limit:]`/`replace`/`!r` 不存在 `TypeError` 路径。
- **限制（如实声明）**：本机不存在基线 `d8383fde...` 的字节级副本（`git stash list` 为空、`/tmp`、`.ua` trash、pycache 均无可用快照，且该文件相对 HEAD 的 diff 混入了 014 自身的未提交改动），故「全局仅 3 处」无法用字节 diff 直接证明；上述判定依据为「引用面 + 判定码赋值点 + 分支条件」的结构性证据，并以第 (3)(4) 项行为证据交叉验证。若日后能取回基线副本，可补一次字节 diff 以彻底闭合。

**(3) 014 基线复跑（本人实测）**

- `PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q tests/test_pdf_sniffing.py tests/test_parser_router_v2.py` → **61 passed**（1 warning，0.59s）。
- 分文件：`test_pdf_sniffing.py` → **27 passed**；`test_parser_router_v2.py` → **34 passed**（合计与 61 自相符）。
- 附带：`tests/test_mineru_pdf_parser.py`（DB 环境变量按 `config/app.yaml` mysql 段注入，未回显密码）→ **33 passed**，被变更模块自身无回归。

**(4) 变异复验（证明新增断言非空转）**

- 备份 → 精确摘除 `:606-610` 的 `f"{_process_output_tail(run_result)}"`（保持合法 Python，`py_compile` 通过；变异态 md5 `7f6c53f10f2651e2f766fabb244ee584`）→ 跑 `test_contract_violation_message_carries_magic_pdf_output` → **1 failed**，失败点 `tests/test_mineru_pdf_parser.py:659`（`"Unknown file suffix: .bin" in error.message` 不成立，消息退化为纯 `magic-pdf output missing markdown content`）。
- 逐字还原 → 再跑 → **1 passed**；`md5sum` 回到 `a82a3653ec7998d30eabfd16e99b0a9e`，与变异前逐字节一致（已实测比对）。

**(5) verdict**

```
verdict: PASS WITH NOTES
```

独立结论：**判定逻辑未变**，本次 3 处变更确实只在错误消息层；新增用例具备可证伪性（变异必 FAIL、还原必 PASS），61 passed 成立。

Notes（不阻断，供后续引用）：

- N-a：`DECISION_SUBPROCESS_ERROR` 消息由「`stderr` 或 `stdout` 全文」改为「stdout+stderr 拼接后取末 800 字符」，当输出超 800 字符时早期信息会被截断——可诊断性权衡，不影响判定码与控制流。
- N-b：第 (2) 项「全局仅 3 处」的证明强度受限于无基线副本（见该节限制声明）；行为面证据充分，字节面证据缺失。
- N-c：其余 6 个冻结件 md5 与第 1 节受测基线逐字一致，`config/pdf_sniffing.yaml` 仍为 014 独享冻结态。
