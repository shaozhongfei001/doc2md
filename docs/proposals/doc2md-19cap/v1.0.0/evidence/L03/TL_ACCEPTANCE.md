# L03 · TL 验收记录（P1）

| 项目 | 值 |
|---|---|
| Loop | L03 原生 Word 与表格结构（T301–T304） |
| 派工单 | `docs/proposals/doc2md-19cap/v1.0.0/dispatch/STAGE-P1-L03-派工单.md` |
| L03 入口 commit | `a783dc8c1c7f762f3f7d0998b87a42c34418b9e8`（错误码映射之后） |
| L03 代码 commit | `07da949`（T301/T302）+ `a51a8d6`（T303） |
| 执行角色 | `dev-word`（T301/T302）、`dev-grid`（T303）；TL 独立验收 |
| 状态 | DEV_SELF_TEST=DONE、**TL_ACCEPTANCE=ACCEPTED**、DB_REVIEW=N/A（本 Loop 无 DB 接触）、INDEPENDENT_QA=**PENDING**（`qa-verifier` 在飞） |

## 1. 交付物与指纹

| 文件 | 行 | md5(12) |
|---|---|---|
| `word/__init__.py` | 67 | `3e3eec202725` |
| `word/package.py` | 292 | `b40be8355af0` |
| `word/final_view.py` | 357 | `a006588e47db` |
| `word/native_tables.py` | 534 | `b8b26b918575` |
| `tests/test_word_package.py` | 289 | `b9ac6a642046` |
| `tests/test_word_final_view.py` | 278 | `0606b5a2dd17` |
| `tests/test_word_tables.py` | 584 | `b95a04834971` |
| `tables/grid.py`（T303） | 637 | `1d734961e8e3` |
| `tests/test_table_grid.py`（T303） | 602 | `297389bcb0e7` |

## 2. 数字（全部取自提交树实测）

| # | 命令 | 结果 |
|---|---|---|
| 1 | 全量 `pytest -q` | **869 passed / 0 failed / 133 skipped** |
| 2 | 3 个 word 测试文件 | **93 passed**（26 + 19 + 48） |
| 3 | 去掉本批 3 文件 | 776 passed（776+93=869，**零回归**） |
| 4 | `check_baseline --allow-index-append` | `passed=true` |
| 5 | `check_loop_diff --loop L03 --base a783dc8` | `passed=true`，7 文件，`errors=[]` |

## 3. TL 的独立验收动作（不采信自述）

1. **复跑**：两条命令各自跑出与 DEV 一致的数字（见 §2）。
2. **跨模块集成**：自建 3×3 含 `gridSpan` 的 DOCX → `extract_tables` → JSON 落盘口径 → `contracts.Table` 校验 **1/1 accepted**（14 键、slots 正确）。
3. **探针**（针对 DEV 回报与代码不一致的那一条）：注入 `w:hMerge="restart"` → `REJECTED code=E_DOCX_MERGE_INVALID exit=4`。**代码是正确的，DEV 的缺口措辞过时。**
4. **变异抽检 3/3 CAPTURED**（在隔离副本 `/tmp/l03verify` 上做，避免干扰在飞成员）：

| ID | 变异点 | 目标测试 | 变异后 | 还原后 | md5 |
|---|---|---|---|---|---|
| W1 | 关掉 `_reject_hmerge` | `test_word_tables.py` | FAIL | PASS | `b8b26b918575` 复原 |
| W2 | 关掉 vMerge continue 的 anchor 校验 | `test_word_tables.py` | FAIL | PASS | `b8b26b918575` 复原 |
| W3 | 关掉结构修订检测 | `test_word_final_view.py` | FAIL | PASS | `a006588e47db` 复原 |
| G1–G3（T303） | span 校验 / 重叠守卫 / 有洞无证据 | `test_table_grid.py` | FAIL | PASS | `1d734961e8e3` 复原 |

DEV 自报 19 组（T301/302）与 7 组（T303）变异，TL 未重复其全部，只做抽检；QA 将补作者未覆盖的分支。

## 4. 过程中发现并纠正的两处偏差（留痕）

| # | 偏差 | 处置 |
|---|---|---|
| D-1 | DEV 回报 §3 写"旧式 `w:hMerge` 未覆盖，按普通 tc 处理"，与代码不符（已有 `_reject_hmerge` + `E_DOCX_MERGE_INVALID`） | 以代码 + TL 探针为准；要求 DEV 后续以最终代码重扫缺口清单 |
| D-2 | DEV 回报的测试数字（79 passed、`test_word_tables.py` md5 `73bcdc4daf37` / 481 行）是**提交前快照**；提交时该文件已 584 行 / 93 例 | 提交信息中的数字改为提交树实测 **93 / 869**，并在提交信息里写明"DEV 回报为提交前快照，旧数字不作结论"；新增纪律：DEV 回报须给"最终确认时刻 + 各文件 md5"，若仍在改须先发"别验收"消息 |

D-2 是本阶段最值得记录的一条：**如果直接采信回报数字写进提交信息，就会在历史里留下一个假数字**。这是本项目反复出现的"假绿"同型问题，只是这次发生在流程层而非断言层。

## 5. 拒收 / 返工记录

| 轮次 | 问题 | 处置 |
|---|---|---|
| R1 | `w:hMerge` 被静默当普通格（更宽、且不产生洞，grid 也拦不住） | 要求 fail-closed；DEV 已实现并有 `restart`/`continue` 用例；TL 探针确认 |
| R2 | `native_tables` 未复用 `tables/grid.py`，矩阵/重叠/slot 规则出现两份实现 | 要求单一事实源；DEV 已改为 import 并使用 `build_slots`/`validate_grid`/`to_contract_table`，自建矩阵删除；新增 `test_grid_module_is_the_only_matrix_builder` |
| R3（未采纳 DEV 建议） | DEV 建议放宽"行尾未描述列且无 gridAfter 按未解释洞拒绝" | **不放宽**：契约要求未解释洞必须拒绝，严格于 Word 宽容渲染是正确方向 |

## 6. 债务与缺口（不影响本批验收）

| ID | 内容 | 处置 |
|---|---|---|
| L03-DEBT-01 | `to_contract_table` 的 `n_rows/n_cols` 由最外 anchor 推导；`w:tblGrid` 更宽时（末尾 `gridAfter`、整行省略）会缩表。DEV 在 `native_tables` 侧按 `validated` 回填尺寸，含 2 例 + M20 变异 | 正解是让 `grid.to_contract_table` 接受显式 `n_rows/n_cols`（属 T303 文件，本 Loop 不改）。登记债务，L05 会再动 `tables/`，届时由 TL 统一收口；`qa-verifier` 本轮核对其回填是否真生效 |
| L03-GAP-01 | 页眉/页脚中的表格未覆盖（不在 T301/T302 范围） | 登记，后续按需另立任务 |
| L03-GAP-02 | 页面/`bbox` 一律 `null`，只有 XML 定位 | 符合 D-08（不制造页码），非缺陷 |
| L03-GAP-03 | `max_grid_slots_per_table` 等上限未在 grid 内实施 | 属配置/上层策略，登记 |

## 7. 越界与红线核对

- 7 个新增文件全在 L03 白名单；`check_loop_diff` `errors=[]`；**未扩白名单**。
- 未改 `backend/app/extensions/{__init__,contracts,identity,runner,cli}.py`、`io/**`、`sources/**`、`sql/**`、`specs/**`、`config/**`、`backend/requirements.txt`、任何既有测试。
- `word/package.py` 只以只读方式打开原件；有独立测试断言读前读后 `sha256`/`mtime`/`size` 与目录清单不变（含 `chmod 444` 样本）。
- 本 Loop 无 DB 接触，`DB_REVIEW` 记 N/A（P0 的 DB 审核已覆盖共享层）。

## 8. 结论与下一步

`TL_ACCEPTANCE = ACCEPTED`（以第 3 节实测和 3/3 抽检变异为前提）。
`INDEPENDENT_QA` 由 `qa-verifier` 对 `07da949` 执行，覆盖：A 复跑比对、B 复核 L01 第 2 轮三项修复、C 补 5 组作者未变异分支、D 核实 L03-DEBT-01 回填真实性、E 假成功/错误码、F 报告一致性。
QA 若判 RETURN，本记录结论自动失效，按返回项返工。

---

## 9. 附记：R1/R2 最终态的落位确认（TL 追加）

DEV 在 `07da949` 之后回报"R1 已实现、R2 走方案 A 并补差分测试"，同时指出其内容**已在 `07da949` 内**。TL 逐字节核对：

| 文件 | HEAD blob md5(12) | 工作区 md5(12) | 一致 |
|---|---|---|---|
| `word/native_tables.py` | `b8b26b918575` | `b8b26b918575` | 是 |
| `tests/test_word_tables.py` | `b95a04834971` | `b95a04834971` | 是 |

`git status` 为空、无未跟踪文件 → **07da949 已包含 R1/R2 最终内容，不需要额外补提交**
（本记录即在为此留痕；提交信息已写明 R1/R2 已落地）。§3 表格、§2 数字均无需修改。

### 9.1 差分测试已抽检（非采信）

| 用例 | 位置 | 断言 |
|---|---|---|
| `test_payload_is_identical_to_grid_to_contract_table[7 shapes]` | `test_word_tables.py:449` | 7 种形状下 `cells` 顺序 / `slots` / `omitted_slots` / `n_rows` / `n_cols` 与 `grid.to_contract_table` 逐键相等 |
| `test_the_only_divergence_is_a_grid_wider_than_its_anchors` | `:467` | 唯一分歧被单独钉住：`to_contract_table` 推 (1,1)、交付 (1,3)，其余 9 键相等 |
| `test_grid_module_is_the_only_matrix_builder` | `:412` | spy `build_slots` 必须被调用（单一事实源） |

TL 实跑：`pytest -k "identical or divergence or only_matrix_builder or hmerge"` → **16 passed / 0 failed**。

### 9.2 更正一处计数

§3 写"DEV 自报 19 组（T301/302）"，DEV 本轮更新为 **30 组累计有效变异全部 CAPTURED**
（原 19 + R2 阶段 4 + R1 阶段 3 + 复跑 4），每次 `restored=True`。TL 侧为**抽检**（W1–W3、G1–G3 共 6 组），
不复跑其全部；差额由 `qa-verifier` 按 §8 的 C 项补覆盖。

### 9.3 一处口径需要写清（避免被误读为"两份实现"）

R2 之后 `native_tables` 仍保留两项 grid 不可见的 XML 事实判断：
`w:vMerge` adjacency（continue 必须连到上一行同列区间的 restart anchor）、
"行描述列数 < `w:tblGrid` 且无 `gridAfter` ⇒ 未解释尾列"。
这两项**不重算矩阵**，属结构事实，与 grid 的职责边界清晰；已在 §6 `L03-DEBT-01` 记录唯一残留的尺寸回填。
