# L33 验收记录（阈值显式化 B-lite + 用例强度）

- 入口：`loop-entry/L33` = `9cb71c3`
- 代码：`321bea6`
- **状态：`CODE_MERGED` + 未校准阈值不再可被隐式接受**

## 1. 来源：`qa-l22` 对 A 方案的否证

我原来的 A 方案是"给默认值 + 标注未校准"。QA 的判断比我准：

> `threshold_calibrated: False` 只挂在 `CONFIDENCE_BELOW_THRESHOLD` 的 detail 上。
> **门槛没触发时，产物里没有任何东西说明这次放行用的是未校准的 0.6。**
> 所以不是"没人会被迫看见"，而是**在最该被看见的时刻（放行）恰好看不见**。

我原来的估算错在把"未校准"当成一个**状态标注**，而它实际是一个**决策**：
门槛一旦有默认值，未校准就从"信息"变成了"没人做过的选择"。

## 2. B-lite 的形态

```python
DEFAULT_CONFIDENCE_THRESHOLD = 0.6          # 公开常量，唯一可 grep 的接受点
def route_regions(..., confidence_threshold: float)   # 无默认值
```

| 方案 | 拒绝"未校准"的能力 | 能否现在跑 |
|---|---|---|
| A（原）默认值 + 标注 | ❌ 放行时不可见 | ✅ |
| 裸 B 必须自己编一个数 | ✅ | ✅（但会催生私造的 0.55/0.7） |
| **B-lite** 常量 + 必填 | ✅ 每处调用都留下接受动作 | ✅ 无额外前置条件 |

裸 B 我原先担心"必须先校准才能跑"，其实那是 A 的惰性换算——B-lite 证明这个代价不存在。

## 3. 落地

| 改动 | 内容 |
|---|---|
| 签名 | `confidence_threshold: float`（无默认，keyword-only） |
| 常量 | `DEFAULT_CONFIDENCE_THRESHOLD` 保留并公开 |
| 测试 | 20 处调用逐处显式传参（不是 helper 默认——那会把接受动作重新藏起来） |
| 新用例 | `test_the_threshold_must_be_chosen_explicitly`：不传即 `TypeError` |

## 4. FIND-C：用例强度

`qa-l22` 指出 strict 分支在门槛被整段删除时，红在 `:195 IndexError`（列表取不到元素），
**那是崩溃，不是"行为消失"**。

已改为先断言存在：

```python
assert "CONFIDENCE_BELOW_THRESHOLD" in codes(strict)   # 先
detail = [...][0]["detail"]                            # 后
```

实测（删门槛）：红在 `:200 assert 'CONFIDENCE_BELOW_THRESHOLD' in []`。

## 5. 变异验证

| 变异 | 结果 |
|---|---|
| 给 `confidence_threshold` 恢复默认值 `= 0.6` | `test_the_threshold_must_be_chosen_explicitly` 红（TypeError 未发生） |
| 删掉整个门槛块 | strict 分支红在 `:200` **断言**（不再是 IndexError） |

## 6. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 2 个文件 |
| B | **1414 passed / 131 skipped**（1413 + 1） |
| C | `passed=true` |

## 7. QA 逐提交复跑的数字（外部核对）

| 提交 | 声称 | 实测 |
|---|---|---|
| L30 `e566b92` | 1405 | 1405 ✔ |
| L31 `339ec2d` | 1407 | 1407 ✔ |
| L32 `3da7b3f` | 1414 ✘ | **1413** |
| L33 `321bea6` | 1414 | 1414 ✔ |

L32 的笔误在两个地方（提交信息 + 本应写准的证据）都已更正。
**我手写全量数字已经错两次了**，此后一律从 pytest 输出复制。

## 8. 仍未处理

| 编号 | 内容 |
|---|---|
| `L32-FIND-A` | **人工复核无法救回低置信度结果**：门槛把 state 改成 `NEEDS_REVIEW`，随后"unresolved 不可被 ref 确认"把人工决议吞掉（`REVIEW_REF_IGNORED`） |
| `L31-FIND-02` | `reviewer="---"` 仍被接受（授权模型，待 Owner 裁定） |

`L32-FIND-A` 我认为是本轮**最需要产品决策**的一条：它意味着"机器不确定时，人也无法确认"。
下一 Loop 处理，方案待定（可能需要区分"分类未知"与"分类已知但置信度低"两种 `NEEDS_REVIEW`）。
