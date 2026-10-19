# L50 验收记录（OCR 装配入口：空值 + 显式 issue）

- 入口：`loop-entry/L50`
- 代码：`e0be4f3`
- **状态：`CODE_MERGED`；`E2E-FIND-01` 的第三处（入口类型）有了 OCR 侧对应物**

## 1. 做法：不假装同构

`assemble_package` 从 `DocxPackage` 出发，因为原生表带着 OCR 没有的东西：
`w:tblHeader`、脚注锚点、来自 XML 网格的 `slots`。

`assemble_from_ocr` 不伪装成 `DocxPackage`，而是**只交付它真有的**：

| 字段 | OCR 侧 | 处理 |
|---|---|---|
| `cells` / `slots` / `n_rows` / `n_cols` | 有 | 直接给（L49 推导） |
| `header_rows` | **人声明** | 进 `stats.header_rows_by_table` |
| `notes` | **没有** | 空 + `NOTES_UNAVAILABLE_FROM_OCR` |
| `omission_evidence_ids` | **没算** | 空 + `OMISSION_UNAVAILABLE_FROM_OCR` |
| 候选证据 | **没有** | 文档级 `EVIDENCE_UNAVAILABLE_FROM_OCR` |

第二列和第三列必须一起出现。空列表本身是一个**陈述**：

> `notes = []` 读作"这张表没有脚注"；
> `notes = [] + NOTES_UNAVAILABLE_FROM_OCR` 才读作"我们没读到脚注"。

这两件事在业务上完全不同——前者可以签字，后者不行。

## 2. 三条拒绝

| 情形 | 处理 | 为什么 |
|---|---|---|
| 空表列表 | `E_INPUT_INVALID` | "空文档"与"识别失败"会长得一模一样 |
| 传入裸 dict | 拒绝，要求 `OcrTable` 实例 | 否则**未声明表头**的表能混进来 |
| `generation_id` 空 / `source_sha256` 非 64 位 | 拒绝 | 产物必须可归因到具体输入 |

第二条是这轮最容易写松的地方：`tables: Sequence[OcrTable]` 只是类型注解，运行时若照单全收，
L49 的"必须人工声明"就作废了。

## 3. 测试（6 条）

| 用例 | 断言 |
|---|---|
| 真 fixture 进契约形状 | 5×4、slots 5 行、cells 20、`header_rows_by_table={"T-REAL":[0]}` |
| 空 notes 必带 issue | `NOTES_UNAVAILABLE_FROM_OCR` 存在，`notes_unavailable=True` |
| omission/evidence 声明缺失 | 两个 issue 都在，且 `omission_evidence_ids` 为空 |
| 空表被拒 | 消息含 "empty document" |
| 裸 dict 被拒 | 消息含 "declared header is not optional" |
| generation/source 透传 | `to_mapping()["stats"]["tables"] == 1` |

## 4. 变异

| 变异 | 结果 |
|---|---|
| 把 NOTES issue 的 code 改掉 | 1 failed |
| 允许空表 | 1 failed |

还原后 md5 回 `34c2acb1…`，6 passed。

（M1 第一次我把 `issues.append` 改成了 `extend` 且括号不配，导致语法错误、输出为空；
改为一处字符串替换后才是干净的红。这是"先证动作发生"的又一次应用。）

## 5. 现状：能力到哪、没到哪

**到了**：

```
PDF/图片 → OCR → 表格结构 → 几何校验 → 人工声明表头 → 契约形状 tables + slots
                                                       + 显式"缺什么"的 issue
```

**没到**：

- 没有 `packets`（复核包需要 `omission ⊆ evidence`，而 OCR 侧没有 evidence）；
- `tables` 尚未经 `validate_root` 全量校验（本轮只断言形状与关键字段）；
- 没有订正链路：`apply_correction` 需要 `evidence_ids`，OCR 表为空。

所以准确表述是：

> **OCR 侧能产出"契约形状的表 + 明确的缺失声明"，还不能产出复核包。**

## 6. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L50..HEAD` **2 个文件**（新模块 + 测试） |
| B | **1487 passed / 131 skipped**（1481 + 6） |
| C | `passed=true` |
