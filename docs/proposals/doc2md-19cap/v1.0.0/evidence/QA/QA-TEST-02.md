# QA-TEST-02 记录（L09 补充覆盖）

- 来源：独立 QA（`qa-l09`）变异抽检 **M7 = NOT_CAPTURED**
- 分支：`qa/test-02` → 合并 `main` = `f07aaab`
- 范围：**仅测试文件**，未改任何产品代码

## 1. 缺口（QA 实测，非推测）

`test_separator_entries_are_not_content` 的夹具是 `note("-1", "", type=separator)` —— **文本为空**，
所以关掉 `SKIP_TYPES` 检查后，它掉进 `if not text:` 的 `EMPTY_NOTE` 分支，断言仍绿。
且原用例只断言 notes 文本列表，**没有任何用例断言 `issues` 为空**。

真实风险：带文本的 separator（Word `continuationNotice` / 部分生成器会产出）会直接泄漏成
`FN--1 / "SEPARATOR-LINE" / NOTE_WITHOUT_REFERENCE`，全量用例无一捕获。

## 2. 补的两条

| 用例 | 断言 | 为什么能抓住 |
|---|---|---|
| `test_empty_separator_produces_no_issue` | `result.issues == []` | 区分"按类型跳过"与"被空文本分支顺带过滤" |
| `test_separator_with_text_is_still_not_content` | notes 不含它 / issues 为空 / candidates 不含该文本 | 带文本时无法靠空文本分支掩盖 |

## 3. 验证

| 状态 | 结果 |
|---|---|
| 正常 | `11 passed` |
| 注入 M7 变异（`if False: continue`） | **2 failed**（两条新用例，干净变红） |
| 全量 | **1306 passed / 133 skipped**（1304 + 2） |

## 4. 边界

未改 `notes_extract.py`；separator 在**产品代码里本来就正确跳过**，缺的是"测试能否证明它"。
因此本项不改变 `L09` 的能力判定，只补齐证据强度。

## 5. 第三方复核（独立 QA 于收工前主动补做）

`qa-l09` 在收工前对 `f07aaab` 做了一次**独立复核**，用全新副本（`/tmp/qa-l09b`），结论：**修复成立，不是假绿**。

| 复核项 | 实测 |
|---|---|
| 提交范围 | `git show --stat f07aaab`：只动 `test_producer_notes.py`（+39），**产品代码 0 改动** |
| 未变异 | `test_producer_notes.py` **11 passed**（原 9 + 新增 2，无回归） |
| 注入原 M7 变异 | **2 failed / 9 passed**，红的正是新增两条：`test_empty_separator_produces_no_issue`、`test_separator_with_text_is_still_not_content` |
| 还原 | `git diff` 为空 |

它认可的两点值得记下来：

1. **断言选得对**：空 separator 断言 `issues == []`，带文本 separator 断言 notes 不含它 + `issues == []` + candidates 不含该文本。三条合起来才把"按类型跳过"与"被空文本分支过滤"真正分开。
2. **产品代码零改动说明性质**：原来不是功能缺失，而是**可证性缺失**。改动面控制在测试侧，风险为零。

> 这一条把"作者自述已修复"升级为"第三方在同一变异下复现了红/绿两种状态"，
> 与 L02 的 MB1/MB2 第三方复跑是同一处置：**自述不算证据，能在别人手里复现才算**。
