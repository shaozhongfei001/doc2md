# L34 验收记录（人工可覆盖低置信度，带审计）

- 入口：`loop-entry/L34` = `c3800ce`
- 代码：`944ec13`
- **状态：`CODE_MERGED` + L32-FIND-A CLOSED**

## 1. 关闭的是什么

`qa-l22` FIND-A：L32 的门槛让低置信度**不可恢复**。

```
[table conf=0.05 + review_decisions={'R1':'RV-9'}]
  → state=NEEDS_REVIEW  ref=None  issues=[CONFIDENCE_BELOW_THRESHOLD, REVIEW_REF_IGNORED]
```

门槛把 state 改成 `NEEDS_REVIEW`，随后既有的"unresolved 不可被 ref 确认"分支把人工决议吞掉。
剩下只有两条路：**调低全局阈值**（等于对所有区域撤防）或**重跑引擎**。

而 `review_decisions` 本来就是为"机器判不了"准备的人工逃逸口。

## 2. 修法：区分两种 NEEDS_REVIEW

| 原因 | 含义 | 人能否确认 |
|---|---|---|
| `UNKNOWN_CLASS` | 不知道这是什么 | ❌ 不能凭空造类 |
| `CONFIDENCE_LOW` | 知道是表，但引擎不确定 | ✅ 可以，带审计 |
| `CONFIDENCE_MISSING` | 引擎没给概率 | ✅ 可以，带审计 |
| `DECORATIVE_WITHOUT_REASON` | 要过滤但没说理由 | ❌ 不能替它编理由 |

实现：`blocked_by` 记录降级原因，`ref` 处理按原因分派。

## 3. 审计：覆盖不删原判断

```python
issues: [CONFIDENCE_BELOW_THRESHOLD, CONFIDENCE_OVERRIDDEN]
detail: {engine_confidence: 0.05, review_decision_ref: "RV-9", blocked_by: "CONFIDENCE_LOW"}
```

**原置信度 issue 保留**，另加一条 `CONFIDENCE_OVERRIDDEN`。
如果覆盖时把原 issue 删掉，产物就变成"人工确认过的表"，而看不出"机器当时只有 0.05"——
那不是审计，是抹痕。

## 4. 用例与双向变异

| 用例 | 断言 |
|---|---|
| `test_a_reviewer_can_confirm_a_low_confidence_region_with_audit` | `CONFIRMED` + ref 落库 + `CONFIDENCE_OVERRIDDEN` + **原 issue 仍在** + 无 `REVIEW_REF_IGNORED` |
| `test_a_reviewer_still_cannot_invent_a_class_for_an_unknown_region` | `UNKNOWN` 仍 `NEEDS_REVIEW`、ref 被忽略 |
| `test_a_reviewer_cannot_confirm_a_decorative_region_without_a_reason` | 仍 `REVIEW_REF_IGNORED` |

| 变异 | 结果 |
|---|---|
| 取消覆盖通路（恒 `REVIEW_REF_IGNORED`） | 覆盖用例 **红** |
| 让 `UNKNOWN` 也能被覆盖 | UNKNOWN 与 DECORATIVE 两用例 **红** |

两个方向都钉住：**该放行的放行，该拦的拦**。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 2 个文件 |
| B | **1417 passed / 131 skipped**（1414 + 3） |
| C | `passed=true` |

## 6. 一条设计判断（写下来备查）

有人会问："人工确认了，为什么还留 `CONFIDENCE_BELOW_THRESHOLD`？"

因为两者回答**不同的问题**：

- 原 issue 陈述**引擎的状态**（"它当时只有 0.05"）——这是事实，不因人的决定而改变；
- 覆盖 issue 陈述**人的决定**（"我看了，确实是表"）——这是另一个事实。

把它们合并成一条，就等于让人的决定改写机器的观测记录。审计失去意义。
