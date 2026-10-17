# 原02设计决策与08红线逐条对齐

本表只记录设计继承与验证计划，未执行G1—G5，不伪称通过。

| 决策 | 原语义 | 本包动作 | 具体落实 | 验证 |
|---|---|---|---|---|
| D-01 | sha256即内容身份 | 保留 | 新source_version和source_access与content身份分离；多来源同字节不共享授权 | inputs/baseline_lock.json、TC-PI-F03-01-01 |
| D-02 | 源文件/raw_vault只读 | 保留 | readbridge仅rb读取；原生Word不save；新目录不允许包含/嵌入源根 | G4原件hash、路径逃逸负例 |
| D-03 | 三件套只增不改 | 保留且本期零改动 | 伴随产物在extension_root，不给旧三件套加新键 | G4旧manifest逐键和hash |
| D-04 | PDF路由fail-closed | 保留既有代码 | 不改014路由；缺vault例外表达冲突登记，新增层缺原件直接拒绝 | 旧parser_router_v2/pdf_sniffing回归、NEEDS_BASE_PARSE |
| D-05 | 规则版本化/旧版复现 | 保留 | 新runner不改旧routing_rules；新profile自带版本与hash | G1/G4旧v1路由向量 |
| D-06 | 嗅探环境态不缓存 | 保留 | 新worker依赖缺失不缓存成成功；不改旧缓存白名单 | 缺依赖复测、worker恢复 |
| D-07 | parser版本/profile参与幂等 | 扩展独立新job_key | 旧profile不变；新job另纳输入/权限/engine/normalizer/patch摘要 | 新ID固定向量、重跑幂等 |
| D-08 | 缺数据null/计数，禁编造 | 保留 | 未知bbox/单位/confidence为null/PAGE_ONLY；数值估读默认关，DX-06单列 | TC-PI-F04-03-02、TC-PI-F05-04-02/03 |
| D-09 | asset sha对齐fail-closed | 保留 | 旧asset按manifest校验；新asset在新manifest按完整sha核对 | TC-PI-F05-06-02、G4 |
| D-10 | 可选依赖与Mock诚实性 | 保留 | 不改旧图片行为；新增真实worker缺依赖显式失败/未覆盖；Mock不得发布 | 缺依赖/Mock负例、实际引擎门 |
| D-11 | 样本不足跳过而非ratio=0 | 保留 | 不改旧semantic checker；新评测缺样本报INPUT_BLOCKED，不计算伪100% | G1旧语义回归、05分母 |
| D-12 | 阈值真实接线 | 保留 | 旧quality yaml不变；新profile阈值必须变异证实生效 | G3与threshold profile hash |
| D-13 | no-semantic复现008 | 保留 | 不改quality checker/summarizer和旧开关输出 | 旧semantic_disabled字段/文件读断言 |
| D-14 | 两套offset字段并存 | 保留 | 新TextLocator引用固定text sha与半开Unicode区间，不改DB字段 | 旧UID向量/offset回归 |
| D-15 | IR1.0严格键集 | 保留 | 新schema namespace，绝不塞cell confidence/bbox/header_path进IR1.0 | schema负例和旧IR文件hash |
| D-16 | block_id/reading_order稳定 | 保留 | 不重写旧block；新object id独立按generation/locator生成 | 旧IR不变，新identity vectors |
| D-17 | 零SQL迁移 | 保留并覆盖新增层 | 文件账本；只经现有服务完成既有登记，无新直接DB写 | G5 sql树无新增/无diff、DB审查 |
| D-18 | rendered与parsed_text分离 | 保留 | 新rendered在extension_root；不覆盖旧rendered或基准parsed_text | G4正文hash/offset |
| D-19 | 可扩展证据但field_value零数据 | 不动旧证据合同 | candidate_evidence独立；旧kb_evidence无写，table/image正式入库另审 | G4固定旧UID与旧表快照 |
| D-20 | ir_source保留additive且旧v1空数组 | 保留只读 | 新bridge读取并快照原JSON；不改旧MinerU/force清理逻辑 | 旧v1/新raw hash与解析回归 |

## 08红线

| 红线 | 原约束 | 本包落实 | 证据 |
|---|---|---|---|
| L-1 | 原parsed_text语义/hash | 零回写，快照只在新目录 | G4 |
| L-2 | evidence_uid v1 | 旧公式/源码/行不变；新ce命名不同 | G4 |
| L-3 | sql/** | 无schema/迁移/建新DB | G5 |
| L-4 | 001—016封版 | 只读，差异另表 | G5 |
| L-5 | fail-closed/不编造 | 半成品不COMMITTED；未知空值和待核 | G2/G3 |
| L-6 | 硬编码凭据 | 只用安全配置引用和环境；不把真实app.yaml打包 | L00/安全扫描 |
| L-7 | 本机独有依赖 | 独立锁worker/源码hash与信创实测，不依赖个人路径/GPU | L00/L04/L06 |

## 原08验证门

G1全量基线不能只看“failed≤4”：现有requirements已约束click<8.2，必须在同一恢复环境跑前后版本，
只允许已记录且同根因的旧失败，修复环境后应全绿；新失败/新增skip必须解释。
G2逐模块契约测试除旧5类还要本包新case；G3临时隔离副本变异与还原hash；
G4旧三件套、offset、证据UID、原件不变；G5零SQL/白名单。
这些是后续工程Gate，不是本文作者已代QA签字。
