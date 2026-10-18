# L20 验收记录（续表表名归一化与信号强弱）

- 入口：`loop-entry/L20` = `cd3fa16`
- 代码：`0b39db2`
- **状态：`CODE_MERGED` + 假拒绝修正 + 待独立 QA 复验**

## 1. 来源：第四轮抽检的假拒绝排查

`qa-l20` 的 8 个变异 **8/8 CAPTURED、0 间接捕获**。但它按我要求做的**反向追问**（守卫会不会误杀合法输入）发现了真实缺陷：

```
表1 存款余额  vs  表1 存款余额        -> JOIN_CANDIDATE
表1 存款余额  vs  续表1 存款余额      -> SEPARATE（不可复核）
列定义变化（更强的反证）              -> NEEDS_REVIEW（可复核）
```

两条问题：

1. **中文长表续页标"续表1 …"极常见**，而它被判成"确定不是同一张表"，且不可复核；
2. **信号强弱倒置**——最弱的证据（表名差两个字）拿到最硬判决，最强的证据（列定义变了）反而可复核。

## 2. 修法

| 改动 | 内容 |
|---|---|
| 表名归一化 | 比较前剥离续页词缀（`续表`/`续前`/`（续）`/`(续)`）；`续表` 保留其 `表`，故 `续表1 X == 表1 X` |
| 信号分级 | `ComparisonReason` 增加 `severity`（`HARD`/`SOFT`）；表名真冲突 = `SOFT` → `NEEDS_REVIEW`，与 `COLUMN_PATH_CHANGED` 同级 |
| 数据冲突不变 | 币种/单位/期间仍是 `HARD` → `SEPARATE`（那是数据，不是标签） |
| 硬优先 | `SOFT` 不得软化 `HARD`：表名不同 + 币种不同 → 仍 `SEPARATE` |

## 3. TL 探针抓到的一处新误伤（修复过程中）

第一版归一化把裸 `续` 也当词缀，于是：

```
_normalise_caption("续贷余额") -> "贷余额"
```

`续贷`（rolled-over loan）是**业务词**，不是续页标记。已改为只剥离多字符词缀，并加断言：

```python
assert _normalise_caption("持续经营说明") == "持续经营说明"
assert _normalise_caption("续贷余额") == "续贷余额"
```

同一次探针还抓到：软理由（表名）与硬理由（币种）同时存在时，`elif` 顺序让软理由先命中，导致 `SEPARATE` 被降成 `NEEDS_REVIEW`。已改为 `HARD` 优先分组判断，并补用例。

> 这两处都不是 qa-l20 指出的，是**修它指出的问题时新引入的**——说明"修一个假拒绝"本身也会造出新的假拒绝。

## 4. 用例

| 用例 | 断言 |
|---|---|
| `test_data_conflicts_are_separate` | 币种/单位/期间仍 `SEPARATE`（原 caption 参数已移出） |
| `test_a_real_caption_mismatch_is_reviewable_not_separate` | 真不同表名 → `NEEDS_REVIEW`，`CAPTION_CONFLICT.severity == "SOFT"` |
| `test_a_continuation_marker_is_the_same_label`（3 变体） | `续表1 …` / `（续）表1 …` / `表1 …（续）` → `JOIN_CANDIDATE` + `CAPTION_CONTINUATION_MARKER` |
| `test_a_different_label_still_blocks_a_join` | 归一化不得变成"所有表名都相同" |
| `test_normalisation_does_not_mangle_a_caption_that_merely_contains_the_character` | 探针：内容里的 `续` 不被剥离 |
| `test_a_hard_conflict_still_wins_over_a_soft_one` | 探针：硬冲突优先 |

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间**恰好 2 个文件** |
| B | **1379 passed / 133 skipped**（1373 + 6） |
| C | `passed=true` |

**更正**：提交信息 `0b39db2` 里写的 "1377" 是笔误，实测为 **1379**。以本文件与门禁记录为准。

## 6. 已知限制

| 编号 | 内容 |
|---|---|
| `L20-LIM-01` | 归一化只处理 4 种中文词缀，英文 `(cont'd)` / `Table 1 (continued)` 未覆盖；需要真实语料确认词表 |
| `L20-LIM-02` | 表名真冲突降为 `NEEDS_REVIEW` 后，若上游没有人工复核环节，这类表会停在候选而非分离——**决策权交给复核者**，这是有意的，但依赖 L07/L08 的复核流程真的存在 |

`L20-LIM-02` 值得强调：降级不是"更宽松"，而是"把决定交给该决定的人"。若复核环节不存在，它反而会积压。

## 7. 结论

`qa-l20` 指出的假拒绝已修正，且修复过程中由探针发现并修掉了两个新引入的误伤。

仍待验：`qa-l20` 报告里另一条更深的发现——**`omission_evidence_ids ⊆ 候选证据` 的校验在真实装配路径上恒真**（`_evidence_candidates` 用同一批 id 造出"已知集"）。这一条属于"守卫空转"，需要独立 Loop 处理，未在本 Loop 夹带。


## 8. 勘误（2026-10-18，由 qa-l20 复验触发；修正见 L21）

### 8.1 "数据冲突必须仍为 SEPARATE" 说得太绝对

本文件第 2、4 节写的是无条件表述。实测反例：

```
币种 CNY -> USD  且  期间 一侧有值/另一侧 None
-> NEEDS_REVIEW（CURRENCY_CONFLICT/HARD + PERIOD_UNKNOWN/HARD）
```

原因：decision 链里 `*_UNKNOWN` 分支排在 `hard` 之前，任何**单侧缺失**都会把确定的数据冲突降为可复核。
这是既有顺序、本次未引入，且方向保守（不会静默放行），但表述必须改成：

> **无单侧未知时**，数据冲突 = `SEPARATE`；**存在单侧未知时** = `NEEDS_REVIEW`（未知不是匹配）。

### 8.2 "持续经营说明会被 replace 毁掉" 是错误类比

归一化只剥**前缀/后缀**，而"持续经营说明"以"持"开头、以"明"结尾，任何前后缀实现都不会碰它。
**那条断言在任何实现下都恒真**，不承重。真正会被误伤的是**以"续"开头**的业务词：
`续贷余额`、`续期存款`、`续保率`、`续存`。

L21 已把断言换成这些输入，并实测：把裸 `续` 加回前缀后，用例红在
`assert '期存款' == '续期存款'`。

### 8.3 空白差异被叫作"续页标记"

`表1 贷款余额` vs `表1贷款余额` 判 `JOIN_CANDIDATE` 是对的（内部空白不该参与表名比较），
但日志写"differ only by a continuation marker"是错的陈述。L21 已拆成独立 code
`CAPTION_WHITESPACE_ONLY`，并把非阻断理由的 `severity` 标为 `INFO`（原来默认落 `HARD`，
非阻断却标硬，读日志容易误判）。

### 8.4 契约文本与实现矛盾（P1，最需要修）

`L20_loop_addendum.json` 的 T2001 原文写了"剥离 `续表/续前/续/（续）/(续)`"，**含裸 `续`**；
而实现只剥多字符词缀。照契约字面实现，会精确复现刚修掉的"续贷余额 → 贷余额"。

契约是给下一轮的人与 agent 读的——**文档写错会制造下一个缺陷**。L21 已更正该文本。
