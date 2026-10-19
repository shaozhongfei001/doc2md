# L48 验收记录（作废时间戳：决定时间 + 写入时间）

- 入口：`loop-entry/L48`
- 代码：`c7ced54`
- **状态：`CODE_MERGED`；Owner 收口补充项完成**

## 1. 两个时间，不是同一个事实

| 字段 | 谁给 | 含义 | 能不能被改 |
|---|---|---|---|
| `at` | **调用方** | 这次作废**决定**发生的时间 | 可以填错——所以要校验格式 |
| `recorded_at` | **系统** | 这条标记**真正写入**的时间（UTC，带时区） | 调用方改不了 |

只留一个都不够：

- 只有 `at`：调用方时钟错了，历史就被写错，而且没人能发现；
- 只有 `recorded_at`：丢失"决定是什么时候做出的"，而审批时效看的是决定时间。

## 2. `at` 现在要过格式校验

```text
at="昨天下午" → E_INPUT_INVALID: at '昨天下午' is not ISO-8601
```

**拒绝时不写任何东西**——半条带坏时间戳的标记比没有更糟，它会让排序和时效计算从此不可信。

## 3. 判据

| 判据 | 用例 | 实测 |
|---|---|---|
| 每条 mark 带 `recorded_at` 且可解析 | `test_every_mark_carries_a_system_write_time` | 时区非空；`at` 原样保留 |
| 非 ISO 的 `at` 被拒且不落盘 | `test_a_non_iso_decision_time_is_refused` | 消息含 `not ISO-8601`，日志为空 |
| fold 同时暴露两个时间 | `test_the_fold_exposes_both_times` | 两者不相等，且都可见 |

## 4. 变异

| 变异 | 结果 |
|---|---|
| `recorded_at` 写成空串 | 1 failed |
| 跳过 `at` 格式校验 | 1 failed |

还原后 md5 回 `4d2aa960…`，11 passed。

## 5. 与 Owner 原话的对应

> 标签 = 一条 mark，不产生新 generation，版本号不动，同时还要有作废的"时间戳"

| 要求 | 落点 |
|---|---|
| 一条 mark | `revocations.jsonl` 一行（L47） |
| 不产生新 generation | 不调用 `commit_generation`，无新目录（L47） |
| 版本号不动 | 产物 `generation_id` 不变（L47） |
| **作废时间戳** | `at` + `recorded_at` 双时间（本轮） |

## 6. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L48..HEAD` **2 个文件** |
| B | **1472 passed / 131 skipped**（1469 + 3） |
| C | `passed=true` |

## 7. 仍然开着的

| 编号 | 内容 |
|---|---|
| `L47-LIM-01` | 日志无压缩/归档 |
| `L47-LIM-02` | 文件权限未绑定主体 |
| `L46-LIM-02` | 撤销是否需同级审批（政策，等 Owner） |
| `L32-LIM-01` | 阈值 0.9 仍是候选（等真实低分样本） |
