# Tasks：新增工作流施工清单

所有任务TODO；详细输入/白名单/停止点见02_LOOP_ENGINEERING，机器版见loop/loop_contract.json。

## L00 基线、环境和实施选择

- [ ] T001 记录ZIP HEAD、业务父提交、内容哈希及4项mode差异；核对最新工作树
- [ ] T002 复测实际Python/Click/Typer/MySQL与旧pytest，记录具体失败与skip身份；不沿用数量容忍
- [ ] T003 锁定新目录/写入根和来源范围；把017保持只读的工作流选择记录追加到SPEC_INDEX
- [ ] T004 DB预审新增层无直接DB写、无migration，核对旧服务只读/调用边界

## L01 合同、输入桥接与原子产物仓库

- [ ] T101 实现身份canonicalization与独立伴随schema，正反例及跨进程稳定ID
- [ ] T102 实现受限extension_root、路径/符号链接校验和read-set固定
- [ ] T103 实现单源/对象锁、staging+fsync+rename+CAS，故障点恢复
- [ ] T104 新增独立CLI薄壳；dry-run不建锁文件、不调用模型、不写报告
- [ ] T105 读取既有三件套/可选IR/manifest资产；缺失显式NEEDS_BASE_PARSE
- [ ] T106 新增目录fixture和contract/IO/CLI测试；对照原保护hash

## L02 来源版本、许可与可恢复同步

- [ ] T201 定义SourceAdapter协议；先实现LocalManifestConnector与能力探测
- [ ] T202 登记原生revision/本地snapshot、复用/重加工路径、同源版冲突
- [ ] T203 来源类别/权威/许可/用途/有效期校验，不让外部覆盖行内
- [ ] T204 提交事件页、完整checkpoint与cursor；撤权先落账、墓碑禁止旧事件复活
- [ ] T205 生成源清单对账与影子路由；未连银行源保持UNSUPPORTED/NOT_CONNECTED

## L03 原生Word与表格结构

- [ ] T301 安全只读OOXML；FINAL文字与审阅批注分轨，结构修订识别
- [ ] T302 gridSpan/vMerge/gridBefore/After、anchor和嵌套表定位
- [ ] T303 独立grid严格校验和slots生成；可复用旧schema的合法矩形投影校验但不写旧IR
- [ ] T304 构造/读取横纵合并、修订、空白、嵌套Word Gold并验证原件零变化

## L04 Paddle/MinerU适配、坐标和OCR

- [ ] T401 在独立worker环境锁PaddleOCR/Paddle/权重；保存真实golden raw和API字段清单
- [ ] T402 MinerU content_list/middle只读映射，唯一匹配才绑定坐标；复用已有正确表
- [ ] T403 实现PDF point/像素/旋转/缩放/crop矩阵与可逆验证
- [ ] T404 优先引擎cell文字，仅对歧义做token候选关联与局部处理
- [ ] T405 真实扫描语料分层评测；记录三份PDF坐标单位和回显；缺真实引擎不得标完成

## L05 多级表头、续表与脚注

- [ ] T501 列/行标题路径由cell引用形成，单位/表题上下文独立
- [ ] T502 相邻页续表候选，比较表名/期间/单位/列定义；确认前不合并
- [ ] T503 原生脚注/版面备注绑定到明确scope，歧义阻断条件发布
- [ ] T504 生成独立可读rendered/review packet及上下文，不改旧parsed_text

## L06 流程图、图表与一次ROI复识别

- [ ] T601 Paddle布局+VLM分类候选和唯一事实路由，混合图分区域
- [ ] T602 批准VLM结构化流程节点/边，schema与引用校验，不训练检测网络
- [ ] T603 图结构及正文一致检查，循环保留、否定/冲突按证据报告
- [ ] T604 PP-Chart2Table适配、轴/单位/标签回核；估读不能伪装精确
- [ ] T605 基于verified region追加一次ROI复识别，保存全部候选和预算/超时
- [ ] T606 真实流程图/统计图语料盲评，分离模型能力与工程结构校验

## L07 人工纠错、候选证据与依赖失效

- [ ] T701 带版本/证据的candidate_evidence；每次读取/导出检查当前权限
- [ ] T702 RFC6902 test/replace白名单、基版CAS、只读预览、授权复核记录
- [ ] T703 依赖DAG，父表头/轴单位等fan-out重算；反向补丁留历史
- [ ] T704 outbox事件和幂等ACK/晚到ACK校验，未连consumer不造成功
- [ ] T705 本地纠错→影响清单→重算→复核完整E2E；外部只报真实已连接范围

## L08 最终对账、切换、恢复与交付

- [ ] T801 完整迁移分母/许可/版本/权限差分，LOCAL_READY与ACTIVE分开
- [ ] T802 并发切换/回退与新旧任务pin，撤权状态不回滚
- [ ] T803 真实源/consumer接口有输入才联调切换回退；缺失分项NOT_CONNECTED
- [ ] T804 旧全量回归、合同负例变异和原件/parsed/hash/UID/SQL零变化
- [ ] T805 故障注入、备份恢复、cold/warm性能和资源边界实测
- [ ] T806 HO按证据写交接，TL逐验收项决定可交范围/缺口；不假定全部已验收

[打开批次白名单与输入](../../../docs/proposals/doc2md-19cap/v1.0.0/02_LOOP_ENGINEERING.md)。
