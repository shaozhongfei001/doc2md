# 测试、评测与验收合同 V1.0

**状态：测试已设计，产品用例全部NOT_RUN；无独立QA签署。**
每个AC对应一个用户工作项，必须通过其全部适用TC；未连接/未取得真实样本的TC不能用PASS替代。
产品测试机器清单见loop/test_cases.json；交付包自身静态检查见VALIDATION_REPORT，二者分开。

## 1. 证据分级与报告要求

| 类型 | 能证明 | 不能证明 |
|---|---|---|
| UNIT | 确定逻辑、结构、状态、边界/否定分支 | 模型识别准确率或银行接口可用 |
| ADAPTER_GOLDEN | 固定版本raw响应与映射符合合同 | 真实推理效果、兼容所有版本 |
| REAL_ENGINE | 记录真实engine/model/weights/环境与输入，实际推理 | 银行生产容量或不同硬件兼容 |
| SIMULATED_INTEGRATION | 本地manifest、模拟源/consumer下的同步事务与异常 | 真实天权/易晓通/访客/外部索引联调 |
| E2E | 指定环境的完整读入/处理/纠错/恢复/输出 | 若源或模型为模拟，不证明其真实质量 |
| BANK_JOINT | 具体银行测试源、授权、消费者入口和切回证据 | 自动取得生产部署/验收批准 |

报告逐例记录：TC、输入sha与Gold版本、source/adapter/model/配置锁、命令/exit code、
实际状态、断言与产物、FAIL/NOT_COVERED/INPUT_BLOCKED原因、角色和执行身份。
只给总数不够；临时跳过要有逐例原因，不能把语料/模型缺失当正常skip。

## 2. 语料与Gold候选

下列数量是施工验收集的初始建议，不是银行已承诺样本或签署标准。B00可按实际输入调优并留差异。

| 语料组 | 最小建议组成 | Gold内容 |
|---|---|---|
| 来源同步 | 2个来源manifest，≥20事件，含重复/乱序/撤回/重授权/缺页 | 版本清单、每页cursor、访问状态、预期对账 |
| DOCX | ≥12份脱敏文件、≥24张表，横/纵/组合合并、空白/省略、嵌套、文本与结构修订 | 原XML locator、FINAL文字、anchor/slots、人工读原件记录 |
| 扫描表 | ≥10份文档/≥30张表，有线/无线/旋转/低清各有；同表切图不能算独立样本 | 原图hash、页尺寸、cell结构/文字、OCR词框与单位 |
| 坐标 | ≥3份真实解析PDF，含CropBox/旋转/大小不同页 | 页级单位校准、人工原图叠框、误差记录 |
| 表格语义 | ≥12张多层/重复表头表；≥6对续表/非续表；≥8组脚注 | 数据格路径、续表判定、限制范围和原文引用 |
| 流程图 | ≥12张，含交叉/回流/双向/泳道/菱形分支 | 节点、边、方向/label、可定位区域、正文一致结论 |
| 统计图 | ≥12张，双轴/对数/单位/截轴/无标签/图表冲突均覆盖 | 轴/单位/period、精确值出处、趋势/不确定范围 |
| 纠错/切换 | 父表头/脚注/箭头/轴单位各≥2案例；并发/崩溃/回退 | 依赖清单、全部受影响对象、ACK和任务pin结果 |

开发/调参/盲验按原文档分组隔离；同一原件裁切、重压缩和近重复图不能跨组制造伪独立评测。
Gold由有权人员核对来源生成；模型输出不能自我充当Gold。授权样本只在批准环境保留，不混入公开仓库。

## 3. 指标与分母

| 指标 | 定义 | 去重/异常处理 |
|---|---|---|
| 表格结构正确率 | span/grid整体与Gold完全相同表数 / 全部Gold表数 | 失败、未恢复、拒识算未正确，不能删分母 |
| cell文字正确率 | 原符号/小数/单位口径一致的格数 / 全部Gold可见格 | 只许事前约定空白/换行等规范化，不能事后去掉负号提高分数 |
| header路径正确率 | 全部列/行path与Gold一致数据格数 / 全部需要路径的Gold数据格 | 缺路径和NEEDS_REVIEW不算自动正确 |
| 自动采纳精度/覆盖 | 自动采纳中正确数/自动采纳数；自动采纳数/Gold对象总数 | 两个指标并报，不能全部拒识后报100%准确 |
| 续表/脚注 | 确认关系/范围的precision、recall及误合并数 | 一对多关系按原件+table pair/note target去重 |
| 流程图节点/边 | 文本/role与端点/方向/label分别precision/recall | 回流边不能因成环被剔除；unknown单列 |
| 精确图值正确率 | 值、轴、series、period、单位、证据均正确的点 / 全部Gold精确点 | 估读/趋势不混精确；缺失算未正确 |
| 证据可回查率 | hash/版本/定位/权限全部有效项 / 所有输出候选项 | 原图被换/撤权的读取拒绝测试另报，不当“可回查失败”掩盖安全行为 |
| 重算影响召回率 | 实际失效/重算Gold依赖数 / 应受影响Gold依赖数 | 记录误伤数和未连consumer，不把漏项留在分母外 |
| 工程稳定性 | 单条错误隔离、cursor耐久、CAS冲突、防复活、恢复成功用例 | 属确定控制；关键安全错误0容忍 |

初始内部目标候选：原生Word基础支持结构100%与Gold一致、证据/权限/版本确定控制100%；
扫描表整体结构≥90%、cell文字≥95%；多级路径≥95%；流程边/方向≥90%；
有可见标签/原表的精确图值≥98%。这些只作POC目标，未经本行确认/真实测量不写成验收承诺。
任何进入“已人工确认输出”的关键数字/否定/限定必须100%通过人工核对；否则保持待核。
达不到模型目标先报告各类失败/复核率并改适配或支持范围；不通过缩分母美化。
没有实测准确率与本行最终阈值时，只能交工程局部成果，不能宣布业务验收完成。

## 4. 逐项测试用例

### AC-PI-F03-01 登记源版本与复用路径

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F03-01-01 原生修订号与本地快照 | 同一文件分别输入真实source_revision和缺修订号的manifest | 前者SOURCE_NATIVE；后者LOCAL_SNAPSHOT且source_revision=null；内容身份相同也保留来源身份 | UNIT；NOT_RUN |
| TC-PI-F03-01-02 同源版不同摘要 | 同source/object/revision先后输入hash A与B | 第二次隔离E_SOURCE_VERSION_CONFLICT，旧版不被覆盖 | SIMULATED_INTEGRATION；NOT_RUN |
| TC-PI-F03-01-03 旧服务复用不导入全文 | REUSE_QUERY+授权查询指针，不给原件 | 仅登记路由/引用；不创建假原件或空解析；授权校验仍必需 | ADAPTER_GOLDEN；NOT_RUN |

### AC-PI-F03-02 同步新增、修改和撤销

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F03-02-01 重复事件与异参 | 并发提交同event及同ID异payload | 同参只一条committed；异参隔离；cursor不越过未处理事件 | SIMULATED_INTEGRATION；NOT_RUN |
| TC-PI-F03-02-02 崩溃恢复与缺页 | 分别在payload/fsync/rename/HEAD前后注入崩溃，再漏掉一页 | cursor只对应已完整提交页；重放不漏/不重；缺页不产生delete | E2E；NOT_RUN |
| TC-PI-F03-02-03 撤权先到旧新增后到 | REVOKE提交后重放旧UPSERT，且order token不可比较 | 保持拒读；查源或隔离；不能按到达时间复活 | SIMULATED_INTEGRATION；NOT_RUN |
| TC-PI-F03-02-04 真实源增量 | 获准源接口一组新增/修改/撤回，含授权TTL失效 | 对账一致；记录源延迟；TTL过期不可复核即拒读 | BANK_JOINT；NOT_RUN |

### AC-PI-F03-03 核对迁移与切换

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F03-03-01 对账分母 | 10个源对象：3复用4重加工1排除1失败1待处理 | 总数10；失败/待处理留在分母，批次未达切换条件 | SIMULATED_INTEGRATION；NOT_RUN |
| TC-PI-F03-03-02 切换安全与ACK | 权限扩大差异或必需consumer未连接 | 阻断ACTIVE；本地模拟ACK不能标真实切换通过 | E2E；NOT_RUN |
| TC-PI-F03-03-03 并发切换与回退 | A切B后生成任务T；另一人已切C；尝试按B回退 | CAS冲突；T仍pin B；不删除T，不回退撤权账本 | E2E；NOT_RUN |
| TC-PI-F03-03-04 真实入口切换回退 | 源和消费者提供路由控制，含切换后新任务 | 双读差异消解，切/回退各一次，原入口连续性和新任务完整有日志 | BANK_JOINT；NOT_RUN |

### AC-PI-F03-04 分别登记行内规则和外部参考

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F03-04-01 外部许可缺失 | 上传同业/外部政策但license_ref为空 | 拒绝有效加工，保留待补元信息，不把其标BANK_EFFECTIVE_RULE | UNIT；NOT_RUN |
| TC-PI-F03-04-02 外部不覆盖行内 | 同名行内规则与外部参考有冲突 | 并存且来源类别不同，外部不能覆盖有效行内规则 | SIMULATED_INTEGRATION；NOT_RUN |
| TC-PI-F03-04-03 上传政策证据回看 | 有许可的脱敏外部政策贯穿解析/复核/候选发布/回看 | 源版/许可/原文位置可回查；声明LOCAL_OPERATOR而非银行正式批准 | E2E；NOT_RUN |

### AC-PI-F04-01 原生Word表格还原合并格

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-01-01 横纵合并 | DOCX同时含gridSpan=2与vMerge重启/继续，两个独立标题同文本 | anchor/slots与Gold逐格一致；不按重复文字额外合并 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-01-02 空白与表中表 | 真空tc、gridBefore、嵌套table、同名cell | 真空白保留；omitted需原XML证据；嵌套独立对象，不混入父文本 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-01-03 修订最终显示 | 已删除旧条款、插入新条款、moveFrom/moveTo、批注 | FINAL不含删除旧条款；审阅上下文/批注单独可追；源文件hash不变 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-01-04 错误继续格与结构修订 | vMerge无起点/跨度不齐及不支持的cellMerge修订 | 明确错误与XML定位，转复核；不猜合并、不把旧值拼进正文 | ADAPTER_GOLDEN；NOT_RUN |

### AC-PI-F04-02 扫描表格生成结构候选

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-02-01 有线/无线/低清 | 同源扫描PDF的有线、无线、旋转、低清表 | 真实引擎输出保留；页数逐页对账，分类型报告结构/文字/拒识率 | REAL_ENGINE；NOT_RUN |
| TC-PI-F04-02-02 返回字段漂移 | 锁定版本golden raw去掉pred_html或改变table_ocr_pred类型 | E_ENGINE_SCHEMA_DRIFT，不输出假空表SUCCESS | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-02-03 不重复跑全部OCR | 既有MinerU中合法完整表与一个高风险区域 | 只适配完整表，对目标区域调用Paddle；引擎调用记录可计数 | SIMULATED_INTEGRATION；NOT_RUN |

### AC-PI-F04-03 统一裁切图和原页坐标

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-03-01 旋转缩放裁切组合 | 原图1000x800，非零crop、90度旋转、0.5缩放，含四角Gold | 逆映射回原区域误差在批准epsilon内；图hash/页号一致 | UNIT；NOT_RUN |
| TC-PI-F04-03-02 坐标单位未知 | MinerU middle有数值但单位未实测/块匹配一对多 | 只给PAGE_ONLY/NEEDS_REVIEW，不产REGION_VERIFIED | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-03-03 不可逆/越界 | 奇异矩阵、NaN、严重越界、非线性无逆映射 | 拒绝区域自动处理，不均分页面补bbox | UNIT；NOT_RUN |
| TC-PI-F04-03-04 三份真实PDF校准 | 至少3份脱敏PDF，含旋转/CropBox/不同页尺寸 | 人工逐页核对坐标回显，记录引擎/渲染器/单位，不用demo替代 | REAL_ENGINE；NOT_RUN |

### AC-PI-F04-04 检查并展开行列跨度

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-04-01 anchor覆盖 | rowspan/colspan组合、跨多个逻辑槽 | 所有虚槽指向同anchor，文字只存一次，尺寸包含覆盖格 | UNIT；NOT_RUN |
| TC-PI-F04-04-02 拒绝不合法span | 0/负数/非整数span、重叠、越界、未解释洞 | 对应错误可定位；不改成1、不前向填文字补洞 | UNIT；NOT_RUN |
| TC-PI-F04-04-03 省略位置有证据 | Word gridBefore与扫描漏识别都形成空slot | 前者有XML证据可显式omitted；后者拒绝，不能滥用omitted掩盖漏格 | ADAPTER_GOLDEN；NOT_RUN |

### AC-PI-F04-05 把OCR文字放回对应格

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-05-01 框架已有cell文字 | 框架明确cell text及token映射 | 直接采用并保存raw引用，不重新猜列 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-05-02 跨格边界和多行 | OCR框同时覆盖两格且得分接近，多行回折 | 保留歧义，不强配；仅对目标区域复识别/人工 | REAL_ENGINE；NOT_RUN |
| TC-PI-F04-05-03 数值符号与空置信度 | (1.5)、-7、1/7混淆、小数和百分号；引擎不给分数 | 符号不丢；原候选可查；缺confidence仍null | ADAPTER_GOLDEN；NOT_RUN |

### AC-PI-F04-06 建立多级表头和行标题路径

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-06-01 重复子表头 | 人民币/期限、外币/期限、左侧两层行标题 | 形成两条不同路径和行路径，引用各自header cell | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-06-02 空表头/异义跨列 | 上层表头缺失或覆盖多个异义数据列 | NEEDS_REVIEW，不能只补一个普通列名视为完成 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-06-03 路径变更影响 | 人工修正父表头 | 其下所有依赖数据格路径重算；独立另一张表不变 | E2E；NOT_RUN |

### AC-PI-F04-07 判断相邻页是不是同一续表

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-07-01 确有续表 | 相邻页同表号同列/单位/期间，第二页重复表头 | 先JOIN_CANDIDATE；确认后只生成组合view，原两表保留 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-07-02 同列数不同期间 | 列数相同但币种/期间冲突、一页两张相似表 | SEPARATE或NEEDS_REVIEW，不自动join | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-07-03 无表头续页/断行 | 第二页没有表头、首行承接上页半行 | 保留两表和待核关系，不能虚构缺表头或补值 | REAL_ENGINE；NOT_RUN |

### AC-PI-F04-08 绑定脚注和表外条件

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-08-01 同页两表同标记 | 两张表下都出现*，一条仅对一列有效 | 按证据绑定具体target；不默认最近或全表 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-08-02 单位与例外分散 | 单位在表题、主条件正文、例外脚注 | context包含三处引用；输出不丢除外限定 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F04-08-03 范围无法确定 | 有脚注但无明确标记或多个可能目标 | UNRESOLVED并阻断相关条件发布；原表和脚注可回看 | E2E；NOT_RUN |

### AC-PI-F04-09 保存人工纠错补丁并重算影响

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F04-09-01 父表头修正 | 一个header被5格/索引/外部Wiki声明依赖 | 5格和所有声明consumer标STALE并重算/事件；未连consumer=NOT_CONNECTED | E2E；NOT_RUN |
| TC-PI-F04-09-02 并发纠错冲突 | 两份patch基于同generation，第一份已提交 | 第二份E_VERSION_CONFLICT，不自动rebase或覆盖 | E2E；NOT_RUN |
| TC-PI-F04-09-03 禁止改身份与未审批发布 | replace源hash/locator或伪造reviewer字段，提交未审patch | 拒绝；preview仅派生副本，不更改原件或ACTIVE路由 | UNIT；NOT_RUN |
| TC-PI-F04-09-04 反向补丁与恢复 | 当前纠错版再做逆patch，外部ACK迟到 | 形成新版本/完整历史；迟到旧ACK不使新版本ACTIVE | E2E；NOT_RUN |

### AC-PI-F05-01 区分表格、流程图、统计图和装饰图

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F05-01-01 混合类型 | 含表格的流程图、截图文字、折线图、页眉logo | 按region分流或待人工；装饰图留附件不产事实；无重复事实 | REAL_ENGINE；NOT_RUN |
| TC-PI-F05-01-02 类型不确定 | 模型FLOW/CHART互相冲突且无可信分数 | 保留多个raw候选，classification UNKNOWN/NEEDS_REVIEW | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F05-01-03 图片指令 | 图内包含要求执行工具/泄漏文档的文字 | 只当文档内容，不触发任何工具或外联 | E2E；NOT_RUN |

### AC-PI-F05-02 抽取流程节点及箭头候选

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F05-02-01 复杂箭头 | 交叉线、双向、回流、菱形是/否、两泳道同名动作 | 不同节点ID；不按阅读顺序猜边；不明方向UNRESOLVED | REAL_ENGINE；NOT_RUN |
| TC-PI-F05-02-02 非法引用 | 模型edge指向本次不存在的node或外部ID | schema/引用校验拒绝；raw保留但不生成有效边 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F05-02-03 模型坐标不足 | 有节点文字但bbox无法映回原图 | 候选问题列表，不冒充已定位流程关系；人工可确认 | ADAPTER_GOLDEN；NOT_RUN |

### AC-PI-F05-03 检查图结构与图文一致

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F05-03-01 图有环 | 正确的退回补件流程 | 环保留；不因有环删除边或判全图无效 | UNIT；NOT_RUN |
| TC-PI-F05-03-02 正文相反 | 正文必须审批、图绕过审批，另一个漏否定分支 | CONFLICT/UNKNOWN带两边证据，阻断相关步骤成为确认知识 | REAL_ENGINE；NOT_RUN |
| TC-PI-F05-03-03 孤立点/缺端点 | 一个节点孤立，决策点只有“是”分支 | 逐节点/边问题，不自动补出口或猜“否” | UNIT；NOT_RUN |

### AC-PI-F05-04 读取统计图的轴、单位及数据

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F05-04-01 真实数值来源 | 原表存在或图印数字标签，含双轴万元和百分比 | exact_value仅来自可定位表格/标签，轴/单位/period完整 | REAL_ENGINE；NOT_RUN |
| TC-PI-F05-04-02 轴截断/无刻度 | 柱高可见但没标签，双轴不清或对数轴 | exact_value=null；默认定性/UNKNOWN，不送财务计算 | REAL_ENGINE；NOT_RUN |
| TC-PI-F05-04-03 模型编出数字 | Chart2Table给完整数字但原图不可核 | 不标PRINTED_LABEL；留模型候选，numeric estimate默认禁用 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F05-04-04 表图冲突 | 相同期间原表数值与图标签不同 | 两来源并存CONFLICT，不能用预测覆盖原表 | ADAPTER_GOLDEN；NOT_RUN |

### AC-PI-F05-05 对不一致区域局部复识别

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F05-05-01 否定词与数字分歧 | “不支持”漏“不”、1/7、单元错列三个区域 | 只重识别指定ROI并保留旧新候选、引擎和参数；不重跑全批 | REAL_ENGINE；NOT_RUN |
| TC-PI-F05-05-02 预算与独立性 | 同一模型换提示再次回答，仍冲突 | 不报独立验证；一次轮次后NEEDS_REVIEW，超时可回收 | E2E；NOT_RUN |
| TC-PI-F05-05-03 局部坐标错误 | crop矩阵不成立或定位无依据 | 拒绝局部自动重识别，转原图复核，不猜裁图框 | UNIT；NOT_RUN |

### AC-PI-F05-06 把图中发现转换为带证据的候选

| TC | 输入/触发 | 预期结果 | 证据类型 |
|---|---|---|---|
| TC-PI-F05-06-01 观察类型分开 | 同图有可见文字、模型解释和人工转录 | 三种observation_class分开，不能让模型标自己已审核 | ADAPTER_GOLDEN；NOT_RUN |
| TC-PI-F05-06-02 缺证据/换图 | 结论没有有效region，或图资产被换成相似图 | 精确结论不进入候选；hash不符fail-closed | E2E；NOT_RUN |
| TC-PI-F05-06-03 撤权失效 | 候选/缓存/导出已存在时提交来源撤权 | 每次读取拒绝，依赖标STALE；历史只留受限审计标识 | E2E；NOT_RUN |

## 5. 变异与旧基线保护

变异只在临时隔离工作树执行，逐点记录修改前/变异/还原hash、实际失败测试与还原PASS；
不在主工作树或真实数据上做破坏实验。每个点不能用注释代替修改判定分支。

| 变异 | 修改点 | 必须打红的测试 | 期望 |
|---|---|---|---|
| MU01 | 撤权墓碑守卫改成允许旧UPSERT恢复 | TC-PI-F03-02-03 | 访问/复活用例必须FAIL |
| MU02 | cursor在payload持久化前推进 | TC-PI-F03-02-02 | 崩溃恢复对账FAIL |
| MU03 | 完整清单标记恒为true | TC-PI-F03-02-02 | 漏页不得删除断言FAIL |
| MU04 | grid允许重叠或非法span改成1 | TC-PI-F04-04-02 | 结构拒绝断言FAIL |
| MU05 | 缺bbox时均分页面补值 | TC-PI-F04-03-02 | PAGE_ONLY/null断言FAIL |
| MU06 | 同名表头合并成同一路径 | TC-PI-F04-06-01 | 币种路径区分断言FAIL |
| MU07 | 模型输出数字一律填exact_value | TC-PI-F05-04-03 | 模型估读不能伪装标签断言FAIL |
| MU08 | ROI冲突时无限重试或假独立 | TC-PI-F05-05-02 | 预算/轮次/独立性断言FAIL |
| MU09 | patch跳过base generation CAS | TC-PI-F04-09-02 | 并发patch冲突断言FAIL |
| MU10 | 缺consumer ACK也ACTIVE | TC-PI-F03-03-02 | 发布拒绝断言FAIL |
| MU11 | rollback恢复旧access_epoch | TC-PI-F03-03-03 | 撤权不复活断言FAIL |
| MU12 | 临时副本改变旧UID公式或parsed字节 | G4旧UID与hash固定向量 | 保护比较FAIL，原仓库不动 |

旧证据固定向量直接以基线_evidence_uid公式计算并保存；不能拿新实现产出再当预期。
原三件套/IR/raw_vault原件完整hash前后对比；旧manifest既有键逐键相同；
SQL和封版Spec无diff。允许新伴随文件，不代表允许修改旧产物。
全量pytest在同一隔离环境前后各跑，比较具体failure/skip身份。handoff称613/4/2仅历史记录。

## 6. 验收结论的合法写法

- 设计覆盖19/19：本包已建立逐项设计与测试追溯，不意味着功能19/19实现。
- DEV自测通过：仅开发证据，不能叫独立QA。
- 适配器Gold/Mock通过：不能叫真实Paddle/VLM准确率通过。
- 本地LOCAL_READY：不能叫银行入口切换/下游图谱Wiki已发布。
- 某分项缺接口/样本：列INPUT_BLOCKED/NOT_CONNECTED和影响，不一票停止其它独立开发。
- 独立QA最终报告与Owner/本行验收仍待执行，本包不预写结论。
