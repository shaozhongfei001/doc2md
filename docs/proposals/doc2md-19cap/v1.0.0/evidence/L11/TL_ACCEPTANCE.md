# L11 验收记录（T1101–T1104，重算执行器）

- 工作流：`DOC2MD-19CAP` / 修订 `DOC2MD-19CAP-V1.0.1-ADDENDUM-L11`
- 入口：`loop-entry/L11` = `a461c5e`
- 代码：`c68aceb`
- 合并：`--ff-only` → `main` = `c68aceb`
- **状态：`CODE_MERGED` + `L07-GAP-04 CLOSED` + `INDEPENDENT_QA_PENDING`**

## 1. 这个 Loop 关闭的是什么

`L07-GAP-04`：整改路径到"受影响对象清单"就停了，从未真正重跑下游。
清单很容易被当成"已重建"——产物看起来仍然完整，错误不可见。

## 2. 门禁

| 门 | 结果 |
|---|---|
| A1 | 官方工具不支持（`unknown Loop`）→ 等价自检：区间**恰好 2 个文件**，`outside whitelist: none`，无未跟踪 |
| B | **1331 passed / 133 skipped**（L10 后 1319，+12） |
| C | `passed=true`，`errors=[]` |
| GAP 关闭判据 | 端到端用例 `test_changing_one_cell_rebuilds_its_semantics_and_leaves_the_old_version_alone` 通过 |

## 3. 四条性质，每条都对应一种"看起来对"的错法

| 性质 | 错法 | 实现 |
|---|---|---|
| **拓扑序** | 子对象用改前的父数据重算，结果"合理但过期" | 按加工 DAG 求子图拓扑序，根视为已变更；同级按 id 稳定排序 |
| **不静默跳过** | 缺 producer 时跳过该对象，其余照跑并报成功 | 重建**任何对象之前**检查全部 edge_type，缺则整体 `E_NOT_IMPLEMENTED` + `blocked_objects` |
| **逐条如实** | 一个失败吞掉整批，或失败项消失 | `REBUILT/FAILED` 逐条记录，`PRODUCER_RAISED` 兜住非 `ExtensionError`，批次 `ok=False` |
| **纯函数** | 就地改 state、顺手落盘 | 输入深拷贝、不写文件、同输入 digest 相同 |

## 4. 过程中修掉的一个真实 bug

`_subgraph_order` 首版把 root 只当作"不在 affected 里"，没有把它加进 `resolved`，
于是**根的直接子节点永远不 ready**，10 个用例一起报"依赖图有环"。

这个 bug 的危险不在它会被发现（很响），而在于**错误信息误导**：它说的是"图的环"，
实际是"根没被标记完成"。若当时按提示去查环，会白查很久。已改为 `resolved = {root_id}` 并留注释说明理由。

## 5. 新增限制（如实登记）

| 编号 | 限制 | 影响 |
|---|---|---|
| `L11-LIM-01` | `recompute` **未接入** `pipeline.py`：`assemble` 不会自动触发重算 | 重算是可调用能力，不是流水线默认行为；接线路标在 L12 |
| `L11-LIM-02` | producer 由调用方按 `edge_type` 注册，模块**不自动发现**生产者 | 注册缺失会整体拒绝（安全），但也意味着新 edge_type 必须显式接线 |
| `L11-LIM-03` | 重算只按"父对象 payload"做局部重建，**不做全量重解析** | 需要重解析的改动（如换 OCR 引擎）不在本 Loop 范围 |

## 6. 结论

`L07-GAP-04` **关闭**：影响清单现在能驱动真实重建，且有端到端断言证明
"改一格 → 只有依赖对象被重建 → 产出新版本 → 旧代字节不变"。

但它**尚未成为流水线默认行为**（`L11-LIM-01`）——所以对外只能说"具备重算能力"，
不能说"纠错后系统会自动保持下游一致"。这条差别会被写进交接材料。
