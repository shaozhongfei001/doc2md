# L09 验收记录（T901–T904，生产者接线）

- 工作流：`DOC2MD-19CAP` / 修订 `DOC2MD-19CAP-V1.0.1-ADDENDUM-L09`
- 依据：`ADR-19CAP-02`（Owner 采纳方案 B）
- 入口：`loop-entry/L09` = `edce7b0`
- 代码：`ac9f25b`（T901–T904）
- 合并：`--ff-only` → `main` = `ac9f25b`
- **状态：`CODE_MERGED` + `INDEPENDENT_QA_PENDING`（本 Loop 未做独立 QA）**

## 1. 门禁：A1 官方工具不可用，改用等价自检

```text
$ check_loop_diff.py --repo . --loop L09 --base-commit edce7b0…
check_loop_diff.py: error: unknown Loop
```

原包工具的 Loop 列表来自 `PACK/loop/loop_contract.json`，L09 不在其中。
**没有修改 PACK，也没有扩原白名单来制造通过。**

等价自检（`ADR-19CAP-02` §6.1 已登记口径）：

| 检查 | 结果 |
|---|---|
| `git diff --name-only edce7b0..HEAD` | 恰好 4 个文件，全部在白名单 |
| `git ls-files --others --exclude-standard` | 空 |
| 白名单比对脚本 | `outside whitelist: none`，`PASS` |

| 门 | 结果 |
|---|---|
| B 全量 | **1303 passed / 133 skipped**（L08 后 1279，新增 24） |
| C 受保护 | `check_baseline --allow-index-append` → `passed=true`，`errors=[]` |

**这是等价自检，不等于原包工具通过**——原包对 L09 根本没有这条门。

## 2. 交付内容

| 任务 | 文件 | 关键约束 |
|---|---|---|
| T901 表头带 | `word/table_flags.py` | 只认 `w:trPr/w:tblHeader`；无标记 → `header_rows=()` + `NO_NATIVE_MARK`，**不猜首行** |
| T902 列宽/页码 | 同上 | 列宽取 `w:tblGrid/w:gridCol@w:w` 归一化为比例（不同缩放可比）；页码**只取** `w:lastRenderedPageBreak` 缓存，无缓存即 `None`，不当作 page 0 |
| T903 脚注文本 | `word/notes_extract.py` | 原文仅去首尾空白；跳过 separator；引用回指 cell；无引用的脚注 → `UNRESOLVED` |
| T904 端到端 | 两个测试文件 | `extract_tables + flags → derive_header_paths` 产出非空路径；`read_notes → bind_notes` 产出 `CANDIDATE` 且 `publishable` |

## 3. 关闭了哪些缺口

| 缺口 | 状态 | 说明 |
|---|---|---|
| `L05-GAP-01` 表头带 | **已关闭** | `tblHeader` 已产出，端到端断言通过；无标记时仍全表 `NEEDS_REVIEW`（这是设计，不是缺陷） |
| `L05-GAP-03` 脚注文本 | **已关闭** | 脚注/尾注原文与引用已产出，`bind_notes` 可绑定并发布 |
| `L05-GAP-02` 列宽/页码 | **部分关闭** | 列宽已产出；**页码依赖 Word 的 `lastRenderedPageBreak` 缓存**，无缓存即 `None`。`propose_continuation` 在无页码时按设计 `NEEDS_REVIEW`，所以续表判断对"无缓存 DOCX"仍不可达 |

新增两项限制（如实登记）：

| 编号 | 限制 | 影响 |
|---|---|---|
| `L09-LIM-01` | 页码为 Word **保存时缓存**，可能过期 | 续页候选可能基于过期分页；已在 `page_source` 标注来源，调用方须决定是否信任 |
| `L09-LIM-02` | 脚注锚点只走**顶层表**；嵌套表内的引用不产出 cell id | 该脚注降为 `UNRESOLVED`，不硬套到父表 |

## 4. 过程中修正的一个实现错误（记录）

页码映射最初用 `id(element)` 作键，实测**恒为 `None`**：lxml 在不同遍历中可能给出不同的代理对象，
`id()` 不稳定。改为 `tree.getpath(element)` 作键后才正确。

这条正是"看起来实现了、其实一直返回 None"的类型——如果只跑正向用例（"有 break"用例恰好也返回 None），
会被误当成"页缓存不存在"。测试里同时钉了"有 break → 1"和"无 break → None"两个方向。

## 5. 未做

- 真实 Word 语料回归（无 gold 语料），`NOT_RUN`；
- 本 Loop 无独立 QA（`INDEPENDENT_QA_PENDING`）；
- 未改动 L03/L04/L05 任何公开签名（白名单外零改动，A1 等价自检可证）。

## 6. 结论

三条 gap 中 **2 条关闭、1 条部分关闭**（页码受 Word 缓存限制）。
`PI-F04-06`（表头）与 `PI-F04-08`（脚注）从 `CODE_PARTIAL` 可上调为"端到端可达"；
`PI-F04-07`（续表）仍受 `L09-LIM-01` 限制，保持部分完成。
