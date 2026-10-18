# L37 验收记录（候选证据的畸形输入）

- 入口：`loop-entry/L37` = `33190d2`
- 代码：`e7ef086`
- **状态：`CODE_MERGED` + M1/M6 CLOSED（覆盖缺口）**

## 1. 先更正我自己的一个错误命名

我请 `qa-l22` 抽检 L35 时写的是"纠错声明（`correction_declaration`）"。**代码里没有这个字段。**

`a7fda18` 实际实现的是 **`candidate_evidence` 的作废归因**：

| 我写的 | 实际的 |
|---|---|
| `correction_declaration` | `CorrectionOutcome.stale_candidate_evidence / unattributable_evidence / evidence_checked` |
| "声明与实际改动不符必须拒" | `_affected_candidate_evidence()` 按 `locator.cell_id` / `locator.table_id` 归因 |

它 grep 全仓（`app/` `tests/` `contracts/` `specs/`）确认零命中。**我核过这条更正：确实零命中。**

"声明"是我对设计意图的比喻，但派单时把比喻当成了字段名。若照原样写进交付说明，读者会去找一个不存在的字段。

> 这不是笔误，是**用比喻代替了符号**。派单和证据里引用代码，必须引用能被 grep 到的名字。

## 2. 两条覆盖缺口（M1/M6）

| 变异 | 原实现行为 | 变异后 | 危害形态 |
|---|---|---|---|
| **M1** 删 `candidate_evidence_id` 必填 | `E_INPUT_INVALID` | 接受，`stale=(None,)` | **接口契约污染**：一旦有人按 id 列表作废，`None` 会被当成一个 id（当前无消费方，无写盘后果） |
| **M6** 删缺 `locator` 的归因分支 | `unattributable=('EV-NOLOC',)` | `unattributable=()` 且 `checked=True` | 输出与"这次更正不涉及任何证据"**完全一样**，却仍声称已检查 |

M6 是最该记的那条：它同时满足"没作废、没列出、但报告说查过了"——正是该函数 docstring 明令禁止的 *silently kept*。

**共同成因**：夹具 `_candidate()` **永远造出形状完整的候选**，所以两条防线在用例层面都不存在。补的不是功能，是畸形输入。

## 3. 补的两条断言

| 用例 | 断言 |
|---|---|
| `test_a_candidate_without_an_id_is_refused` | `E_INPUT_INVALID` + 消息含 `candidate_evidence_id` |
| `test_a_candidate_without_a_locator_is_reported_not_dropped` | `unattributable == ("EV-NOLOC",)`、`stale == ()`、`checked is True`、`stats` 三键一致 |

## 4. 变异验证（含一次假绿，如实记录）

| 变异 | md5 落盘 | 结果 |
|---|---|---|
| M1 | `00bfa67f…` | 1 failed |
| M6（首次串行） | `2d44babc…` | **1 passed ← 假绿** |
| M6（清 `__pycache__` 后重跑） | `2d44babc…` | 1 failed |
| M6（同株独立探针复核） | — | `unattr=()` 确认为变异态 |

**假绿的处置**：我没有把那次绿解释成"用例写错了"，也没有直接采信后来的红，而是**清掉 `__pycache__`、加 `PYTHONDONTWRITEBYTECODE=1` 重跑两遍**，两次都是红；再用直接探针在变异态上确认返回 `unattr=()`。

### 4.1 根因更正：不是字节码缓存

独立 QA 指出我把规则当成根因，机制上站不住：

- CPython 时间戳式 `.pyc` 的失效判据是**源文件 mtime（秒）+ size**，任一不符即重编译；
- M6 这个变异让源文件**减少 31 字节**，size 变了 → 缓存必然失效；
- 更硬的判据：**只要变异真的被加载，该用例不可能绿**（无 locator → 既不进 stale 也不进 unattributable，`assert () == ("EV-NOLOC",)` 必红）。

它绿，只能说明**那次跑的代码里没有这个变异**。所以根因更可能是"变异没落盘 / 跑到了另一份代码"——与 L35 那次脚本路径写错同族。

> **规则（保留，成本为零）**：变异验证清 `__pycache__` 或用 `PYTHONDONTWRITEBYTECODE=1`。
> **根因判断（更正）**：先证变异已落盘（`md5sum` + `git diff --stat`），再怀疑缓存。
> 字节码缓存的失效判据含 size，改变字节数的变异不会被旧 `.pyc` 命中。

这是本轮的**第四个**"验证动作本身出错"的实例（QA 的并行污染、我的脚本路径错、区间基准选错、这次的变异未生效），只是**不能把它归到缓存头上**。

## 5. QA 的两条观察（登记，不开 Loop）

| 编号 | 内容 | 处置 |
|---|---|---|
| `L37-OBS-01` | 重复 id 不合并：`[EV-DUP, EV-DUP]` → `stale=('EV-DUP','EV-DUP')` | 写明"不做去重，由调用方幂等"，不改 |
| `L37-OBS-02` | `[]` 与 `None` 的差别只由 `checked` 表达 | 契约写明：`None` = 未检查，`[]` = 检查过且为空 |

第二条值得单独说：如果有人把 `candidate_evidence` 的默认值写成 `[]`，`evidence_checked` 会被"洗"成 `True`——**空清单与未检查，读数上一模一样，但含义相反**。

## 6. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L37..HEAD` **恰好 1 个文件**（test_correction.py，+39） |
| B | **1427 passed / 131 skipped**（1425 + 2） |
| C | `passed=true` |

## 7. 边界

本轮只证明：**这两条防线可被观测到破坏，且破坏形态会被断言抓住**。不构成 L35 的可用性结论，也不覆盖 L35 的其它分支（如 table-scoped 归因的跨表情形由 M3 那条既有用例负责）。

## 8. 一处必须写进交付说明的能力边界：L35 的清单**没有消费方**

QA 在复核 M1 危害时顺手查了消费方，结论也要更正我自己上一轮的措辞：

| 我上一轮写的 | 事实 |
|---|---|
| "`mark_stale` 收到空 id → 错误指令" | `evidence/candidates.py:177` 的 `mark_stale(candidates, *, revoked_source_ids)` 吃的是**候选 dict 列表 + 源版本 id**，**不吃 candidate_evidence_id 列表** |
| — | 全仓**没有任何代码消费** `stale_candidate_evidence`，它只在 `correction.py` 产出、`CorrectionOutcome` 承载、`to_mapping()` 序列化 |

所以 M1 的准确定级是**接口契约污染**（将来若有人按 id 列表做 by-id 作废，`None` 会被当成一个 id），**当前没有任何写盘后果**。测试仍该有（钉的是"必须拒绝"），但危害描述必须下调。

> QA 的自评值得原样保留：**"我不该在没查消费方时就写'错误指令'。"**

### 8.1 这条边界决定"L35 已完成"怎么读

L35 完成的是 **"算得对、报得出"**，不是 **"已经作废了"**：

- 模块产出"应作废清单"，不写盘（设计如此，保持纯函数）；
- 仓内**没有 writer**，所以"作废真的发生"没有端到端证据。

这不是缺陷，是 L35 的声明范围。但交付说明若写"纠错后证据会被作废"，就超出了证据支持的范围——正确说法是"纠错后能算出并报出应作废的证据清单"。
