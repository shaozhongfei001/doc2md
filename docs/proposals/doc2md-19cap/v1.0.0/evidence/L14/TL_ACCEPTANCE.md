# L14 验收记录（T1401–T1404，孤儿脚注归属）

- 入口：`loop-entry/L14` = `6581805`
- 代码：`c60aefe`
- 合并：`--ff-only` → `main` = `c60aefe`
- **状态：`CODE_MERGED` + `L13-FIND-01 CLOSED` + `L14-FIND-02 OPEN` + `INDEPENDENT_QA_PENDING`**

## 1. 关闭的是什么

`L13-FIND-01`：`pipeline._note_candidates_for` 把**无目标脚注**追加到每一张表，
一条脚注被计成 N 条，同一条件在 N 个复核包里各出现一次。
其 docstring 写的是"attached to the first table"——**代码与自述不符**。

## 2. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间**恰好 3 个文件**（pipeline.py + 2 测试），无未跟踪 |
| B | **1356 passed / 133 skipped**（L13 后 1354，+2） |
| C | `passed=true` |
| GAP 关闭判据 | 真实 docx（2 表 + 1 孤儿脚注）实测：`stats.notes==1`、每表 `[1,0]`、仍 `NEEDS_REVIEW` |

## 3. 修法与证据

```diff
-def _note_candidates_for(table_id, candidates)
+def _note_candidates_for(table_index, table_id, candidates)
         if not targets:
-            owned.append(dict(candidate))
+            if table_index == 0:
+                owned.append(dict(candidate))
```

实测（2 表 + 1 孤儿脚注）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `stats["notes"]` | 2 | **1** |
| 每表 notes | `[1, 1]` | **`[1, 0]`** |
| note 状态 | — | `NEEDS_REVIEW`（保留，未丢弃） |
| 首表 packet 含该脚注 | 是 | 是 |
| 次表 packet 含该脚注 | **是**（重复） | **否** |

关键取舍：**孤儿脚注既不能丢、也不能复制**。丢 = 缺脚注变成没有脚注；复制 = 一个条件读成 N 个条件。
正确位置是"归首表、标未解决、只出现一次"。

## 4. correction 侧去重退化为防御

L13 在 `correction.py` 里做过一次文档级去重（`DUPLICATE_NOTE_ID_DEDUPED`）。
上游修好后正常流程不再触发，因此把用例改成两条：

| 用例 | 断言 |
|---|---|
| 正常流程 | **不应**出现 `DUPLICATE_NOTE_ID_DEDUPED` |
| 人为注入重复 | 防御仍生效：`note_id` 唯一，且**必须**报出被去重的 id |

保留而非删除的理由：防御代码不写的代价是下次同类回归无人拦截；但"防御触发"和"正常"必须可区分，
否则等于把上游缺陷永久掩盖。

## 5. 新发现（探针）

**`L14-FIND-02`**：**无表格文档**里的脚注。

```
文档只有正文 + 1 条脚注（正文里有引用）→ tables=0, notes=0
                                      → issues=1: NOTE_WITHOUT_REFERENCE(note_ref_id="9")
```

脚注**文本不进任何产物**，只剩一条 issue 提到它的 `ref_id`。当前 schema 的 notes 挂在 table 上，
没有文档级安置位，所以这不是"漏写一行代码"，而是**契约缺位**。

未在本 Loop 修（会牵动契约层，超白名单）。登记待决策：扩展文档级 notes 安置位，还是明确拒绝此类文档。

## 6. 结论

`L13-FIND-01` **关闭**：一条孤儿脚注 = 一条 note，位置确定、状态可见。

对外表述：**"表格脚注的归属与计数在 assemble 与 correction 两侧口径一致"**。
不能说"脚注全支持"——`L14-FIND-02`（无表文档）仍在。
