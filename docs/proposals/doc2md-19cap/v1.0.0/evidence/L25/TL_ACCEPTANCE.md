# L25 验收记录（已知集按证据来源重建，修 L23 假拒绝）

- 入口：`loop-entry/L25` = `29fa6f6`
- 代码：`cfd2e31` + `0e93ce4`
- **状态：`CODE_MERGED` + L23 的 BLOCKER 已修 + 待独立复验**

## 1. 关闭的是什么

`qa-l21` 判 **L23 = QA_REJECT**，核心理由：

> L23 把"文档级证据"实现成了"**注释**声明的证据"。

生产路径上 `omission_evidence_ids` 来自 `native_tables.py` 对 `w:gridAfter` 的解析
（`docx:gridAfter:{row}:{col}`），**从不经过注释**。于是任何含 gridAfter 的合法表格都被拒。

## 2. 修法：已知集按**来源**重建，而不是按**声明**收集

```python
known = 注释证据(notes) ∪ 结构证据(由 cells/n_cols 重算)
```

| 组件 | 作用 |
|---|---|
| `_structural_evidence_ids(table)` | 从 cells 与 `n_cols` 重算每行的空洞列，按位置生成 `docx:gridBefore/gridAfter:{row}:{col}` |
| **不读** `omission_evidence_ids` | 它是被校验的对象，不能同时充当"已知"的来源 |
| `correction` 复用同一实现 | 不再有第二份口径（L23 的两份实现就曾各自漂移） |

关键区别：**"表声明的 id" 不进已知集，"表结构蕴含的 id" 才进。** 两者都来自这张表，
但后者可独立重算，前者不可验证——这正是空转与有效校验的分界。

## 3. 三条判据（QA 要求成对，缺一不可）

| 判据 | 用例 | 结果 |
|---|---|---|
| ① 合法通过 | `test_a_table_with_grid_after_still_assembles` | 装配成功，`omission_evidence_ids == ['docx:gridAfter:1:2']` |
| ② 幽灵拒绝 | `test_an_omission_evidence_id_that_no_note_registered_is_refused` | `E_REVIEW_PACKET_INVALID`，`detail.missing == ['EV-GHOST']` |
| ③ 函数行为直断 | `test_evidence_candidates_admit_structure_and_reject_an_unfounded_claim` | 结构证据**在**、`EV-GHOST` **不在** |
| ④ 纠错侧 | `test_a_grid_after_table_survives_a_correction` | `publishable=True`，omission 保留 |

③ 是针对 QA 的 M1a 发现的：旧用例是白盒（自己调函数再喂 packet），
所以"取值逻辑退回旧行为"时它照样绿。现在直接断言函数输出。

## 4. 双向变异验证

| 变异 | 目标用例 | 结果 |
|---|---|---|
| A：把表声明并回已知集（恢复 L23 前的空转） | ③ 函数级断言 | **红**（`EV-GHOST` 出现在结果里） |
| B：删掉结构证据（恢复 L23 的过度收窄） | ① gridAfter 装配 | **红**（`E_REVIEW_PACKET_INVALID`） |

A 与 B 分别对应两个相反的错误方向，**同时被钉住**才算这条规则有边界。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 4 个文件（pipeline.py、correction.py、2 测试） |
| B | **1391 passed / 131 skipped**（1388 + 3） |
| C | `passed=true` |

## 6. 这轮暴露的判断错误

我原以为"收窄已知集"是单向变严，只会多拒不会少拒——**这在守卫上是真的，在功能上是假的**：

> 收窄守卫的**输入集合**，等于放宽守卫的**拒绝面**。

L23 让子集校验真正生效（这部分是对的，M2/M3 可证），但它同时把合法输入推出去了。
我当时只验了"是否还能拒绝"，没验"是否还该通过"——**单向验证必然漏掉这类错误**。

## 7. 仍未关闭

- L25 的独立复验（`qa-l21` 已收到复验请求）；
- `L20-LIM-01`（英文续页词缀 `(cont'd)` 未覆盖）、`L20-LIM-02`（表名降级依赖复核环节存在）；
- 四类外部输入缺口不变。
