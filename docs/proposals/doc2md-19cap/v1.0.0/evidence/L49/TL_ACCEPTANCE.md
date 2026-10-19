# L49 验收记录（OCR 表头：引擎建议，人工裁决）

- 入口：`loop-entry/L49`
- 代码：`cb35cd6`
- **状态：`CODE_MERGED`；`E2E-FIND-01` 的缺口的第二项（逐表 header_rows）有了来源**

## 1. 你的原话怎么落成代码

> A 人工声明，但需要视觉推断先准备证据，人类更像是最终裁决。

| 角色 | 实现 | 能否让表"可用" |
|---|---|---|
| 引擎（视觉证据） | `suggest_header_rows`：**只读 `<th>` 标记** | **不能** |
| 人（裁决） | `declare_header_rows`：必须带 `reviewer` + `review_decision_ref` | 只有它能 |
| 组装 | `build_ocr_table`：无声明直接 `E_REVIEW_REQUIRED` | — |

三条边界，都是防"建议变成决定"：

1. **建议只来自 `<th>`**——不是加粗、不是字号、不是"第一行看起来像"。
   后三者是 L05 明令禁止的猜表头带手法；用引擎自己的标记则不同：那是**引擎断言**，不是我们的推断。
2. **没有 `<th>` 就没有建议**（`rows=()`）。半行 `<th>` 也判为无建议——**半个表头带不是表头带**。
3. **人覆盖引擎不报错，但要留痕**：`HEADER_OVERRIDES_ENGINE` 同时记 `suggested` 与 `declared`。
   人有权改机器；但"改过"必须能被后来的人看见。

## 2. 声明为什么必须"从 0 起连续"

不是形式主义：`slots` 与后续语义推导都假设"表头带之上的每一列，由带内对应单元格代表"。
非前缀声明（如只标第 2 行）会产出一张**无法从任何一个带推导语义**的表。

所以拒绝三种：不从 0 起、不连续、把数据行全吃掉。

## 3. slots 是机械推导，不需要人

`to_contract_table` 由 `row/col/rowspan/colspan` 直接算出 `slots` 与 `n_rows/n_cols`。

这正好对应 QA 指出的第三处缺口：**它缺的是数据源，不是判断**。
`header_rows` 需要人（判断），`slots` 只需要 cells（结构）。

## 4. 九条用例

| 用例 | 断言 |
|---|---|
| th 行成为建议 | `rows == (0,)` |
| 无 th 时建议为空 | `available is False`（不猜首行） |
| 部分 th 不算表头带 | `rows == ()` |
| 无声明构建 | `E_REVIEW_REQUIRED` |
| 非前缀声明 | `E_INPUT_INVALID`（三种形状） |
| 表头吃掉全部行 | 报 "no data rows" |
| payload 含 slots/cells/source | 3×2 → slots 3 行、cells 6、`HUMAN_DECLARATION` |
| 人覆盖引擎 | 允许 + `HEADER_OVERRIDES_ENGINE` 记录两份答案 |
| **真实 3.7.0 fixture** | 5×4、slots 5 行、header (0,) |

## 5. 变异验证

| 变异 | 结果 |
|---|---|
| 无 th 时猜首行 | 1 failed |
| 无声明时用建议顶替 | 1 failed |
| 允许非前缀声明 | 1 failed |

还原后 md5 回 `1b7e7c97…`，9 passed。

## 6. 还没接上的（不修饰）

本轮只做了"表头来源"这一处。`E2E-FIND-01` 的三处缺口里：

| # | 缺口 | 状态 |
|---|---|---|
| 1 | `assemble_package` 只接 `DocxPackage` | **仍未解** |
| 2 | 逐表 `header_rows` 无来源 | **本轮解了（人工声明）** |
| 3 | payload 无 `slots` 直接拒绝 | **本轮解了（机械推导）** |

所以现在能产出**契约形状的 OCR 表**（cells + slots + header_rows + 来源），
但**还不能**进 `assemble_package`——入口类型那一道还在。

下一轮若要接，需要新增一条"OCR 装配入口"，复用 `build_final_view` 之外的所有步骤。
这属于新能力，不是修 bug，应单独立 Loop。

## 7. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L49..HEAD` **3 个文件**（新模块 + adapter + 测试） |
| B | **1481 passed / 131 skipped**（1472 + 9） |
| C | `passed=true` |
