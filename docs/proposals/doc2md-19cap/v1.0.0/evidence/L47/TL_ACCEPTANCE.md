# L47 验收记录（作废标记流水：append-only）

- 入口：`loop-entry/L47` = `5f3ab59`
- 代码：`02831b2`
- **状态：`CODE_MERGED`；`L46-LIM-01`（未落盘）以"标记流水"形式关闭**

## 1. 你的裁定怎么落成了代码

| 你的话 | 代码里的对应 |
|---|---|
| "作废只是一个标签" | 标签 = 一条 mark，不产生新 generation，版本号不动 |
| "generation 对应作废记录增加标记" | mark 写在**该 generation 自己的** `revocations.jsonl` 上 |
| "B 会造成维护混乱" | 不建第二本账；当前状态 = 对标记流的 **折叠**，不是另存一份 |

关键设计：**日志是事实，状态是推导**。

```
revocations.jsonl         （只追加，永不改写）
  seq1 REVOKE  EV-2  by E12345 @ 10:00  prev=ACCEPTED/RV-OLD
  seq2 RESTORE EV-2  by E12345 @ 11:00
  seq3 REVOKE  EV-2  by E12345 @ 12:00
        │
        └── fold ──> EV-2 = STALE（最后一次标记胜出）
```

没有"当前状态"字段可被改坏，也没有两份数据能对不上。

## 2. 四条判据

| 判据 | 用例 | 实测 |
|---|---|---|
| revoke 后折叠为 STALE | `test_revoke_appends_one_mark_and_folds_to_stale` | 通过 |
| restore 回到原状态与原 ref | `test_restore_returns_to_the_recorded_state_and_ref` | `ACCEPTED` + `RV-OLD` |
| 重复同向不写第二行 | `test_a_repeated_action_does_not_write_a_second_line` | 第二次返回 `None`，仍 1 行 |
| 双进程并发 seq 无重无丢 | `test_two_processes_appending_get_unique_sequences` | **2 进程 × 25 = 50 条，seq 1..50 连续** |

另四条：seq 稠密、未知 action 先拒后写、**断行拒绝而不是跳过**、折叠三次后状态正确。

## 3. 变异验证

| 变异 | 结果 |
|---|---|
| seq 恒为 1 | 1 failed（稠密性） |
| 重复标记照写 | 1 failed（幂等） |
| 恢复忽略原 ref | 1 failed（`RV-OLD` 丢失） |

还原后 md5 回 `c996f6a7…`，8 passed。

（M3 第一次因工作目录错没落盘、输出为空；已改为在 `backend/` 下重做。
这是本长程第三次同类失误，规则已在 L42 落过：**先证动作发生，再看结果**。）

## 4. 一个必须承认的代价：generation 目录不再是"整目录不可变"

原 `artifact_store` 的保证是"目录写成就不可变"。现在同一目录下多了一个**只追加**的文件，
所以准确表述是：

> **payload 不可变；作废标记只追加、不改写。**

这不是破坏原子性（append + `fsync` 仍是原子的单行写入），但它确实改变了"目录只读"的直觉。
若将来要求目录严格只读，改成 `<generation>/revocations/` 子目录、或者 `objects/…-revocations/` 平级目录都可以，**日志格式不用变**。

## 5. 边界

| 编号 | 内容 |
|---|---|
| `L47-LIM-01` | 日志只增不减：没有压缩/归档策略，长期会变长 |
| `L47-LIM-02` | 文件级权限未做：谁能 append 取决于进程，不取决于 reviewer 字段 |
| `L47-LIM-03` | 未接入 `artifact_store` 的锁体系（用的是单独 `flock`），两者并发语义尚未统一验证 |
| `L47-LIM-04` | 折叠是 O(标记数)；当前量级无影响，未做索引 |

## 6. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L47..HEAD` **2 个文件**（新模块 + 测试） |
| B | **1469 passed / 131 skipped**（1461 + 8） |
| C | `passed=true` |
