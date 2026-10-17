# doc2md 首批19项能力：SDD 与 Loop Engineering 契约包

版本：V1.0；日期：2026-10-17（Asia/Shanghai）。
包状态：**DESIGN_CANDIDATE / 未实施 / 独立QA待执行**。
工作流：DOC2MD-19CAP；作者角色：解决方案与详细设计负责人。本包提出可施工合同，不代替 Owner 审批、独立 QA 或本行验收。

## 是否覆盖原契约

**不要覆盖。将本包作为版本化增量方案放入新目录。**
保持原 `specs/001…016`、`specs/017-unified-document-ir`、三件套、IR 1.0、旧CLI、SQL和证据UID不变。
017 后续未完成任务继续保留；新方案不把这19项冒充为017已经交付。

| 类别 | 本包处理 |
|---|---|
| 001—016封版契约、02决策、08红线 | 保留；只读引用 |
| 017五件套及TL-RULINGS-P1 | 保留；若需完成017原轨，另按原验收执行，不能用本包替换 |
| 本包SDD/Loop/字段schema | 新增候选；不自动宣称冻结或批准 |
| SPEC_INDEX | TL接手后追加一条新工作流选择记录；保留历史，不把017改为DONE |
| 旧理解交接稿0.2 | 继续保留为来源理解证据；本包是新设计，不是它的覆盖版本 |
| 真正修改旧字段、SQL、CLI语义或UID | 必须先补03中的差异决议；本包默认路径不需要这些破坏性变更 |

推荐放置：
- 主设计文档 → `docs/proposals/doc2md-19cap/v1.0.0/`；
- 本包 `spec-proposal/` → `specs/proposals/doc2md-19cap-v1.0.0/`；
- 本包 `contracts/` → `contracts/doc2md-19cap/v1.0.0/`；
- 本包 `loop/` → `loop/proposals/doc2md-19cap/v1.0.0/`。
不擅自占用“018”正式编号。TL检查当前SPEC_INDEX后登记最终编号；变更编号只更新交叉引用。

## 先读顺序

1. 原仓库 `handoff/external-refactor-2026-10-17/00-READ-ME-FIRST.md`；
2. 原包02、08；
3. 本包03覆盖与差异策略；
4. 本包01详细设计；
5. contracts模型/schema及06框架说明；
6. 本包02 Loop合同、05验收、loop/loop_contract.json；
7. 本包08 CodeBuddy TL启动提示词。

## 交付内容

| 文件 | 作用 |
|---|---|
| 01_SDD.md | 总体架构、19项处理逻辑、对象、调用、状态、异常、运行与切换 |
| 02_LOOP_ENGINEERING.md | 9个Loop批次、角色交接、白名单、前置与停止点 |
| 03_CHANGE_DECISIONS.md | 10项差异决议候选、对原契约覆盖策略 |
| 04_TRACEABILITY.csv / .md | 19项→Excel行→模块/方法→对象→接口→控制→测试→Loop |
| 05_TEST_ACCEPTANCE.md | 逐项正反例、评测分母、实测/模拟证据分级、验收条件 |
| 06_FRAMEWORKS.md | 复用框架、选定方向、接入字段、版本和POC条件 |
| 07_OPEN_INPUTS.md | 仅阻塞受影响项的输入/决策缺口 |
| 08_CODEBUDDY_TL_PROMPT.md | 可直接交给TL的完整启动提示词 |
| 09_DECISION_ALIGNMENT.md | 原D-01…20、L-1…7、G1…5逐条对齐 |
| contracts/ | 12个根payload的JSON Schema、Pydantic参考模型和合成示例；不是业务实现 |
| loop/ | 机器可读任务合同、状态及角色动作 |
| spec-proposal/ | 新工作流SDD五件套入口 |
| inputs/ | 源码哈希锁、Excel原行摘录和来源索引 |
| tools/ | 包结构检查、旧基线只读校验；不修改仓库 |
| VALIDATION_REPORT.md | 本轮交付包自检实测；不是产品测试/独立QA |

## 执行与授权

本会话“生成”已授权生成方案、合同和必要的可检验样例，不等于授权合并、生产部署或覆盖封版契约。
TL可按本包推进不触碰旧契约的实现准备与新增文件开发；只有明确受影响的业务授权、
真实接口、数字估读策略和破坏性变更才等待对应决定。不要把所有未决项做成全局停止条件。
