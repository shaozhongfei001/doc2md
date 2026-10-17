# 差异决议候选与覆盖策略 V1.0

## 1. 结论

**不覆盖原契约。新增版本化合同并保留旧链路。**
本包没有变更原仓库任何源码、SQL或Spec。以下是本轮明确提出的差异/新增方案；
“生成”授权不是Owner已批准每项业务政策，不预填批准人、批准时间或签署状态。
不触碰旧合同的适配实现可在后续授权开发范围继续；具体保护合同变更未决时只停止受影响项。

## 2. 差异清单

### DX-01 框架引用调整

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_DESIGN_ROUTE；尚未实施 |
| 影响工作项 | PI-F03-01…04 |
| 当前来源 | Excel F03拟复用WeKnora Connector/StreamingConnector |
| 拟议方案 | 采用Python SourceAdapter+本地manifest实现，借鉴能力/游标/checkpoint，不导入WeKnora Go平台 |
| 理由 | 避免第二套运行态/身份/存储；真实银行connector尚无接口 |
| 消费方/兼容 | 不改旧source/DB/CLI，新增来源账本和合同；所有旧消费者不接新字段 |
| 验证 | L02事件/撤权/崩溃/银行联调；真实源缺失保留NOT_CONNECTED |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-02 Word框架调整

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_DESIGN_ROUTE；尚未实施 |
| 影响工作项 | PI-F04-01/04 |
| 当前来源 | Excel建议WeKnora原生Word解析前保留HTML/XML |
| 拟议方案 | python-docx 1.2.0+lxml直接只读OOXML；只补原生合并/最终显示/定位适配 |
| 理由 | Docx2优先MarkItDown和重复文本合并不证明原生merge保真；用户要求最小引用 |
| 消费方/兼容 | 不换旧MarkItDown，不改parsed_text；新表格在伴随产物 |
| 验证 | 横纵合并/嵌套/空白/修订Gold对照，源hash不变 |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-03 新伴随产物

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_ADDITIVE；尚未实施 |
| 影响工作项 | PI-F04/PI-F05 |
| 当前来源 | IR1.0 cell bbox恒null、无confidence，键集封闭 |
| 拟议方案 | 新增document-enhancement/1.0存坐标、表头、脚注、图结构和候选；IR1.0只读 |
| 理由 | 新需求无法合法塞回封闭IR；保护D15/D18 |
| 消费方/兼容 | 旧schema/renderer/evidence消费者不变；新消费者明确opt-in，禁止把sidecar交旧validator |
| 验证 | schema正反、对照hash、缺sidecar旧链路仍可跑 |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-04 CLI与存储新增

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_ADDITIVE；尚未实施 |
| 影响工作项 | 全部19项 |
| 当前来源 | 旧19CLI、SQL、目录冻结 |
| 拟议方案 | 新增app.extensions.cli+extension_root，文件账本单写者/原子generation |
| 理由 | 减少旧命令/注册表的语义冲击，零数据库迁移 |
| 消费方/兼容 | 新入口/新错误码只在扩展CLI，旧参数/返回保持；源码采用新增命名空间 |
| 验证 | dry-run/幂等/权限位与hash对照、全量旧回归 |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-05 候选证据分离

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_ADDITIVE；尚未实施 |
| 影响工作项 | PI-F05-06/PI-F04-09 |
| 当前来源 | 017规划table_cell/image_region进入旧证据；field_value零数据 |
| 拟议方案 | 本包先提供candidate_evidence_id伴随证据，不写kb_evidence；旧UID v1保持 |
| 理由 | 本期是候选加工，未完成017证据生产闭环，不冒充已接入 |
| 消费方/兼容 | 下游需读取新候选schema；未来DB证据适配另按017裁定或新差异处理 |
| 验证 | 原UID固定向量、零旧表写、候选回链与撤权E2E |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-06 图表数值估读条件

| 字段 | 决议内容 |
|---|---|
| 状态 | OWNER_DECISION_REQUIRED_FOR_NUMERIC_ESTIMATE；尚未实施 |
| 影响工作项 | PI-F05-04 |
| 当前来源 | Excel允许柱高估读但标估算，不入硬规则；原D08要求缺数据不编造 |
| 拟议方案 | 默认仅定性/未知，exact_value=null；数值estimated_value分支保留但禁用，待Owner明确用途和边界 |
| 理由 | 避免模型数字冒充来源精确值，同时不悄悄删除Excel条件分支 |
| 消费方/兼容 | 只影响数值估读；原表/可见数字标签精确读取可继续 |
| 验证 | exact/estimate分离负例、无标签不得填精确值；启用需另有决定 |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-07 发布与下游失效边界

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_CONDITIONAL_SCOPE；尚未实施 |
| 影响工作项 | PI-F04-09/PI-F03-03 |
| 当前来源 | Excel描述图谱/Wiki/索引重建与切换 |
| 拟议方案 | 本期生成依赖manifest、outbox/ACK合同及本地消费实现；外部真实适配按接口条件 |
| 理由 | doc2md无Graph/Wiki运行系统，不能借名字假装完成 |
| 消费方/兼容 | 未连必需consumer不能ACTIVE；不把LOCAL_READY改叫全量发布；外部接口仍待确认 |
| 验证 | ACK晚到/缺失、真实联调、回退演练分开报告 |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-08 依赖与推理隔离

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_ADDITIVE；尚未实施 |
| 影响工作项 | PI-F04/PI-F05 |
| 当前来源 | 旧requirements/图片Protocol+Mock与主机环境 |
| 拟议方案 | 新Word/patch依赖单独锁；Paddle模型独立worker；VLM通过批准内网服务 |
| 理由 | 不污染旧venv；不把Mock或个人GPU配置当银行环境 |
| 消费方/兼容 | backend/requirements.txt不改；不默认升级magic-pdf；模型/驱动/weight版本仍需实测锁 |
| 验证 | 缺依赖/超时/worker回收、真实模型矩阵 |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-09 编排范围调整

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_DESIGN_ROUTE；尚未实施 |
| 影响工作项 | PI-F05-02/05 |
| 当前来源 | Excel在智能体链路中提及LangGraph节点/interrupt |
| 拟议方案 | 19项预处理用普通Python受控任务DAG与持久化候选/人工状态；本包不引LangGraph |
| 理由 | 无四智能体会话编排需求，避免为了少量节点增加另一套运行态 |
| 消费方/兼容 | 未来四智能体编排保留LangGraph候选，不因此修改其需求 |
| 验证 | 中断恢复/预算/一次ROI/人工继续有明确测试 |
| 批准人与时间 | 未指定，按实际决定填写 |

### DX-10 017与SPEC_INDEX状态对齐

| 字段 | 决议内容 |
|---|---|
| 状态 | PROPOSED_ADOPTION_RECORD；尚未实施 |
| 影响工作项 | 全部19项 |
| 当前来源 | 017存在DRAFT、DESIGN REVIEWED、P1/P2实现等不同状态，原裁定要求继承 |
| 拟议方案 | 新工作流另目录；TL仅追加选择记录并引用既有TL-RULINGS；不覆盖017、不擅自升级状态 |
| 理由 | 设计授权可推进独立新增层；旧017剩余工作/QA不能被抹掉 |
| 消费方/兼容 | 本包所有对017都是只读；若未来完成017原轨按其验收独立交付 |
| 验证 | baseline锁/SPEC_INDEX增量diff/交付状态核对 |
| 批准人与时间 | 未指定，按实际决定填写 |

## 3. 文件操作逐项约定

| 原文件/路径 | 操作 | 后续何时可变 |
|---|---|---|
| 原handoff/00、02、08 | 保留，只读引用 | 原Owner维护新版本，不由本包覆盖 |
| specs/001…016 | 保留封版 | 必须新差异/缺陷Spec授权，不能静默编辑 |
| specs/017-unified-document-ir | 保留 | 完成其原剩余任务另走其白名单/裁定/验收；不得用本包05替换A017 |
| docs/architecture/DOCUMENT_IR_DESIGN_V1.md | 保留 | 未经单独决议不覆盖 |
| 原backend/app、CLI、models、sql | 全部保持 | 仅新增extensions命名空间；旧代码若需改另报差异 |
| backend/requirements.txt | 保留 | 新依赖另锁，新worker隔离；不要顺手升级旧栈 |
| specs/SPEC_INDEX.md | 追加新选择记录 | 由TL核对当前HEAD后追加；保留017真实状态和原历史 |
| 本包spec-proposal五件套 | 新增 | 后续差异版本化，采用后不得无记录改字段 |
| 本包contracts和loop合同 | 新增v1.0.0候选 | 修订产出v1.0.1或v1.1并说明兼容，不拿同路径静默改含义 |

## 4. 吸收与回退

先在隔离分支复制新目录；运行本包只读baseline检查；由TL登记采用的合同hash/允许批次，
保留差异状态。选择记录与文件齐全不等于独立QA或银行验收。
任何删除旧代码、把sidecar写回IR、变更UID、迁移SQL都不在默认白名单。
代码回退只移除新入口/路由选择，不清除来源撤权账本、审计或原件；
已产生的新派生产物保留受限可追溯，不自动删掉历史。

若用户所说“原契约”还包含未随ZIP提供的另一套SDD/Loop，该套内容尚未核对；
同样先保留，不依据本包自动整套替换。TL取得后只比较受影响条款，追加新的差异决议。
