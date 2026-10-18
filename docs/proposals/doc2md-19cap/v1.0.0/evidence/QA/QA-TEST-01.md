# QA-TEST-01 记录（单独立项）

- 来源：独立 QA（`qa-mutation`）在 8 组变异抽检中提出的 M3 观察
- 立项：Owner 2026-10-18（**不并入 L09，不占用 018**）
- 分支：`qa/test-01` → 合并 `main` = `f5618fe`
- 范围：**仅一个测试文件**，未改任何产品代码

## 1. 问题

`test_missing_ack_keeps_local_ready` 传入 `acks={}`。在"跳过守卫"变异下，
代码先执行 `acks[name]`（因为守卫被跳过）抛 `KeyError`，测试虽然变红，但
**不是它自己的断言失败**——即该断言层在变异世界里从未被验证过。

## 2. 处置

新增 `test_present_but_unfinished_ack_does_not_activate`：`acks` 中**保留 key**，
状态为 `IN_PROGRESS`，使守卫成为唯一可能的拒绝点。

| 状态 | 结果 |
|---|---|
| 正常代码 | **passed**（`E_CONSUMER_NOT_CONNECTED`，`stale_or_wrong_generation == ["search-index"]`） |
| 守卫改 `if False:` | **FAILED: DID NOT RAISE**（干净，正是断言层在起作用） |
| 对照：旧用例同变异 | FAILED: `KeyError`（脏） |

## 3. 边界

- 未修改 `release/switch.py`，`activate` 的行为一字未变；
- L08 的功能判定与 A1/B/C 结论**不变**：这只提升了一个测试的失败形态质量；
- 全量 **1304 passed / 133 skipped**（1303 + 1 新增）。

## 4. 为什么单独立项而不是顺手改

L08 已封版。就地改它的测试会让"L08 已通过"这一结论失去可追溯性。
单独立项 + 独立分支 + 独立记录，使"测试质量改进"与"L08 验收结论"两件事互不覆盖。
