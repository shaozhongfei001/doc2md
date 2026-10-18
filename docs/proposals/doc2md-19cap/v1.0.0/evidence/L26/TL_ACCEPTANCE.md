# L26 验收记录（结构证据以 slots 为基准重算）

- 入口：`loop-entry/L26` = `b2fa653`
- 代码：`50187c1` + `b2f5a18`
- **状态：`CODE_MERGED` + L25 两条残留已修 + 待独立复验**

## 1. 来源：`qa-l21` 对 L25 的两条残留

| 编号 | 方向 | 现象 |
|---|---|---|
| 残留一（MAJOR） | **过窄** | 整行无 `w:tc`（`gridAfter=n_cols`）的文档：`extract_tables` 接受，`build_review_packet` 拒绝 |
| 残留二（MINOR） | **过宽** | `w:vMerge="continue"` 位置不落地 cell，被重算成 `docx:gridBefore:2:0` —— 凭空多出一个"可重算"的 id |

两者同一根因：**L25 用 `cells` 反推空洞**，而 `cells` 里有两类信息天然不落地（整行为空、合并续行）。

## 2. 修法：基准从 `cells` 换成 `slots`

```python
holes = [c for c in range(n_cols) if slots[r][c] is None]
```

| 现象 | 在 slots 下的表现 |
|---|---|
| 整行为空 | 整行 `None` → 整行 `gridAfter`（与提取器一致） |
| `vMerge continue` | 指向 anchor id → **不是**空洞 |
| 合并跨列 | 每个覆盖位置都有 anchor → 不是空洞 |

`omitted_slots` / `omission_evidence_ids` **仍然不用**：它们是被校验对象，用它重算等于恢复空转。

无 `slots` 时退化为 `cells` 重算，并注明为降级路径——降级路径也补了 `rowspan` 展开，否则它会重犯残留二。

## 3. 判据（QA 三条 + 我加的一条）

| 判据 | 用例 | 结果 |
|---|---|---|
| 整行无 tc 必须装配成功 | `test_a_row_with_no_cells_still_assembles` | omission = `gridAfter:1:0..2` |
| vMerge 不得产洞 | `test_a_merged_cell_does_not_look_like_a_hole` | 重算为空，装配成功 |
| 幽灵 id 仍须拒 | L25 的三条负例 | 全部仍红/仍拒 |
| **两处实现必须一致** | `test_recomputed_structure_matches_the_extractor_record` | 重算的 (row,col) 集合 == 提取器 `omitted_slots` |

第四条是我加的：两个独立实现对"哪些位置是空洞"必须达成一致，**分歧本身就是缺陷**（L25 的两条残留正是分歧的两个方向）。

## 4. 变异验证

| 变异 | 结果 |
|---|---|
| B：跳过整行为空的行 | 整行用例**红**；交叉校验**红** |
| A：强制降级到 `cells` 路径 | **全部仍绿——等价变异** |

A 需要解释，不能含糊：降级路径已补 `rowspan` 展开，所以它现在与 slots 口径一致。
即 **A 在行为上等价，不是用例漏测**。我把这条如实记下来，而不是标成 CAPTURED。

> 但这也说明：`slots` 基准的**必要性**目前只有"更忠实于几何"这一条理由，
> 不是"没有它就会错"。若要让它成为硬约束，需要一条只有 slots 才能判对的样例
> （例如 cells 缺 rowspan 信息而 slots 完整）。已登记为 `L26-LIM-01`。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 3 个文件（pipeline.py + 2 测试） |
| B | **1395 passed / 131 skipped**（1391 + 4） |
| C | `passed=true` |

## 6. 仍未关闭

| 编号 | 内容 |
|---|---|
| `L26-LIM-01` | `slots` 基准 vs 降级路径目前行为等价，缺一条只有 slots 能判对的样例 |
| `L26-LIM-02` | 交叉校验用 `omitted_slots` 作 oracle——**只用于测试**；生产路径不得引用它，否则又成循环 |
| `L20-LIM-01/02`、四类外部输入缺口 | 不变 |

## 7. 结论

L25 的两条残留（一个过窄、一个过宽）同时关闭，且这次**两个方向各有断言**——
上一轮的教训是：只验一个方向，必然漏掉另一个。

## 8. 更正与关闭（由 qa-l21 复验触发，处置见 L27）

### 8.1 `L26-LIM-01` 的表述不准确，已更正并关闭

本文件第 4 节写的是"`slots` 基准的必要性只有'更忠实于几何'这一条理由"。**这个说法不准确。**

QA 实测并给出论证：`slots` 由 `build_slots(cells, n_rows, n_cols)` 生成，**是 cells 的纯函数**；
在降级路径补上 `rowspan` 展开与 `range(n_rows)` 遍历之后，两条路径在任意合法 payload 上输出的
`(row, col)` 集合**必然相同**（变异 A 下定向 6 条 + 全量 1395 条全绿）。

因此准确表述是：

> `slots` 与 `cells` **在本产品内恒等**，故该分支无法被产品级夹具区分。
> `slots` 的价值是把 `rowspan` / 空行的正确性交给派生矩阵，**省掉手写展开这个易错点**——
> 这是维护性收益，不是可判据的收益。

`L26-LIM-01` **关闭**（不是"待补样例"，而是"不存在可补的样例"）。

### 8.2 降级路径在产品内不可达，已删除

QA 的观察：`build_review_packet` 的产品调用者只有 `pipeline.py` 与 `correction.py` 两处，
其 table 均来自 `extract_tables`，**必有 `slots`**；降级路径只被手工 payload 测试走到。

两套口径正是 L23→L25→L26 三轮反复的根源，已在 **L27** 删除，改为
`slots` 缺失即 `E_INPUT_INVALID`（fail-closed）。

### 8.3 两处既有用例被误改，已还原（L27）

`test_correction.py` 里有两处 `object_id` 被误改为表头单元格，注释却写"第二行没有单元格"——
而那两个 fixture 的第二行各有两个 `tc`。注释与事实不符，且削弱了"订正**数据**单元格"的语义覆盖。

QA 用变异 C 证明这两处改动**不必要**（改回后 2 passed）。L27 已还原，只保留真正适用于空行表的那一处，并把注释改准。
