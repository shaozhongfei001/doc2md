# L27 验收记录（结构证据单一口径）

- 入口：`loop-entry/L27` = `28b7fd7`
- 代码：`148fa6e`
- **状态：`CODE_MERGED` + L26 两条整改完成 + 待 QA 最终确认**

## 1. 两条整改的来源

`qa-l21` 对 L26 判"功能面建议 PASS，附两条整改"：

| 整改 | 内容 |
|---|---|
| ① | `test_correction.py` 两处既有用例的 `object_id` 被误改为表头单元格，注释与 fixture 事实不符 |
| ② | `cells` 降级路径在**产品内不可达**，两套口径是 L23→L25→L26 反复的根源 |

## 2. 整改一：还原被误改的用例

我的全局替换把三处 `first_data_cell_id(assembly)` 一起改掉了，其中两处的 fixture **第二行有单元格**，
注释"第二行没有单元格"是错的。QA 用变异 C（只把前两处改回）证明改动不必要：**2 passed**。

已还原两处，保留真正适用于空行表的那一处，并把注释改准：

```python
# This fixture's second row has no cells at all, so it has no data cell to
# correct; the header cell is the only correctable target here.
```

代价说明：既有的"订正**数据**单元格"覆盖被恢复，空行表仍由表头单元格订正覆盖——两者不再混用。

## 3. 整改二：删除降级路径，改 fail-closed

```python
raise ExtensionError("E_INPUT_INVALID", "table payload has no slots matrix; ...")
```

理由（QA 的论点，我接受）：

- `slots` 是 `cells` 的纯函数，降级路径只能**漂移**、不能**补充**；
- 产品调用方恒有 `slots`，"降级"实际只发生在手工 payload 测试里；
- 静默降级意味着**那道校验从未对这类输入生效过**，而它会以"通过"的形式呈现。

## 4. 判据

| 判据 | 用例 | 结果 |
|---|---|---|
| 两处用例恢复数据单元格订正 | 既有 38 例 | 全绿 |
| 缺 `slots` 必须拒绝 | `test_a_payload_without_slots_is_refused_rather_than_degraded` | `E_INPUT_INVALID`，消息含 "no slots matrix" |
| 三类文档行为不变 | gridAfter / 整行空洞 / vMerge | 全绿 |

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 3 个文件 |
| B | **1396 passed / 131 skipped**（1395 + 1） |
| C | `passed=true` |

## 6. L26 证据已更正

`L26-LIM-01` 原文"`slots` 的必要性只有更忠实几何这一条理由"**不准确**。准确表述是：

> `slots` 与 `cells` 在本产品内**恒等**，该分支无法被产品级夹具区分；
> `slots` 的价值是省掉手写 `rowspan` 展开这个易错点——**维护性收益，不是可判据的收益**。

`L26-LIM-01` 已关闭，理由是"不存在可补的样例"，而非"待补样例"。

## 7. 这一串 Loop 的净教训

`L23 → L25 → L26 → L27` 四轮反复的根因不是某个 bug，而是**同一个决定做了两次**：

| 轮次 | 两套口径 | 后果 |
|---|---|---|
| L23 | 注释证据 vs 表声明 | 拒掉 gridAfter |
| L25 | cells 反推 vs 提取器 | 漏整行、造幽灵 |
| L26 | slots vs cells 降级 | 降级不可达且必然等价 |
| L27 | **只剩一套** | — |

> 任何"为了兼容某种输入"而保留的第二套实现，都会在下一轮变成漂移源。
> 兼容性应当靠**显式拒绝**表达，而不是靠一条更宽松的旁路。
