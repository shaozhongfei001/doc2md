# L46 验收记录（清单接线 + 可恢复作废）

- 入口：`loop-entry/L46`
- 代码：`6b60683`
- **状态：`CODE_MERGED`；`L40-LIM-01`（未接线）关闭，`L40-LIM-03`（不可撤销）关闭**

## 1. 接线：让 L35 的清单真的驱动 L40

| 之前 | 现在 |
|---|---|
| `apply_correction` 产出 `stale_candidate_evidence`，无消费方 | `revoke_for_correction(outcome, candidates, ...)` 直接消费它 |
| 调用方要自己抄 id 列表 | 抄写这一步不存在了，也就没有抄错的可能 |

两条拒绝，都是防"看起来完成了"：

| 情形 | 处理 | 为什么 |
|---|---|---|
| `stale` 为空 | 拒绝 | 订正说没有要作废的，仍调用 writer 说明上游有问题，不该静默成功 |
| `unattributable` 非空 | **默认拒绝** | 只作废"能归因的"，却报告"全部处理"，是把部分成功说成全部成功 |

第二条留了显式出口（`require_attributable=False`），但那份清单会写进 audit 的
`unattributable_not_revoked`——**放过要留痕**。

## 2. 可恢复：不猜原状态

作废时记录 `previous_review_state` / `previous_review_decision_ref`，恢复时照抄回去。

| 情形 | 处理 |
|---|---|
| 有条目记录 | 恢复到 `ACCEPTED`（原值）而不是 `UNREVIEWED` |
| 无记录（历史数据） | **拒绝**：没有可信的"作废前"可回，猜一个等于伪造历史 |
| 条目不是 `STALE` | 拒绝：没有作废可撤 |

往返 `revoke → restore → revoke` 稳定，第二次作废记录的仍是 `ACCEPTED`。

## 3. 用例（8 条新增，`test_revocation.py` 共 17 passed）

| 用例 | 断言 |
|---|---|
| `test_the_correction_list_drives_the_writer` | 两条 stale 都被作废，`audit.source == "correction"` |
| `test_an_empty_correction_list_is_refused` | 空清单 `E_INPUT_INVALID` |
| `test_unattributable_evidence_blocks_revocation_by_default` | `E_REVIEW_REQUIRED`，消息列出 id |
| `test_unattributable_can_be_accepted_explicitly_and_is_recorded` | 显式放过 + audit 记录 |
| `test_restore_returns_to_the_recorded_state_not_to_unreviewed` | 回 `ACCEPTED` + `RV-OLD`，撤销字段被清除 |
| `test_restore_refuses_when_no_prior_state_was_recorded` | 拒绝，不猜 |
| `test_restore_refuses_a_candidate_that_was_never_revoked` | 拒绝 |
| `test_revoke_restore_revoke_round_trip_is_stable` | 往返后仍记 `ACCEPTED` |

## 4. 变异验证

| 变异 | 结果 |
|---|---|
| 恢复硬编码 `UNREVIEWED` | 1 failed（回不到 `ACCEPTED`） |
| 忽略 `unattributable` 直接作废 | 1 failed（默认拒绝失效） |

还原后 md5 回 `b2c525aa…`，17 passed。

## 5. 现在的表述边界

可以说：

> 订正产生的"应作废清单"可以直接交给执行器作废，带决策/执行人/时间/原因；作废可撤销，撤销也留痕。

**不能说**：

- "纠错后自动作废"——仍需显式调用（这是 Owner D2 的"人工触发"）；
- "作废已持久化到存储"——模块不落盘，持久化仍在调用方（`L46-LIM-01`）；
- "并发安全"——两人同时操作同一批仍未定义（`L40-LIM-02`）。

## 6. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L46..HEAD` **2 个文件**（执行器 + 测试） |
| B | **1461 passed / 131 skipped**（1453 + 8） |
| C | `passed=true` |

## 7. 遗留

| 编号 | 内容 |
|---|---|
| `L46-LIM-01` | 未落盘：registry 的持久化仍由调用方负责 |
| `L46-LIM-02` | 恢复的授权规则未定：撤销作废是否与原作废同级审批，仍是 Owner 的事 |
| `L46-LIM-03` | 未处理"作废后又被别人改过"的竞态（需要版本号或 CAS） |
