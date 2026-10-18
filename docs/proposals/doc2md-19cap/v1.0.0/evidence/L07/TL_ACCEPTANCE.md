# L07 验收记录（T701–T705）

- 工作流：`DOC2MD-19CAP` / 契约 `DOC2MD-19CAP-V1.0`（`DESIGN_CANDIDATE`）
- Loop：`L07 人工纠错、候选证据与依赖失效`
- 覆盖需求：`PI-F04-09`、`PI-F05-06`
- 入口 commit（`loop-entry/L07`）：`b71aae0`
- 代码：`a2d1d61`（T701–T704）
- 合并：`--ff-only` → `main` = `a2d1d61`
- **状态：`CODE_MERGED` + `INDEPENDENT_QA_PENDING`**

## 1. 派发情况

沿用 L06 结论：派发通道不稳定，**四个任务全部由 TL 实现**。本记录不把 TL 自写标为 DEV 交付。

## 2. 门禁实测

| 门 | 命令 | 结果 |
|---|---|---|
| A1 | `check_loop_diff --repo /tmp/d19/L07 --loop L07 --base b71aae0` | `passed=true`，`errors=[]`，10 个文件全在白名单 |
| B | `pytest -q` | **1248 passed / 133 skipped**（L06 后 1181，新增 67） |
| C | `check_baseline --allow-index-append` | `passed=true`，`errors=[]` |

## 3. 实现要点（逐任务）

### T701 候选证据 `evidence/candidates.py`

- 三类观察 `SOURCE_CONTENT / MODEL_INTERPRETATION / HUMAN_TRANSCRIPTION` **不互相覆盖**，注册后不被"接受"提升类别。
- 没有可指认 locator 的内容**不得进入**候选证据（SDD 11.3）。
- **读取即现查权限**：`read_candidate` 强制要求 `AccessGuard`（可调用对象），拒绝接收布尔"已授权"标记——五年后回溯导出时，撤销必须立即生效。
- 撤销 → `mark_stale` 标 `STALE` 但**不删除**；`STALE` 即使权限恢复也不导出。

### T702 补丁与决策 `review/patches.py`、`review/decisions.py`

- 复用 `jsonpatch` 1.32 执行 RFC6902，**不另造补丁引擎**（SDD 12.1 明确要求）。
- 只允许 `test`/`replace`；每个 `replace` 必须**紧邻同路径 `test`**，否则拒——并发编辑会失败而不是被静默覆盖。
- 字段白名单 + 禁改清单：`source_sha256`、`schema_version`、`generation_id`、`/evidence`、`locator`、`normalizer_version`、`profile_sha256` 一律不可 patch。
- **CAS**：`expected_current_generation_id != current_generation_id` → `E_VERSION_CONFLICT`，不套用到新版本上。
- **预览不改原文档**（测试用深拷贝快照断言）。
- 决策绑定 **patch digest + base generation + access revision + scope** 四项，任一变化 `is_decision_valid` 即 False。
- 无银行适配时 `BANK_APPROVED` → `E_CONSUMER_NOT_CONNECTED`：**本地单用户复核不冒充银行审批**（SDD 12.1）。

### T703 依赖 `review/dependencies.py`

- 加工 DAG **禁环**（`add_edge` 建边前查可达性），与"业务流程图可有环"明确区分。
- 影响表按 SDD 12.2 逐行落地（父表头/cell 文本/脚注范围/续表/流程箭头/图表轴单位/来源撤销）。
- 未知变更类型 → 报错，**不静默失效为零**。
- `reverse_patch` 生成**针对当前版本**的新补丁（test+replace），保留历史，不改原件。

### T704 outbox `release/consumers.py`

- 入队按 `event_id` 幂等；同 id 不同内容 → `E_VERSION_CONFLICT`。
- ACK 必须绑定同一 `generation_id`；旧 generation 的迟到 ACK → `E_VERSION_CONFLICT`。
- 同一 `ack_token` 重放幂等；不同 token 抢同一事件 → 冲突。
- 未连接目标只报 `NOT_CONNECTED`，**存储状态仍为 PENDING**，不产生"已重建"结论。

## 4. T705 端到端实跑（TL 探针，非独立 QA）

一次连续链路，输出原文：

```text
1) 预览： ['/tables/0/cells/0/text']
   原文档未被改： 100
2) 影响扇出： ['p1', 's1'] ['CANDIDATE_EVIDENCE', 'CELL_SEMANTICS', 'CHART_POINT']
3) 决策有效： True | 换 scope 后： False
4) 银行分支： E_CONSUMER_NOT_CONNECTED
5) 未连接： pending 0 / acked 0 / not_connected 1，且事件仍为 PENDING
6) 已连接后： pending 0 / acked 1 / not_connected 0
```

链路覆盖：纠错预览 → 扇出影响 → 重算集 → 决策绑定与失效 → 银行分支阻断 → outbox 未连接/已连接。

## 5. 缺口与未验证

| 编号 | 缺口 | 影响 |
|---|---|---|
| `L07-GAP-01` | T705 为 **TL 自跑**，非独立 QA | 只能证明链路可走通，不能作为验收结论 |
| `L07-GAP-02` | outbox 为进程内实现，**未接真实 MQ/consumer** | 幂等与迟到 ACK 的逻辑已测，真实投递未验 |
| `L07-GAP-03` | patch 白名单未覆盖结构变更（拆合 cell） | 按 SDD 12.1 有意不支持，需专门结构补丁方案 |
| `L07-GAP-04` | 重算结果为"受影响 ID 集合"，**未真正重跑下游生产** | 重算执行器属 L08 交付 |

## 6. 结论

T701–T704 的函数级契约与安全约束已实现并合入 `main`，A1/B/C 三门绿，67 例新增测试通过，
端到端链路可走通。**但 `L07-GAP-04` 必须强调**：当前只算出"哪些对象受影响"，
**没有真正重跑下游**——把影响清单当成"已重算"就是假绿，这一条留给 L08 并在此显式记账。
