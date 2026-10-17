# 19项需求到施工追溯矩阵

来源：Excel V0.3，Library version 2，端到端技术分解。source_data_index是读取结果索引，不冒充Excel原始需求编号。Python路径、方法、接口均为本轮设计候选。

| 工作项 | 原表数据索引 | 模块/方法 | 对象与接口 | 控制点 | Loop | 测试/验收 |
|---|---:|---|---|---|---|---|
| PI-F03-01 登记源版本与复用路径 | 10 | M03 / register_source_version | SourceVersion / IF01/IF03 | 版本身份/许可/复用途径 | L02 | 3例；AC-PI-F03-01 |
| PI-F03-02 同步新增、修改和撤销 | 11 | M03 / apply_page | SourceEvent/SyncCheckpoint / IF02 | 去重/墓碑/耐久cursor | L02 | 4例；AC-PI-F03-02 |
| PI-F03-03 核对迁移与切换 | 12 | M07 / reconcile_and_switch | ReleaseManifest / IF10 | 分母对账/CAS/回退 | L08 | 4例；AC-PI-F03-03 |
| PI-F03-04 分别登记行内规则和外部参考 | 13 | M03 / classify_authority | SourceVersion / IF01/IF03 | 来源分类/权威/许可 | L02 | 3例；AC-PI-F03-04 |
| PI-F04-01 原生Word表格还原合并格 | 14 | M04A / extract_native_tables | Table/Cell/DocxLocator / IF04 | 最终视图/原生合并 | L03 | 4例；AC-PI-F04-01 |
| PI-F04-02 扫描表格生成结构候选 | 15 | M04B / predict_tables | Table/raw_engine_ref / IF05 | 真实框架返回/页数完整 | L04 | 3例；AC-PI-F04-02 |
| PI-F04-03 统一裁切图和原页坐标 | 16 | M04B / map_to_original | PageGeometry/PageLocator / IF06 | 单位/可逆变换/原图hash | L04 | 4例；AC-PI-F04-03 |
| PI-F04-04 检查并展开行列跨度 | 17 | M04C / validate_and_map_grid | Table/slots / IF06 | 无重叠/越界/洞 | L03 | 3例；AC-PI-F04-04 |
| PI-F04-05 把OCR文字放回对应格 | 18 | M04B / align_tokens | Cell/token_ids / IF05/IF06 | 优先引擎格值/冲突保留 | L04 | 3例；AC-PI-F04-05 |
| PI-F04-06 建立多级表头和行标题路径 | 19 | M04C / derive_header_paths | CellSemantics / IF07 | 路径按cell而非词合并 | L05 | 3例；AC-PI-F04-06 |
| PI-F04-07 判断相邻页是不是同一续表 | 20 | M04C / propose_continuation | Continuation / IF07 | 候选关系/人工确认 | L05 | 3例；AC-PI-F04-07 |
| PI-F04-08 绑定脚注和表外条件 | 21 | M04C / bind_notes | Note/CellSemantics / IF07 | 范围与原文保留 | L05 | 3例；AC-PI-F04-08 |
| PI-F04-09 保存人工纠错补丁并重算影响 | 22 | M06 / apply_reviewed_patch | CorrectionPatch/ConsumerEvent / IF09 | 版本CAS/依赖失效 | L07 | 4例；AC-PI-F04-09 |
| PI-F05-01 区分表格、流程图、统计图和装饰图 | 23 | M05 / classify_and_route | Visual / IF08 | 分类歧义/单区域单路径 | L06 | 3例；AC-PI-F05-01 |
| PI-F05-02 抽取流程节点及箭头候选 | 24 | M05 / extract_flow | FlowGraph / IF08 | 节点/边定位与方向 | L06 | 3例；AC-PI-F05-02 |
| PI-F05-03 检查图结构与图文一致 | 25 | M05 / validate_graph_and_text | FlowGraph/Issue / IF08 | 循环合法/矛盾保留 | L06 | 3例；AC-PI-F05-03 |
| PI-F05-04 读取统计图的轴、单位及数据 | 26 | M05 / extract_chart | Chart/ChartPoint / IF08 | 精确与估读分开 | L06 | 4例；AC-PI-F05-04 |
| PI-F05-05 对不一致区域局部复识别 | 27 | M05 / reconcile_roi_once | PageGeometry/Issue / IF08 | 一次复识别/全程坐标 | L06 | 3例；AC-PI-F05-05 |
| PI-F05-06 把图中发现转换为带证据的候选 | 28 | M06 / build_candidates | CandidateEvidence / IF08/IF11 | 来源版本/hash/观察类别 | L07 | 3例；AC-PI-F05-06 |
