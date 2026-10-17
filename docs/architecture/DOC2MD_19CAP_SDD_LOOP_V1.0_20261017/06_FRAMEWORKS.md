# 成熟框架选型与接入说明

状态：DESIGN_CANDIDATE；检索日期2026-10-17。以下选取工程验证起点，不把上游latest或官方性能数字当本行实测。
代码和模型分别锁版本；服务接口兼容不等于信创兼容。新引擎只在受控worker/内网部署，不默认外传材料。

## 1. 主选组合

| 能力 | 选定方向 | 复用什么 | 项目需要开发什么 | 采用条件 |
|---|---|---|---|---|
| 原生DOCX | python-docx 1.2.0 + 其lxml/OOXML | OOXML包、关系/表格对象与XML节点解析 | FINAL视图过滤、原生gridSpan/vMerge和locator保真适配 | 本地锁定venv，Gold；不靠row.cells重复值猜merge |
| PDF主基线 | 保留当前MinerU/magic-pdf配置 | 已有raw vault/三件套/content_list/middle/资产 | 只读映射、坐标唯一绑定、高风险区域识别 | 不为本期默认升级magic-pdf；demo所用1.3.12非本行标准 |
| 扫描表结构 | PP-StructureV3，工程参考PaddleOCR v3.3.3 | 版面/表结构/OCR/已有模型推理 | 原始结果归档、严格JSON适配、page/cell/text映射、异常复核 | B04锁Paddle/PaddleX/模型/驱动，验证raw字段；无本行硬件证明 |
| 复杂文档兜底 | PaddleOCR-VL（可选关闭） | 文档视觉解析 | 输出adapter、定位与质量差分 | 主路失败且POC有收益再启；缺定位不能声称格级证据 |
| 统计图 | Paddle ChartParsing / PP-Chart2Table | 图转表推理 | 轴/单位/series/point映射与可见数字/原表核对 | 具体API/权重必须在锁定版本验证；模型输出数字仍候选 |
| 流程/复杂图 | Qwen3-VL-Instruct族的批准内网服务 | 视觉理解/定位候选/结构化输出 | OCR context、严格schema、端点/图文检查、一次ROI和人工复核 | model_id/revision/权重、推理服务协议/设备待实际批准；不在doc2md进程加载大模型 |
| schema | 既有Pydantic 2.x方向 | 类型、closed schema、JSON parse/validator | 新伴随合同、跨对象/权限/原子提交控制 | 原项目2.10.4；本包参考模型本轮在2.14.0验证，目标版本需L00复验 |
| 纠错patch | python-json-patch / RFC6902 | test/replace和JSON Pointer处理 | 字段白名单、基版CAS、review、依赖重算 | 锁版本及SBOM；不复制一个自研通用patch库 |
| 来源同步 | Python Protocol；借鉴WeKnora Connector接口思想 | 能力、全量/增量/cursor/checkpoint职责拆分 | 本地manifest与具体银行源adapter、权限/墓碑/对账 | **零WeKnora运行依赖**；银行源无接口就NOT_CONNECTED |

不要引入GraphRAG/LLM Wiki/LangGraph/RAG/向量库来完成这19项预处理；本期不含对应运行服务。
今后完整四智能体编排或图谱建设有独立需求时再选型，本包只输出可消费的候选与失效契约。

## 2. WeKnora最小引用的精确含义

- 不clone/vendor/import其Go主服务、DB模型、任务队列、权限系统或DocReader作为默认依赖。
- 不复制WeKnora代码；只引用其官方接口/职责作为设计参考。若后续确需复制代码，先固定commit并做许可/依赖审查。
- DocReader的ReadStream传解析元信息与图片；StreamingConnector传外部源条目和checkpoint。
- Docx2优先MarkItDown，fallback自研Docx；该行为不能证明原生Word合并格全保真。
- builtin PDF逐页文本/扫描处理，扫描OCR在Go主服务；不能当现成cell候选审核闭环。
- anydoc的PDF文字层和纵向合并值保留方式有局限，不选它替代本项目表格恢复。

这是对Excel框架实现候选的明确调整，详DX-01/02/09，不是删除其中业务功能。

## 3. 开发接线顺序

1. 读既有MinerU raw并捕获target版本真实Paddle结果；不要先用自编JSON冒充真实引擎。
2. 通过薄adapter统一为新schema；raw返回及版本不丢。模型升级先过adapter golden，字段漂移显式报错。
3. geometry保留引擎原单位、渲染尺寸和所有转换；只映射可证明坐标。
4. schema后依次跑引用/grid/权限/证据校验；有错误保留raw+issue，不能保存半成品为合法产物。
5. 语义标题/续表/脚注与视觉候选分层；确定结构、模型推断和人工确认分别留证。
6. worker和模型依赖锁定后才执行性能/质量POC；不得把官方A100数字写成本行吞吐。

## 4. 官方依据与版本说明

| 来源ID | 官方地址 | 本次核验内容与限制 |
|---|---|---|
| FW01 | https://python-docx.readthedocs.io/en/latest/user/tables.html | 1.2.0表格访问、非均匀/省略格；文档为latest，库版本选1.2.0 |
| FW02 | https://python-docx.readthedocs.io/en/latest/dev/analysis/features/table/cell-merge.html | 合并anchor、gridSpan/vMerge的原生结构；未复制原文算法 |
| FW03 | https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.verticalmerge?view=openxml-3.0.1 | OOXML纵向合并语义；不引入.NET运行依赖 |
| FW04 | https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.3.3 | 发布存在，2026-01-20；短commit c0522b8只作来源，构建须锁全commit/包hash |
| FW05 | https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html | 表结构/OCR模块与候选返回字段；latest不能替代v3.3.3真实响应 |
| FW06 | https://www.paddleocr.ai/latest/en/version3.x/module_usage/chart_parsing.html | PP-Chart2Table图转表；模型输出无本项目逐点确认保证，精确API待锁版 |
| FW07 | https://github.com/QwenLM/Qwen3-VL | 模型族、视觉/服务使用；旧Qwen2.5链接当前重定向该仓库，未锁模型revision |
| FW08 | https://python-json-patch.readthedocs.io/en/latest/ | RFC6902 patch库；本包仅用test/replace白名单 |
| WK01 | https://github.com/Tencent/WeKnora/blob/main/docreader/proto/docreader.proto | ReadStream/SourceBlock单位和offset；不据main宣称目标版本已冻结 |
| WK02 | https://github.com/Tencent/WeKnora/blob/main/website-docs/03-features/03-document-parsing.md | 图像处理和解析器职责/限制 |
| WK03 | https://github.com/Tencent/WeKnora/blob/main/website-docs/06-development/03-extension-points.md | Connector/StreamingConnector扩展接口职责 |
| WK04 | https://github.com/Tencent/WeKnora/blob/main/website-docs/03-features/10-datasource.md | 同步/删除/checkpoint能力因来源不同而异 |

没有复制第三方源码/权重；无论MIT/Apache名称还是官方兼容示例，均不能代替选定依赖SBOM与本行目录核验。
