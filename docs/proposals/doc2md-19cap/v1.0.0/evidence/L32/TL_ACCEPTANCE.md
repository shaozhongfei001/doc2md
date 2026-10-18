# L32 验收记录（置信度门槛：低与缺失都不等于确定）

- 入口：`loop-entry/L32` = `9eccf3f`
- 代码：`3da7b3f`
- **状态：`CODE_MERGED` + L31-FIND-01 CLOSED（B1/B2）**

## 1. 关闭的是什么

`qa-l22` B1/B2：`route_regions` 对 `engine_confidence` **没有任何门槛**。

| 输入 | 改动前 |
|---|---|
| `engine_confidence=0.05` | `TABLE / CANDIDATE`，无 issue |
| `engine_confidence=0.0` | 同上 |
| `engine_confidence=None` | 同上 |
| `engine_confidence=0.99` | 同上 |

四种输入完全等价。**"引擎在猜"与"引擎确定"路由结果相同**，且不产生任何提示。

## 2. 方案 A+B（按 Owner 选择）

| 状态 | 行为 |
|---|---|
| 低于阈值 | `NEEDS_REVIEW` + `CONFIDENCE_BELOW_THRESHOLD`（detail 带 `engine_confidence` / `threshold` / `threshold_calibrated: false`） |
| 缺失（`None`） | `NEEDS_REVIEW` + `CONFIDENCE_MISSING`（"没有概率"不是"高概率"） |
| 高于阈值 | 保持 `CANDIDATE`，不产生 issue |

**只作用于 `TABLE/FLOW/CHART`**（`FACT_CLASSES`）：`DECORATIVE` 被过滤、`UNKNOWN` 本来就要复核，
对它们加门槛只是噪音。

## 3. 阈值的诚实标注

```python
DEFAULT_CONFIDENCE_THRESHOLD = 0.6   # 起点值，未校准
```

注释与 issue detail 都写明 `threshold_calibrated: false`。

**这不是保守，是准确**：0.6 没有经过任何真实 VLM 输出校准。把它写成"推荐阈值"，
就是 L04 那条"0.8/0.15 是起点值、必须 golden 校准"的同型错误——**把未验证的数字包装成结论**。

调用方可传 `confidence_threshold` 覆盖；非法值（`0`、`>1`、非数）→ `E_INPUT_INVALID`。

## 4. 用例与变异

| 用例 | 断言 |
|---|---|
| `test_low_confidence_downgrades_a_fact_class_to_review` | 0.05 → `NEEDS_REVIEW` + issue |
| `test_missing_confidence_is_not_a_high_confidence` | `None` → `NEEDS_REVIEW` + issue |
| `test_high_confidence_still_reaches_candidate` | 0.95 → `CANDIDATE`，`issues == []` |
| `test_the_threshold_is_parameterised_and_marked_uncalibrated` | 0.2 阈值放行 0.3；0.9 阈值降级且 `calibrated=false` |
| `test_a_bad_threshold_is_refused` | `0.0` → `E_INPUT_INVALID` |
| `test_decorative_and_unknown_are_not_subject_to_the_gate` | 两类不受影响 |
| 既有 `test_vlm_self_reported_confidence_...` | 补断言：模型自报 0.99 **也不能**让它升出复核 |

| 变异 | 结果 |
|---|---|
| 删掉整个门槛块 | **4 红** |
| 默认阈值降到 0.01 | **1 红**（低置信用例） |

两个方向都钉住：门槛存在（变异1）、门槛的**值**有效（变异2）。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 2 个文件 |
| B | **1413 passed / 131 skipped**（1407 + 6） |
| C | `passed=true` |

**更正**：提交信息 `3da7b3f` 写的 "1414" 是笔误，实测 **1413**。以本文件与门禁记录为准。

## 6. fixture 的语义变化（必须记）

测试 helper 的默认 `engine_confidence` 从 `None` 改为 `0.9`，并在注释里写明：

> 自从 L32，"缺失"是一个**独立的受审场景**，不再是可以默认的取值。

这带来一个副作用：既有用例里凡是依赖"默认 None 也能 CANDIDATE"的，全部需要显式给置信度。
这不是"为了过测试改 fixture"，而是**旧 fixture 编码了错误前提**（没有置信度也能确认）。

## 7. 仍未处理

| 编号 | 内容 |
|---|---|
| `L32-LIM-01` | 0.6 未校准；需要真实 Paddle/VLM 输出的分布才能定，当前只是起点 |
| `L32-LIM-02` | 门槛只作用于"引擎标签 → 分类"这一段；VLM 候选路径的低置信度未单独设阈值（VLM 自报置信度按设计不参与路由） |
| `L31-FIND-02` | `reviewer="---"` 仍被接受——属授权模型，待规格方裁定 |

## 8. 阈值默认值改判：从 A 改为 B-lite（L33）

`qa-l22` 复验本 Loop 时否掉了 A 方案（保留默认值 + 标注），理由比我原来的更准确：

> 「未校准」只挂在 `CONFIDENCE_BELOW_THRESHOLD` 的 detail 上。**门槛没触发时，产物里没有任何东西说明
> 这次放行用的是未校准的 0.6。** 所以不是"没人会被迫看见"，而是**在最该被看见的时刻（放行）恰好看不见**。

它给了一个我没想到的中间形态 **B-lite**：

```python
DEFAULT_CONFIDENCE_THRESHOLD = 0.6          # 保留公开常量
def route_regions(..., confidence_threshold: float)   # 无默认值
```

调用点必须写一次 `confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD`——
**接受 0.6 变成一个 grep 得到的动作，而不是一个没人选择的默认**。
它同时避免了我担心的代价（"必须先校准才能跑"）：不需要任何前置条件，跑起来和原来一样。

已由 **L33**（`321bea6`）落地，并补 `test_the_threshold_must_be_chosen_explicitly` 钉住"无默认"。

## 9. 数字核对（qa-l22 逐提交复跑）

| 提交 | 提交信息 | 实测 |
|---|---|---|
| `e566b92` L30 | 1405 | 1405 ✔ |
| `339ec2d` L31 | 1407 | 1407 ✔ |
| `3da7b3f` L32 | 1414 ✘ | **1413** |
| `321bea6` L33 | 1414 | **1414** ✔ |

L32 的笔误已在本文件第 5 节更正；L33 之后基线为 **1414 passed / 131 skipped**。
