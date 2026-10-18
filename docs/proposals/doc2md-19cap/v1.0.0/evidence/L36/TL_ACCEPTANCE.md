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
