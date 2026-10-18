# L22 验收记录（契约一致性与 severity 三档）

- 入口：`loop-entry/L22` = `472bb50`
- 代码：`cc6f5fb`（合入后）
- **状态：`CODE_MERGED` + P2 完全落实 + severity 三档统一**

## 1. 来源

`qa-l20` 复验 L21 时判定 **P2 只落实 1/3**：

| 位置 | 原文 | 问题 |
|---|---|---|
| `GAP:30` | 已按"有无单侧未知"拆开 | ✅ 已改 |
| `scope_note:17` | "…币种/单位/期间等数据冲突仍为 SEPARATE" | ❌ 无条件表述 |
| `T2003:74` | "…币种/单位/期间仍 SEPARATE" | ❌ 无条件表述 |

结果同一份契约里，`GAP` 说分两种情况、`scope_note` 与 `T2003` 说一律 `SEPARATE`——**契约内部自相矛盾**。

它还指出两处同型残留：`HEADER_REPEATED` 与 `NEXT_PAGE_HEADERLESS` 都是**非阻断**理由，却落默认 `severity="HARD"`，日志读起来像"它们阻断了合并"。

## 2. 处置

| 项 | 改动 |
|---|---|
| 契约同步 | `scope_note` 与 `T2003` 改为"无单侧未知时 SEPARATE；存在单侧未知时 NEEDS_REVIEW（未知不是匹配）" |
| severity 三档 | `HEADER_REPEATED`、`NEXT_PAGE_HEADERLESS` 补 `severity="INFO"` |
| 注释 | `severity` 注释补齐 `HARD/SOFT/INFO` 三档语义，并写明"不阻断的理由必须标明自己不阻断" |
| 不变式断言 | `test_every_non_blocking_reason_says_so_in_its_severity`：**任何非阻断理由的 severity 必须为 INFO** |

## 3. 变异验证（红在目标断言）

| 变异 | 结果 |
|---|---|
| 去掉 `HEADER_REPEATED` 的 `severity="INFO"` | `:391` 红：`[('NEXT_PAGE_HEADERLESS', 'INFO'), ('HEADER_REPEATED', 'HARD')]` |

用通用断言（覆盖所有非阻断理由）而不是逐条断言，是为了让**将来新增的非阻断理由**也自动受约束——否则每加一个理由就得记得补一条测试。

## 4. 一次重复犯的错，这次按自己立的规则避开了

上一步（L21）我因为"变异还原把未提交改动吃掉"出过一次事故。这轮第一次做变异验证时**又踩了一次**——`git checkout -- continuation.py` 把刚写的 `INFO` 改动回滚，还原后测试反而红。

处理方式改了顺序，且这次有效：

```
先 commit → 再做变异 → 还原（checkout 回到已提交版本）→ 全量
```

上一轮立的规则是"跑变异前产品改动必须已提交"。**规则写在证据里没用，必须改变动作顺序**——这轮真正生效的是顺序变更，不是那条文字。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 3 个文件（continuation.py、test_continuation.py、L20 addendum），无越界 |
| B | **1382 passed / 133 skipped**（1381 + 1） |
| C | `passed=true` |

## 6. 结论

P2 **完全落实**，契约内部不再矛盾；`severity` 三档在代码、注释、契约三处一致。

判决方向未变（37 个续表用例全绿，含 `SEPARATE`/`NEEDS_REVIEW`/`JOIN_CANDIDATE` 三种结果的回归）。

## 7. 仍未处理

`qa-l20` 的 4(e)：`omission ⊆ 候选证据` 在真实装配路径上恒真。下一 Loop（L23）处理，用它给的正例 + 反例两条用例。
