# TL 最终评审（L08 / T806）

- 工作流：`DOC2MD-19CAP`，契约 `DOC2MD-19CAP-V1.0`（`DESIGN_CANDIDATE`）
- 范围：本包登记的 **19 项底层能力**（`PI-F03-01—04`、`PI-F04-01—09`、`PI-F05-01—06`）
- 声明边界：**不**声明客户签署、需求冻结、独立 QA 通过、银行验收；**不**声明 017 已完成或 018 已编号。

## 1. 19 项逐项状态

状态用词：
`CODE_COMPLETE` = 实现 + 测试 + 门禁齐备；`CODE_PARTIAL` = 实现有缺口；
`NOT_VERIFIED` = 缺真实输入，只能证明结构；不代表缺陷。

| 编号 | 能力 | Loop | 状态 | 未验证项 |
|---|---|---|---|---|
| PI-F03-01 | 来源版本 | L02 | CODE_COMPLETE | — |
| PI-F03-02 | 增量同步 | L02 | CODE_COMPLETE | MB3–MB5 未复跑；MySQL 131 例 `NOT_EXECUTED` |
| PI-F03-03 | 迁移切换核对 | L08 | **CODE_PARTIAL** | 真实路由/ACK 未接（`BANK_JOINT_REPORT`）；`LOCAL_READY`≠`ACTIVE` 已强制 |
| PI-F03-04 | 行内规则与外部参考分开登记 | L02 | CODE_COMPLETE | — |
| PI-F04-01 | Word 原生表格 | L03 | CODE_COMPLETE | 真实语料盲评 `NOT_RUN` |
| PI-F04-02 | 扫描表格结构候选 | L04 | **CODE_PARTIAL** | Paddle 字段未对 golden 确认；`requirements-ocr-worker.lock` 未解析 |
| PI-F04-03 | 坐标 | L04 | CODE_COMPLETE | 精度未实测（无语料） |
| PI-F04-04 | 跨度 | L03 | CODE_COMPLETE | — |
| PI-F04-05 | 文字回格 | L04 | CODE_COMPLETE | 阈值 0.8/0.15 未校准 |
| PI-F04-06 | 多级表头 | L05 | **CODE_PARTIAL** | `L05-GAP-01`：表头带无上游生产者，端到端不可达 |
| PI-F04-07 | 续表 | L05 | **CODE_PARTIAL** | `L05-GAP-02`：`page_index`/列宽无生产者 |
| PI-F04-08 | 脚注 | L05 | **CODE_PARTIAL** | `L05-GAP-03`：脚注文本未抽取 |
| PI-F04-09 | 人工纠错与重算 | L07 | **CODE_PARTIAL** | `L07-GAP-04`：只算影响集，未真正重跑下游 |
| PI-F05-01 | 图像分类 | L06 | CODE_COMPLETE | 真实 VLM 响应未验 |
| PI-F05-02 | 流程图 | L06 | CODE_COMPLETE | 同上 |
| PI-F05-03 | 图文一致性 | L06 | **CODE_PARTIAL** | `L06-GAP-04`：仅词面（提及+否定极性），未做动作/角色/先后语义比对 |
| PI-F05-04 | 统计图 | L06 | CODE_COMPLETE | Chart2Table 字段未对 golden 确认 |
| PI-F05-05 | 局部复识别 | L06 | CODE_COMPLETE | 真实引擎未触发预算 |
| PI-F05-06 | 证据候选 | L07 | CODE_COMPLETE | 真实 consumer 未接 |

统计：`CODE_COMPLETE` 11 项，`CODE_PARTIAL` 8 项，无 `NOT_STARTED`。

## 2. 门禁汇总

| Loop | A1（范围） | B（全量） | C（受保护） | 契约校验 |
|---|---|---|---|---|
| L00–L03 | passed | 869→961 | passed | 12 根契约示例 |
| L04 | passed（13 文件） | 1071 | passed | — |
| L05 | passed（9 文件） | 961 | passed | — |
| L06 | passed（14 文件） | 1181 | passed | Chart / FlowGraph / Visual |
| L07 | passed（10 文件） | 1248 | passed | — |
| L08 | passed | **1279** | passed | — |

## 3. 必须随交付一起说的四句话

1. **测试全绿不等于业务可用**：8 项 `CODE_PARTIAL` 中，有 3 项（F04-06/07/08）是"函数可用但上游无生产者"，
   2 项（F03-03、F04-09）是"本地可算但无下游执行/真实接口"。
2. **`INDEPENDENT_QA` 全程 `PENDING`**：本工作流所有代码由 TL 或派发 DEV 编写，
   没有独立于作者的 QA 结论。TL 探针抓到过真实的发布级缺陷（L05-P5、L04-P2），但那是自检，不是独立验收。
3. **T803/T805 是 `NOT_CONNECTED` / `NOT_RUN`**，不构成银行侧任何结论。
4. **017 未受影响**：`specs/017-unified-document-ir` 状态、R-01…R-18 裁定、验收 ID 均保留不变；
   本工作流未写入 IR 1.0 任何新键，也未占用 018 编号。

## 4. 可交范围建议（供 HO 与 Owner 决策）

| 范围 | 建议 |
|---|---|
| 契约层（L01）、来源层（L02）、Word 结构层（L03） | **可交**，附 MySQL 未执行说明 |
| 几何/OCR 适配（L04） | 可交"代码 + fail-closed"，**不可**宣称引擎已验证 |
| 表头/续表/脚注（L05） | 可交函数层，**须先补上游生产者**才可端到端 |
| 视觉/图表（L06） | 可交结构校验层，语义一致性按 `L06-GAP-04` 标注为受限 |
| 纠错/证据（L07） | 可交，**须明确"影响清单≠已重算"** |
| 切换/对账（L08） | 仅 `LOCAL_READY` 能力可交；`ACTIVE` 依赖银行接口 |

---

## 附记 01（QA 后更新，2026-10-18）

第 3 节第 2 条"`INDEPENDENT_QA` 全程 `PENDING`"**已被后续工作更新**，原文保留以示过程：

- 已由**独立实例** `qa-mutation`（未参与任何被审代码编写）对 6 个模块的 8 条关键防护做变异抽检，
  结果 **8/8 CAPTURED**，详见 `evidence/QA/INDEPENDENT_QA_REPORT.md`。
- 状态从 `PENDING` 更新为 **`PARTIAL_PASS`**：安全防护面已获非作者验证；
  **功能正确性与 9 个 Loop 的逐项验收仍未独立验收**，且 QA 是独立实例而非独立组织（同一模型/运行时）。
- 该更新**不改变**第 1 节中 8 项 `CODE_PARTIAL` 的判定——那些是输入缺失与上游接线缺口，
  与"测试是否守得住规则"是两件事。
- 附带证伪：TL 曾预判图表 `PRINTED_LABEL` 降级有冗余防护，实测**没有**，
  该守卫是单层承载性的（`build_chart` 的值类型校验方向相反）。预判错误已记录在该报告 2.2 节。
