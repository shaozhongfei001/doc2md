# L36 验收记录（空白 review ref 与公开常量）

- 入口：`loop-entry/L36` = `043c481`
- 代码：`e333c6f`
- **状态：`CODE_MERGED` + FIND-E/FIND-D CLOSED**

## 1. FIND-E：L34 把一个"无害的宽松校验"放大成了缺陷

`qa-l22` 的对照实验说得很清楚（同一输入 `engine_confidence=0.05`、`review_decisions={"R1":"  "}`）：

| 提交 | 结果 |
|---|---|
| `321bea6`（L33，覆盖通路之前） | `NEEDS_REVIEW`，`ref=None`，`REVIEW_REF_IGNORED` —— **空白 ref 被既有分支吃掉，无害** |
| `944ec13`（L34 之后） | **`CONFIRMED`**，`review_decision_ref='  '` → 一个没人做过的确认 |

根因是 `classify.py` 用真值判空：

```python
if not isinstance(ref, str) or not ref:      # "  " 是非空字符串，通过
```

**这不是 L34 新写的校验，而是 L34 让一条旧校验的缺口变得可利用**。这类"放大器效应"值得单独记：
一个宽松校验在旧路径上可能无害，因为下游还有别的分支兜住；一旦下游被放宽，它立刻变成洞。

## 2. 修法与判据

| 项 | 改动 |
|---|---|
| ref 校验 | `not ref.strip()` → `E_INPUT_INVALID`（消息改为 "non-blank"） |
| `__all__` | 加入 `DEFAULT_CONFIDENCE_THRESHOLD`，与"公开常量、唯一可 grep 的接受点"一致 |

| 用例 | 断言 |
|---|---|
| `test_a_blank_review_ref_is_refused_even_when_it_could_override` | 低置信度 + `"  "` → `E_INPUT_INVALID` |
| `test_a_blank_ref_is_refused_on_the_normal_path_too` | 普通路径同样拒绝 |
| `test_the_default_threshold_is_part_of_the_public_surface` | 常量在 `__all__` |

变异（恢复真值判空）→ **2 红**，`DID NOT RAISE` 在 `:304`。

## 3. QA 指出的、比"改代码"更重要的一条：双 issue 的误用风险

它赞成保留两条 issue（原观测 + 人的决定），但指出真正的风险**不是复查者看混，而是下游用错判据**：

> 如果有代码写 `if any(code.startswith("CONFIDENCE_") ...): reject`，
> 那么人工已确认的区域会被旧观测否掉——**这正是"人的决定被静默吞掉"的镜像**。

它查过当前仓：没有任何下游消费这些 code，现状安全。但要求落一条硬规则。已写进 `L34_loop_addendum.json` 的 `usage_rule`：

> 可用性判据以 `classification_state` 为准。任何按 issue code 拒绝的规则必须显式排除 `CONFIDENCE_OVERRIDDEN`。

这句话成本为零，但它堵的是**未来某次误用**——而现在没有任何测试能防住它，因为那个消费者还不存在。

## 4. 为什么没顺手改 `reviewer="---"`（`L31-FIND-02`）

QA 明确同意不动，并给了一句我认为该原样保留的判断：

> 谁是合法 reviewer 属授权模型，是 Owner 的职责域，QA 不应替你决定"占位串算不算主体"。

但它同时提醒：**别让这两条一起挂在"等 Owner"里被遗忘**。因此本 Loop 只收 ref 那条（纯输入卫生），reviewer 那条继续单列 open。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 3 个文件（classify.py、测试、L34 契约） |
| B | **1425 passed / 131 skipped**（1422 + 3） |
| C | `passed=true` |

## 6. QA 的过程披露也值得记

它自述第一次把探针脚本与变异脚本**并行**跑，探针 `import` 到了正处于变异态的模块，发现数字与代码不符后判为污染，**在同一干净树上串行重跑**，报告只用干净树的结果。

> 我在 L35 也踩过同型问题（变异脚本路径写错导致"假通过"）。
> 两次都是**验证动作本身**出问题，而不是代码。这类事不会体现在测试里，只能靠"发现数字与预期不符时不信第一次结果"。

## 7. 仍未处理

| 编号 | 内容 |
|---|---|
| `L31-FIND-02` | `reviewer="---"` 仍被接受（授权模型，待 Owner） |
| `L32-LIM-01` | 阈值 0.6 未校准（需真实 VLM 分布） |
| 四类外部输入 | 未变 |

## 8. 独立复验（`qa-l22`，`e333c6f`）与一处范围口径更正

### 8.1 四项复验全过，且它把"两条路径"扩到了四条

| 输入 | 结果 |
|---|---|
| 覆盖路径 `conf=0.05` + `ref='  '` | `E_INPUT_INVALID` |
| 普通路径 `conf=0.95` + `ref='   '` | `E_INPUT_INVALID` |
| UNKNOWN 路径 + `ref='  '` | `E_INPUT_INVALID` |
| DECORATIVE 无理由 + `ref='\t'` | `E_INPUT_INVALID` |
| `ref=''` / `ref=123` | `E_INPUT_INVALID` |

关键点它点明了：**校验发生在分支分派之前**，所以"能不能覆盖"不再影响是否放行——空白 ref 在任何路径上都没有机会走到 `CONFIRMED`。

### 8.2 三点对照可原地复现（同一副本逐次 checkout）

```text
321bea6 (L33)  ref='  ' -> ACCEPTED NEEDS_REVIEW ref=None  [BELOW_THRESHOLD, REF_IGNORED]
944ec13 (L34)  ref='  ' -> ACCEPTED CONFIRMED    ref='  '  [BELOW_THRESHOLD, OVERRIDDEN]
e333c6f (L36)  ref='  ' -> REJECTED E_INPUT_INVALID
```

第二个变异（从 `__all__` 移除常量）→ 1 红，`assert 'DEFAULT_CONFIDENCE_THRESHOLD' in [...]` @ `:315`。

### 8.3 一处我必须接受的范围口径更正

我在请它复核时写的是 `git diff 22acde5 e333c6f --stat` 应为 3 个文件。**按字面执行会失败：实际 12 个文件。**

原因不是越界，而是**区间基准选错**：`22acde5` 是 L35 **之前**的提交，其间还有 `a7fda18`（L35 实现），所以区间里合法地包含了 L35 的 9 个文件。

正确表述：`git diff --stat e333c6f^ e333c6f`，或 `git show --stat e333c6f`（父提交 `043c481`）。

> 它说得对，这不是挑字眼：**区间基准选错 → 结果与预期不符 → 要么误判越界，要么把"不符合"当成"我记错了"放过。**

这是本轮**第三个**"验证动作本身出问题"的实例，前面两个是它自己的并行污染与我的脚本路径错。三条合起来给出一条规则：

> **范围断言必须钉在提交的父提交上，而不是"我上一轮记得的 HEAD"上。**
> 前者是结构事实，后者是记忆。

### 8.4 残留观察（`L36-OBS-01`，不建议开 Loop）

`ref` 通过校验后未做存储规范化：`ref=' RV-9 '` → 接受，原样写入 `visual.review_decision_ref` 与 `CONFIDENCE_OVERRIDDEN.detail`。

与 FIND-E **不同族**：这是一个**真决定**（有主体、有意图），只是带了空格；FIND-E 是**伪造的决定**（没人做过）。所以不按缺陷处置，只登记观察：若下游用 ref 与台账精确匹配，前导/尾随空格会 miss。

可选硬化：校验通过后 `ref = ref.strip()` 再写入。当前不做。
