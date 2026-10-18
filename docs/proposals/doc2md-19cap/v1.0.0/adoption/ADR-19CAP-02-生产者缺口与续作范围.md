# ADR-19CAP-02：三个上游生产者缺口与续作范围

- 状态：**ACCEPTED（Owner 已决定方案 B）**
- 提出：TL，2026-10-18
- 决定：Owner，2026-10-18 —— 采纳**方案 B**（新开 L09「生产者接线」，独立白名单，不改已有签名）；
  `QA-TEST-01` **单独立项**，不占用 018
- 关联：`L05-GAP-01`、`L05-GAP-02`、`L05-GAP-03`（见 `evidence/L05/TL_ACCEPTANCE.md` 第 4 节）
- 影响版本：会改变已封版 Loop 的白名单，属**契约版本修订**，不是路径调整

## 1. 背景：功能已测，但端到端不可达

L05 的四个函数（表头路径、续表候选、脚注绑定、复核包）实现与测试均已完成，A1/B/C 全绿，
但 grep 全树确认**三个输入无人产出**：

| 缺口 | 事实 | 后果 |
|---|---|---|
| `L05-GAP-01` | 全树未提取 `w:trPr/w:tblHeader`，`extract_tables` 不输出表头带标记 | `derive_header_paths` 拿不到 header band，按设计全表 `NEEDS_REVIEW` → 表头能力**实际不产出任何路径** |
| `L05-GAP-02` | 无 `page_index` / `column_widths` 生产者 | `propose_continuation` 只能被测试驱动，真实续页判断不可达 |
| `L05-GAP-03` | `final_view.py` 只把 `footnotes.xml` 当修订上下文，不产出脚注条目 | `bind_notes` 只能接受外部传入候选，真实脚注永不进入 |

这一类的危险不在"缺功能"，而在**测试全绿造成的错觉**：函数层 110 例通过，业务上一条路径也没跑通。

## 2. 三个候选方案

### 方案 A：直接在 L03 白名单内补（改已封版 Loop）

- 做法：`word/native_tables.py` 增加 `tblHeader` 输出；`word/final_view.py` 增加脚注条目抽取。
- 优点：改动小，接口就近。
- 缺点：**L03 已通过 QA 并封版**（入口 `07da949`，验收 `e69…`/`746…`）。
  事后改其白名单内容，等于让已发布的 A1 结论失效——需要重开 L03 的验收，而不是"顺手加两行"。
- 结论：**不推荐**。

### 方案 B：新开 L09「生产者接线」，独立白名单

- 做法：新增 Loop，白名单只含生产者接线所需文件：
  `backend/app/extensions/word/table_flags.py`（新）、`backend/app/extensions/word/notes_extract.py`（新）、
  `backend/app/extensions/adapters/*`（如列宽/页码来自引擎）、新增对应测试。
  **不改** `native_tables.py` / `final_view.py` 的公开签名，只新增只读产出函数并接线。
- 优点：不改已封版代码的语义；新 Loop 自己跑 A1/B/C；证据链清晰。
- 缺点：需要 Owner 确认"19 项之后继续开 Loop"是否在授权范围内。
- 结论：**推荐**。

### 方案 C：不补，交付时如实标注

- 做法：保持现状，在交接材料中写明"表头/续表/脚注需外部提供输入"。
- 优点：零风险，零新范围。
- 缺点：19 项里 3 项永久停在"函数可用、业务不可用"。
- 结论：可接受，但必须让 Owner 明确知情。

## 3. 推荐方案的实施边界（方案 B）

| 项 | 内容 |
|---|---|
| 新 Loop 编号 | **不占用 018**；沿用工作流标识 `DOC2MD-19CAP`，由项目规则决定正式编号 |
| 白名单 | 仅新增文件 + 对应测试；不修改 L03/L04/L05 已有文件的公开签名 |
| 不改的东西 | `parsed/`、`raw_vault/`、旧三件套、IR 1.0、`sql/**`、既有 322 个受保护文件 |
| 验收门 | 沿用 A1/B/C + 变异抽检；新增"端到端可达"判据：`extract_tables → derive_header_paths` 能产出非空路径 |
| 缺口关闭判据 | 三条 gap 各自新增一条**端到端断言**，而不是再加函数层单测 |

## 4. 与 017 的关系

本 ADR 不涉及 `specs/017-unified-document-ir`：不写 IR 1.0 新键、不改其状态与 R-01…R-18 裁定。
新增的生产者若需承载字段，一律放在扩展层产物中，**不回写 IR**。

## 5. 需要谁决定什么

| 决策 | 责任方 |
|---|---|
| 是否在 19 项之后继续开 L09（方案 B） | Owner |
| 若选 C，交接材料如何表述 | Owner + HO |
| 新 Loop 的正式编号 | 项目现有规则（TL 不自行占用 018） |

未获决定前，TL **不动任何 L03/L04 代码**，也不把这 3 项标为完成。

---

## 6. 决策记录（2026-10-18）

| 项 | 决定 |
|---|---|
| 方案 | **B：新开 L09「生产者接线」** |
| 白名单 | 仅新增文件 + 对应测试（见 `loop/proposals/doc2md-19cap/v1.0.1/L09_loop_addendum.json`） |
| 不改 | L03/L04/L05 已有文件的公开签名；`parsed/`、`raw_vault/`、旧三件套、IR 1.0、`sql/**`、322 个受保护文件 |
| `QA-TEST-01` | **单独立项**，不并入 L09，不占用 018 |
| 018 编号 | 仍未占用；L09 为工作流内工作编号 |

### 6.1 已核实的一个工具限制（必须记录）

原包门禁 `check_loop_diff.py` **无法校验 L09**：

```text
$ check_loop_diff.py --repo . --loop L09 --base-commit <40位>
check_loop_diff.py: error: unknown Loop
```

原因是该工具的 loop 列表来自 `PACK/loop/loop_contract.json`（`parents[1]` 指向 PACK），
而 L09 不在原包 9 个 Loop 之内。

**处置**：L09 的 A1 门禁用**等价自检**替代，并在证据里同时给出等价命令与其局限：

```bash
git -C <worktree> diff --name-only <entry>..HEAD   # 必须 ⊆ 白名单
git -C <worktree> ls-files --others --exclude-standard  # 必须为空
```

同时明确写：**这不等于原包工具通过**，原包工具对 L09 根本不存在。
不修改 PACK、不扩原白名单来"造出"一个 L09 通过。
