# L42 验收记录（整批拒绝与去空格的两条盲区）

- 入口：`loop-entry/L42` = `5e6ce75`
- 代码：`174fd01`（**只改测试**，产品代码零改动）
- **状态：`CODE_MERGED` + `L42-FIND-01/02` 关闭**

## 1. `L42-FIND-01`：一条审计性命中守卫，守护者是"意外崩溃"

`qa-l41` 在做 M9 变异（把整批校验挪到复制之后 + 非法条目 `continue`）时发现：

- 变红的是 `ValueError: dictionary update sequence element #0`——因为用例往注册表里塞的是**字符串**，
  `dict(item)` 先崩了；
- 真正决定性的断言 `broken[0]["review_state"] == "UNREVIEWED"`（"拒绝批次时不得改动任何条目"）
  **在这个变异下根本执行不到**。

它用同一变异态跑了一个探针：输入 `[{"EV-1", UNREVIEWED}, {"no_id": 1}]`（同样非法，但是 mapping，不会崩）
→ 变异体返回 `revoked=('EV-1',)`，`EV-1` 已变 `STALE`：**半作废且静默成功**。

> 也就是说：`revocation.py` docstring 写的"半作废比不动更糟"，当时**没有任何用例在守**，
> 守着的只是一个"非法条目恰好是字符串就会崩"的巧合。

## 2. 补的断言

| 用例 | 断言 |
|---|---|
| `test_a_mapping_without_an_id_is_rejected_whole` | `{"no_id": 1}` → `E_INPUT_INVALID`，消息含 `candidate_evidence_id`，且 `EV-1` 仍 `UNREVIEWED` |
| `test_allow_all_still_strips_the_reviewer` | `ALLOW_ALL` 下 `check("  E12345  ") == "E12345"` |

第二条对应 `L42-FIND-02`：去空格此前**只在 `RESTRICTED` 路径**被断言，而当前生产策略正是 `ALLOW_ALL`——
最常走的那条分支反而没测。

## 3. 变异重放（按变异体形状分别记录）

我先只把"缺 id"校验的 `raise` 换成 `continue`，看到的是：

```text
revocation.py:125: KeyError: 'candidate_evidence_id'
```

一度据此写成"断言仍被崩溃遮蔽"。**这个结论是错的**——QA 指出那是 M9N（更弱、也更不真实的变异体），
它保留了 `by_id = {str(item["candidate_evidence_id"]): ...}` 的直接取值，所以先崩。

按 QA 原来的 M9 形状（校验整段后移到复制之后、非法条目 `continue`、id 映射在带 `continue` 的循环里构建）重放：

```text
>       with pytest.raises(ExtensionError) as excinfo:
E       Failed: DID NOT RAISE <class 'app.extensions.ExtensionError'>
tests/extensions/test_revocation.py:127
```

**新用例红在 `DID NOT RAISE`，即"整批拒绝整个消失了"——语义级捕获，不是崩溃兜住。**

| 变异体 | 红点 | 性质 |
|---|---|---|
| M9（校验后移且不崩） | `test_revocation.py:127` `DID NOT RAISE` | **语义级**：保证本身被观测到破坏 |
| M9N（仅删校验、保留直接 `[]`） | `revocation.py:125` `KeyError` | 崩溃级：实现先崩，断言未执行 |

两者都判红，但只有 M9 证明"这条断言有牙"。**"输入零改动"（`:130`）守护的是另一类变异体**
（先写坏输入再抛错），不承担本变异的红点。

> 这是我第二次在同一个 Loop 里"把较弱变异体的现象当成结论"。
> 规则：**报告红点前先确认变异体形状与对方一致**，否则描述的是另一个实验。

## 4. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L42..HEAD` **2 个测试文件**，+32 行 |
| B | **1451 passed / 131 skipped**（1449 + 2） |
| C | `passed=true` |

## 5. 九变异的独立结论（`qa-l41`）

| 项 | 结果 |
|---|---|
| L38 几何绑定（3） | 全捕获：阈值恒通过、不附加 polygon、默认变乐观 |
| L39 策略（3） | 全捕获；M4 一次打红 3 条（决策两条 + 补丁一条）→ 策略在三条调用路径上都真被约束 |
| L40 执行器（3） | 全捕获（M9 的判定见第 3 节） |
| 合计 | **9/9 CAPTURED**，主仓只读，副本已还原，md5 全部回基线 |

它另外记录了一次过程失误：M2/M5 首次用编辑器写入后文件被回退（md5 未变、diff 为空、测试仍绿），
改为脚本原子替换 + "注入后立刻 md5 + diff 双证"后重做。

> 这与我在 L35/L38/L40 的三次失误同族：**动作没发生，却被当成结果解释。**

## 6. 仍然薄弱的地方（登记，不声称已解决）

| 编号 | 内容 |
|---|---|
| `L42-OBS-01` | 整批拒绝的**红点落点取决于变异体形状**：M9 → 语义级（`:127` `DID NOT RAISE`）；M9N → 崩溃级（`revocation.py:125` `KeyError`）。两种形状均判红，用例有效；"输入零改动"（`:130`）只对"先写坏再抛错"类变异体生效 |
| `L42-OBS-02` | 跨进程并发作废语义仍未定义（`L40-LIM-02`） |

## 7. 补充：新用例不是空转（QA 复验）

| 新用例 | 重放变异 | 结果 |
|---|---|---|
| `test_allow_all_still_strips_the_reviewer` | M6（去掉 `strip()`） | 红在 `test_review_decisions.py:183`，`'  E12345  ' != 'E12345'`；M6 的杀伤从 1 红变 **2 红** |
| `test_a_mapping_without_an_id_is_rejected_whole` | M9 | 未变异绿，M9 下 `:127` `DID NOT RAISE` 红 |
