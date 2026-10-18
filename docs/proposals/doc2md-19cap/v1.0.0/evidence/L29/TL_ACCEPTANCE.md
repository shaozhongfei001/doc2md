# L29 验收记录（英文续页词缀，关闭 L20-LIM-01）

- 入口：`loop-entry/L29` = `fc7bb23`
- 代码：`d0191e9`
- **状态：`CODE_MERGED` + L20-LIM-01 CLOSED**

## 1. 关闭的是什么

`L20-LIM-01`：续页词缀只覆盖中文四种（`续表`/`续前`/`（续）`/`(续)`），
英文报表的续页表（`Table 1 (continued)` 等）落到 `CAPTION_CONFLICT` → `NEEDS_REVIEW`。

**注意定性**：这不是假拒绝（`NEEDS_REVIEW` 仍可复核），而是**把一个确定的同一张表送进复核队列**——
代价是复核成本，不是正确性。所以它的优先级低于 L23/L25 那类假拒绝，但不能记成"不影响"。

## 2. 实现

```python
_CAPTION_EN_PREFIXES = ("(continued)", "(cont'd)", "(cont.)", "cont'd", "continued")
_CAPTION_EN_SUFFIXES = ("(continued)", "(cont'd)", "(cont.)",
                        "-continued", "-cont'd", "-cont.", "continued", "cont'd")
```

三条约束：

| 约束 | 理由 |
|---|---|
| 大小写不敏感 | `(Continued)` / `(CONT.)` / `(Cont'd)` 在真实报表里都出现 |
| 只剥前后缀 | 全局 `replace("continued", "")` 会毁掉 `Continued Care Holdings` 这类真实表名 |
| 中英文互不影响 | 中文用例一条不改，确保没有回归 |

## 3. 用例

| 用例 | 断言 |
|---|---|
| `test_english_continuation_markers_are_the_same_label`（4 参数） | `Table 1 (continued)` / `(cont'd)` / `(cont.)` / `- Continued` → `JOIN_CANDIDATE` |
| `test_english_markers_are_case_insensitive_and_prefix_bounded` | `(CONTINUED)`、`(Cont'd)` 归一；`Continental Bank`、`Continued Care Holdings` **不被误伤** |

第二条是关键：只验"能归一"会写出一个把 `Continental` 也吃掉的实现。

## 4. 变异验证

| 变异 | 结果 |
|---|---|
| 只禁用**前缀**英文词缀 | 1 红（`ContinuedCareHoldings != CareHoldings`）——因为后缀路径仍能处理 `(continued)` 形式 |
| **前后缀全部禁用** | **5 红**（4 参数 + 词边界用例） |

第一条变异的结果值得记：**它证明单靠后缀路径也能覆盖大部分写法**，
所以如果只跑参数化那四条，会以为"前缀逻辑"也被钉住了。是词边界那条用例把它区分出来的。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 2 个文件 |
| B | **1401 passed / 131 skipped**（1396 + 5） |
| C | `passed=true` |

## 6. 结论

`L20-LIM-01` **关闭**：中英文续页标记均归一，且都不误伤内容词。

仍开：`L20-LIM-02`（表名降级为 `NEEDS_REVIEW` 后依赖复核环节真实存在）。
