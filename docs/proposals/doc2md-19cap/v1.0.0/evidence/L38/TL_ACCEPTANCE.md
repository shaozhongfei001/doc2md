# L38 验收记录（Paddle 表格几何绑定）

- 入口：`loop-entry/L38` = `3931eca`
- 代码：`fc055d6`
- **状态：`CODE_MERGED` + 几何绑定可用（L38-FIND-01 已修）**

## 1. 第一次拿真实响应喂适配器，立刻发现 schema 漂移

`paddle_structure.py` 里一直挂着 `PADDLE_LOCK_NOTE`：

> field names follow the official PP-StructureV3 description … and have NOT been
> confirmed against a golden raw response in this repository

这次用 3.7.0 的真实响应一跑，**第一行就崩**：

```text
E_ENGINE_SCHEMA_DRIFT: cell_box_list[0] contains a non-point 41.03172445297241
```

原因是形状不同，不是字段改名：

| 适配器假设（官方"四点"描述） | 3.7.0 实际返回 |
|---|---|
| `[[x1,y1],[x2,y1],[x2,y2],[x1,y2]]` | **`[x1, y1, x2, y2]`** |

这属于**同一字段的形状漂移**——改名会被 `E_ENGINE_SCHEMA_DRIFT` 挡住，改形状也会，
但只有真实响应能暴露。现在两种形状都接受，其它形状仍拒绝（不认识的形状无法比较）。

> 教训：`PADDLE_LOCK_NOTE` 这种"自述未验证"的注释，价值不在于免责，
> 而在于它准确预言了**这里有洞**——只是没人去踩。

## 2. 几何绑定：把"按文档顺序"换成"用测量证明"

新增 `cell_box_order="GEOMETRY"`：

```
box 的 x1/x2、y1/y2 ──聚类──> 网格线 ──> 每格期望矩形 ──比对──> 附加 polygon
                                              └─超差─> CELL_BOX_GEOMETRY_MISMATCH（不绑定）
```

两级失败，都**不绑定**：

| 情形 | `reason` | 含义 |
|---|---|---|
| 网格线数量与 HTML 声明的行列数不符 | `grid` | 连逐格比较的前提都不成立 |
| 逐格误差 > 4 px | `geometry` | 网格对得上，但某一格落错了位置 |

**为什么不用"最近格"兜底**：最近格永远能配上，配错了看不出来。绑定失败必须可见。

## 3. 真实夹具实测

`fixtures/ppstructure_v370_table_cjk.json`（8.3 KB，3.7.0 实跑，5×4 表格）：

| 指标 | 值 |
|---|---|
| HTML 解析单元格 | 20 |
| `cell_box_list` | 20 |
| 网格 | 5 行 × 4 列 |
| 最大误差 | ≤ 4.0 px（实测通过） |
| 绑定结果 | `VERIFIED_BY_GEOMETRY`，每格带 `polygon` |
| issue | 无 `CELL_BOX_*` |

## 4. 六条用例与两个变异

| 用例 | 断言 |
|---|---|
| 真实响应绑定 | 20 格、首行 `项目/2023年/2024年/同比`、每格有 polygon |
| 网格自证 | `(rows, cols) == (5, 4)`，`max_error ≤ 4.0` |
| 打乱两个 box | `GEOMETRY_MISMATCH`，**一格都不附加** |
| 少一个 box | `GEOMETRY_COUNT_MISMATCH` |
| 单格位移 40 px | 判 `grid`（更强的拒绝：连比较前提都不成立） |
| 默认模式 | 仍 `UNAVAILABLE` + `CELL_BOX_ORDER_UNVERIFIED` |

| 变异 | 结果 |
|---|---|
| 几何校验恒真 | 1 failed：`assert 'VERIFIED_BY_GEOMETRY' == 'GEOMETRY_MISMATCH'` |
| 默认改成 `DOCUMENTED` | 1 failed：`assert 'DOCUMENTED_ORDER' == 'UNAVAILABLE'` |

还原后 md5 回 `6521ecea…`，两条用例复绿。

## 5. 过程中我又踩了一次"验证动作"的坑

第一次做变异时，**实现还没提交**，`git checkout --` 把变异和实现一起回滚了，
接着用例全变 collection error。

顺序必须是：**先提交被测改动 → 再变异 → 再还原**。
`git checkout` 只认提交，不认"我刚才写的"。

这是本轮的第五个"验证动作本身出错"实例。已改成本次做法：先 `fc055d6` 提交，再变异。

## 6. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L38..HEAD` **3 个文件**（适配器、测试、夹具） |
| B | **1433 passed / 131 skipped**（1427 + 6） |
| C | `passed=true` |

## 7. 边界

- 本 Loop 只证明"box 与 HTML 网格一致时可信、不一致时拒绝"，**不证明** `pred_html` 的
  `rowspan/colspan` 解析在真实合并单元格上正确——当前夹具是 5×4 无合并；
- 真实扫描件的坐标质量、倾斜/印章遮挡，仍需 `OI04` 语料实测；
- 适配层仍未被 `pipeline` 调用（`PaddleTableResult` 尚未接 `assemble_package`）。
