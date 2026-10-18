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
