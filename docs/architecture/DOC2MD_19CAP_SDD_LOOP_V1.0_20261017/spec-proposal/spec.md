# Spec：doc2md首批19项增强能力

工作流DOC2MD-19CAP，状态DESIGN_CANDIDATE，尚未占用正式018编号。
范围以本包04_TRACEABILITY中19个用户工作项和inputs/workbook_selected_19_rows.json为准。
对应需求/约束/异常的完整条款在01_SDD；它是本proposal的详细设计正文，不另造一套泛化需求。

目标：在保留原件、三件套、IR1.0、旧CLI/SQL/UID的条件下，新增来源同步、表格保真与图像候选、
人工纠错和依赖重算。接口/框架实现建议的差异见03；业务边界不扩大为四智能体整个平台。
未连接外部源/模型/consumer、质量目标未取得，不阻塞独立模块开发，但阻塞对应真实验收。
原017保持原状态；本proposal不替换或废弃它。正式选择由TL追加SPEC_INDEX记录。

[打开详细设计正文](../01_SDD.md)。
