# L19 验收记录（归属校验直证 + 重复实现一致性）

- 入口：`loop-entry/L19` = `f8cb3a3`
- 代码：`69be46d`（只改测试）
- **状态：`CODE_MERGED` + 2/2 CLOSED + 待独立 QA 复验**

## 1. 来源：第三轮抽检（L12 / L14 / L15）

`qa-l19` 注入 8 个变异，**8/8 CAPTURED**。但其中 MB1 被它判定为**"间接捕获"**，这个区分很重要：

| 事实 | 含义 |
|---|---|
| 删掉"必须可归属"守卫后，用例确实红了 | 表面看是钉住的 |
| 但红的原因是**改抛了另一个错** `E_INPUT_INVALID` | 下游 `preview_patch` 的 reviewer 检查在替它兜底 |

也就是说：**这条守卫从未被自己的断言钉住过，它只是恰好被第二道防线挡住了。**

## 2. 缺口：no-op 修正能绕过归属校验

`_build_patch` 在无 ops 时提前返回，**no-op 修正根本走不到 `preview_patch`**。
于是兜底的那道 reviewer 检查也不会执行。QA 实测（基线代码，只删守卫）：

```
改值 + ref=''   -> REFUSED  E_INPUT_INVALID（下游兜住）
no-op + ref=''  -> ACCEPTED publishable=True
                   stats.corrections[-1].review_decision_ref == ''
```

**一次没有任何审查归属的空操作修正被接受，并以 `''` 记入审计记录。**

## 3. 补的两条断言（只改测试）

| 条目 | 用例 | 它钉住什么 |
|---|---|---|
| T1901 | `test_a_no_op_correction_still_needs_an_attributable_decision` | 空 `review_decision_ref` → `E_REVIEW_REQUIRED`，且不依赖下游兜底 |
| T1902 | `test_the_two_note_attribution_implementations_agree` | `pipeline` 与 `correction` 两份 `_note_candidates_for` 在 3 个表序号上归属一致 |

## 4. 证据三坐标（作者实测，待 QA 独立复核）

| 变异 | 变红用例 | 失败位置与表达式 | 还原 |
|---|---|---|---|
| `correction.py` `if not correction.review_decision_ref or ...` → `if False:` | `test_a_no_op_correction_still_needs_an_attributable_decision` | `test_correction.py:866` `Failed: DID NOT RAISE <ExtensionError>` | 1 passed |
| 只改 `correction` 侧 `if table_index == 0:` → `if True:`（制造漂移） | `test_the_two_note_attribution_implementations_agree` | `test_correction.py:903` `attribution differs between the two implementations at table index 1` | 1 passed |

两条都红在**它主张的那一行**，不是调用行、不是崩溃。

## 5. 为什么留两份重复实现（而不是合并）

QA 建议"收敛为单一实现或加等式断言"。我选后者：

- 合并要动产品代码（跨 `pipeline` 与 `correction` 的依赖方向），属于独立变更，不在"只补断言"的范围；
- 直接 import 另一模块的私有函数会让**重命名静默改变行为**——这是当初决定复制的理由；
- 等式断言能达到同样效果：**漂移会被观测到**，而合并只是把漂移的可能性提前消除。

代价已登记：`_note_candidates_for` 若改名，两条测试会先红——那是有意义的红。

## 6. 假拒绝排查（QA 专项，MB1–MB4）

结论：**没有一条守卫会在"合法文档 + 合法修正"上误触发。**

| 守卫 | 是否读文档 | 结论 |
|---|---|---|
| MB1 归属 | 否，只读参数 | 无误伤；但**政策过强**：预览调用也必须先伪造一个 ref |
| MB3 generation_id | 否，只读参数 | 链式修正（GEN-1→2→3）安全 |
| MB2 类型 | 读字段当前类型 | 唯一可达误伤：`tuple/list`、`int/float` 等价被拒；**落点全在非审阅字段** |
| MB4 可发布 | 读 rebuild 结果 | 触发均对应真实重建失败，属正确 fail-closed |

已登记 `L19-LIM-01`：Python 调用方传 `tuple` 到列表字段、或 `1.0` 到 int 字段会被硬拒（审阅字段都是 str/None，故不影响正常使用）。

## 7. 覆盖边界

| 范围 | 独立抽检 |
|---|---|
| L09 / L10 / L11 / L12 / L13 / L14 / L15 / L16 | ✅ 三轮共 28 变异 |
| L05 / L06 / L07 / L08 | ❌ **仍未做** |

## 8. 结论

两条 CLOSED。这轮学到的不是"又补了两个用例"，而是：

> **"测试变红了"和"守卫被钉住了"是两件事。**
> 判据必须包含"红的是不是它自己的那条断言"——否则下游兜底会让一条从未生效的守卫看起来被覆盖。

前两轮把判据从"代码里有检查"推进到"有变异能让它红"；这一轮再推进一层：
**红的原因必须归属于被测守卫本身。**
