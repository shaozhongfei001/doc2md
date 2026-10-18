# L23 验收记录（候选证据已知集去空转）

- 入口：`loop-entry/L23` = `c225f67`
- 代码：`688f23c` + `cd181bc`
- **状态：`CODE_MERGED` + GAP-06 空转消除 + 待独立 QA 复验**

## 1. 关闭的是什么

`qa-l20` 第 4(e) 节：`omission_evidence_ids ⊆ evidence[].candidate_evidence_id` 这条根契约断言，
在真实装配路径上**恒真**。

原因不是断言写错，是"已知集"自造：

```python
# 旧实现
ids = table["omission_evidence_ids"] + 本表注释的 evidence_ids
return ids → 作为 evidence_candidates 传给 build_review_packet
# build_review_packet 再校验 omission ⊆ 这批 ids
```

**用被校验的同一批 id 造"已知集"，子集关系必然成立。** 表声明一个文档里根本不存在的证据 id，也能通过。

## 2. 修法

| 侧 | 改动 |
|---|---|
| `pipeline._evidence_candidates(notes)` | 只取**文档级**证据（全部注释抽取出的 evidence_ids），**不再并入本表 omission ids** |
| `correction._evidence_candidates(notes)` | 同口径，以 `assembly.notes`（文档级）为已知集 |

于是"表声明一个文档没登记过的 omission id"从必然通过变成必然拒绝。

## 3. 成对用例（按 QA 要求，只测拒绝不算）

| 用例 | 断言 |
|---|---|
| `test_an_omission_evidence_id_that_no_note_registered_is_refused` | `E_REVIEW_PACKET_INVALID`，且 **`detail.missing == ["EV-GHOST"]`**（精确相等，不是"含"） |
| `test_an_omission_evidence_id_registered_by_a_note_is_accepted` | 该 id 由文档内注释登记 → **必须通过**，且 `payload["omission_evidence_ids"]` 不回撤 |
| `test_a_correction_refuses_an_omission_evidence_id_no_note_registered` | 纠错侧同一空转（第二份实现）也被钉住 |

只写前两条中的第一条会重演"否定式断言恒真"：把实现改成"一律拒绝"，第一条仍绿。

## 4. 变异验证（先提交，后变异）

| 变异 | 结果 |
|---|---|
| 把 `EV-GHOST` 硬编码回已知集（模拟旧行为） | `:458` **`DID NOT RAISE`** 红 |

还原后全绿。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 4 个文件（2 产品 + 2 测试），无越界 |
| B | **1385 passed / 133 skipped**（1382 + 3） |
| C | `passed=true` |

## 6. 影响面（需要说明）

这是**行为变更**，不是纯加固：

- 以前"表引用任意 omission id"都能装配成功 → 现在会被拒绝；
- 合法前提变成：该 id 必须由文档内某条注释的抽取结果登记。

在 Word 路径上，`omission_evidence_ids` 目前由 flags/tables 提供，多数文档为空，所以实际影响面小；
但如果有上游直接把 omission id 塞进表而不登记证据，**现在会失败**——这正是本 Loop 的目的。

## 7. 仍未处理

`qa-l20` 第 5 节提的结构化升级：把"非阻断 → INFO"从断言下沉到 `ComparisonReason.__post_init__`，
使将来的新构造点**不必记得写测试**也能满足不变式。已登记为 L24（小改动，含新构造点用例）。

## 8. QA 复验结论：**QA_REJECT**（2026-10-18，由 qa-l21 判定）

独立 QA 判定本 Loop **验收不通过**，两条理由都成立：

### 8.1 BLOCKER：修过头，拒掉了合法表格

本文件第 2 节把"文档级证据"实现成了"**注释**声明的证据"。但生产路径上 `omission_evidence_ids`
并不来自注释——它由 `word/native_tables.py:492` 从 OOXML `w:trPr/w:gridAfter` 生成为
`docx:gridAfter:{row}:{col}`。

后果：**任何含 `w:gridBefore/gridAfter` 的 Word 表都无法装配**（`E_REVIEW_PACKET_INVALID`，
`detail.missing=['docx:gridAfter:1:2']`），纠错路径同受影响。

QA 的最小复现（3 列 tblGrid + 第 2 行 2 个 `w:tc` + `gridAfter=1`、无脚注）：

| 提交 | 结果 |
|---|---|
| `c225f67`（L23 前） | ASSEMBLED，omission = `['docx:gridAfter:1:2']` |
| `cd181bc`（L23） | **REFUSED** |

而 `native_tables.py:17` 明确写着 gridBefore/gridAfter 是**唯一接受的空洞形态**。
即：我把"合法且唯一的空洞形态"判成了非法。

### 8.2 pipeline 侧负例是白盒，行为回退时仍绿

QA 的 M1a 变异（把表自身 omission 并回已知集，等价旧行为）**三条用例全绿**。
因为该用例自己调 `_evidence_candidates(notes)` 再造 `build_review_packet`，
钉住的是 `review_packet` 的守卫，而不是它 docstring 声称的"已知集不再含表自身 omission"。

同一变异下纠错侧用例**红了**（那是端到端 `apply_correction`）。

### 8.3 处置

由 **L25** 修复：已知集 = 注释证据 ∪ **由 cells/n_cols 独立重算的结构证据**，
表声明的不可重算 id 仍被拒。并在 L25 中补了函数级断言（结构在、幽灵不在），消除白盒缺口。

> 教训：把"文档里登记过的证据"实现成"某一个来源登记过的证据"，
> 就是对合法输入的一次窄化。**收窄守卫的输入集合，等于放宽守卫的拒绝面。**
