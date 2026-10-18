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
