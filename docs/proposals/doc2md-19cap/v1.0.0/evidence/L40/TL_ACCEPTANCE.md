# L40 验收记录（证据作废执行器）

- 入口：`loop-entry/L40` = `0063696`
- 代码：`212c6bb`
- **状态：`CODE_MERGED` + `L35-BOUND-01` 的"能算出"升级为"能落状态"（仍需人工触发）**

## 1. 执行器补上了 L35 缺的那一半

L35 产出 `stale_candidate_evidence`，仓内**没有任何消费方**。本 Loop 加的是写入器，
但把它做成**纯函数**：

| 属性 | 含义 |
|---|---|
| 不落盘 | 返回新列表，持久化交给调用方 / `artifact_store` |
| 不读时钟 | `at` 由调用方传入——函数内部取时间会让重放不可复现 |
| 不自动调用 | 只有显式调用才作废，`assemble` / `apply_correction` 不会顺手触发 |

## 2. 四条判据

| 判据 | 用例 | 实测 |
|---|---|---|
| 作废后 `state=STALE` 且写 `review_decision_ref` | `test_revoking_sets_the_state_and_the_decision` | `STALE` + `RV-1` + `revoked_by` + `revoked_at` |
| 重复作废幂等 | `test_revoking_twice_is_idempotent_and_reported` | 第二次 `revoked=()`、`already_revoked=("EV-1",)`、**列表逐字段相等**（不改时间戳） |
| 未知 id 必须可见 | `test_an_unknown_id_is_reported_not_swallowed` | `unknown=("EV-TYPO",)`，其余照常作废 |
| 非法条目整批拒绝 | `test_a_malformed_batch_is_rejected_whole` | `E_INPUT_INVALID`，且**输入零改动** |

补四条：

| 用例 | 断言 |
|---|---|
| 重复 id 去重可见 | `duplicates=("EV-1",)` |
| 缺决策/原因 | `E_REVIEW_REQUIRED` |
| 输入不被就地修改 | `before == snapshot` 且 `updated[0] is not before[0]` |
| 策略拒绝 | `RESTRICTED` + `---` 被拒（复用 L39 的同一套） |

## 3. 为什么"整批拒绝"而不是"跳过坏的"

半作废的证据比没作废更危险：**剩下那一半仍然读作当前**，而报告已经说"作废完成"。
所以所有条目先校验形状，任何一个不合法就整批抛错，一个都不改。

同理，未知 id 不做静默跳过——**拼错一个 id 不应该看起来像成功**。

## 4. 变异验证

| 变异 | 结果 |
|---|---|
| 未知 id 静默（删 `unknown.append`） | 1 failed |
| 幂等判断失效（`if False:`） | 1 failed |

还原后 md5 回 `47bbd0ec…`，`test_revocation.py` **8 passed**。

（第一次跑 M1 时工作目录错，补丁没落盘、输出为空；改到 `backend/` 下重做才得到上面的红。
这与 L38 那次"未提交就 checkout"是同一族问题：**先确认动作真的发生了，再看结果**。）

## 5. 现在"纠错后证据会被作废"能说到哪一步

可以说：

> 可以把 L35 算出的应作废清单**人工触发**落成 `STALE`，带决策、执行人、时间与原因，重复执行安全。

**不能说**：

- "纠错后证据自动作废"——执行器不自动调用；
- "作废已端到端验证"——仍没有调用方把它接到流水线上，本 Loop 只验证执行器本身。

## 6. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L40..HEAD` **2 个文件**（新模块 + 测试），无未跟踪 |
| B | **1449 passed / 131 skipped**（1441 + 8） |
| C | `passed=true` |

## 7. 剩余

| 编号 | 内容 |
|---|---|
| `L40-LIM-01` | 未接入 `pipeline`/`correction`，仍是"可调用"而非"默认行为" |
| `L40-LIM-02` | 未定义跨进程并发语义（两人同时作废同一批） |
| `L40-LIM-03` | 作废不可撤销：没有任何"恢复"路径 |
