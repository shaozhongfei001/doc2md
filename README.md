# Documents covert to markdownfile


## 2. 项目总边界

### 2.1 本项目包含

- Word / WPS 文档；
- PPT / WPS 演示；
- PDF；
- Excel / CSV；
- 图片 / 扫描件；
- 压缩包中的文档类文件；
- 投标文件；
- 招标文件；
- 方案文档；
- 需求说明书；
- 设计说明书；
- 汇报材料；
- 验收材料；
- 培训材料；
- 会议纪要；
- 业务规则和指标口径文档。

### 2.2 本项目不包含

- Java / Python / JS / Vue 等源代码分析；
- SQL / SAS 程序逻辑分析；
- Git 仓库结构分析；
- API 调用关系分析；
- 自动删除原始文件；
- 企业级权限管理；
- 多用户协同编辑。

源代码知识库应作为后续独立项目设计。

---

## 3. V1.1 核心设计原则

### 3.1 原始文件只读

系统默认不对原始文件执行：

```text
delete
rename
move
overwrite
chmod
in-place conversion
```

原始文件只做扫描、读取、计算 hash 和登记。

### 3.2 文件实例与内容对象分离

V1.1 必须区分：

```text
kb_file_instance：某个硬盘路径上的文件实例
kb_file_content：基于 SHA256 的唯一内容对象
```

这样可以处理：

- 同一文件散落在多个目录；
- 同名文件内容不同；
- 同内容文件名字不同；
- 文件被复制多次；
- 文件移动但内容不变。

### 3.3 唯一原文仓库 raw_vault

每个唯一 SHA256 内容对象可以复制一份进入：

```text
raw_vault/by_hash/{sha256_prefix}/{sha256}/
  original.bin
  original_name.txt
  source_paths.json
  file_metadata.json
```

注意：

- `raw_vault` 是唯一原文仓库；
- 原始目录仍然不动；
- 多个原始路径只指向同一个 `content_uid`；
- 同一个内容只解析一次。

### 3.4 解析产物按 hash + parser_profile 管理

解析产物保存到：

```text
parsed/by_hash/{sha256_prefix}/{sha256}/{parser_profile}/
  document.md
  document.json
  manifest.json
  quality.json
  parser.log
  tables/
  images/
```

同一内容、同一 `parser_profile` 不重复解析。

如果更换解析器版本或配置，生成新的 `parser_profile` 目录，而不是覆盖旧产物。

### 3.5 梳理成果进入 curated

梳理后的项目知识资产进入：

```text
curated/projects/{project_code}/
  00_project_card.md
  01_background.md
  02_requirements.md
  03_solution.md
  04_delivery_assets.md
  05_reusable_assets.md
  06_lessons_learned.md
  10_evidence_index.md
  source_documents.md
```

`curated` 目录保存的是“梳理成果”，不是原始文件搬家。

### 3.6 重复文件只建议，不自动删除

系统可以识别重复文件和生成清理建议，但不得自动删除。

重复文件治理流程：

```text
发现重复
  ↓
生成 duplicate_group
  ↓
选择 master candidate
  ↓
生成 cleanup suggestion
  ↓
人工确认
  ↓
可选移动到 quarantine
```

---

## 4. Cursor 项目开发目录

完整工作区目录如下：

```text
pkb_sdd_cursor/
  .cursor/
    rules/
      000-project-rules.mdc
      001-sdd-workflow.mdc
      002-python-coding.mdc
      003-database.mdc
      004-parser.mdc
      005-testing.mdc
      006-ai-boundaries.mdc

  docs/
    sdd_development_standard.md
    coding_standard.md
    database_standard.md
    parser_integration_standard.md
    ai_agent_workflow.md
    cursor_usage_guide.md
    codex_usage_guide.md
    git_workflow.md
    test_standard.md
    feature_index.md

  specs/
    000-project-charter/
    001-file-inventory/
    002-file-content-vault/
    003-duplicate-governance/
    004-parser-router/
    005-markitdown-parser/
    006-mineru-parser/
    007-quality-checker/
    008-parse-quality-checker/
    008-review-workflow/
    009-quality-report-summary/
    010-evidence-chain/
    011-curated-project-assets/
    012-search-service/
    013-streamlit-admin/
    901-docker-compose-deployment/
    902-test-dataset/

  backend/
    app/
      api/
      cli/
      core/
      models/
      schemas/
      services/
      workers/
    tests/
    requirements.txt

  frontend/
    streamlit_admin/

  config/
    app.example.yaml
    parser_rules.yaml
    value_rules.yaml
    project_taxonomy.yaml
    quality_actions.yaml

  sql/
    001_init_schema_v1_1.sql
    migrations/

  scripts/
    init_db.sh
    run_pipeline.sh
    create_sample_dataset.sh

  ai_tasks/
    cursor_task_template.md
    codex_task_template.md
    review_checklist.md
    hermes_skill_backlog.md

  data/
    .gitkeep
```

---

## 5. .cursor/rules 设计说明

`.cursor/rules` 是 Cursor 项目的核心约束。它让 Cursor 在生成、修改、重构代码时始终遵守项目边界。

### 5.1 000-project-rules.mdc

定义项目总规则：

- 不处理源代码知识库；
- 不移动、不删除、不重命名原始文件；
- 不自动删除重复文件；
- 不默认上传外部云服务；
- 数据库变更必须有 migration；
- 所有代码变更必须能追溯到 Spec。

### 5.2 001-sdd-workflow.mdc

定义 SDD 流程：

```text
先读 Spec
再读 Plan
再读 Tasks
再读 Acceptance
最后写代码
```

Cursor 不允许绕过 Spec 直接写功能。

### 5.3 002-python-coding.mdc

定义 Python 编码规则：

- 使用 Python 3.11+；
- 使用 `pathlib`；
- 使用类型注解；
- 批处理必须单文件失败不中断；
- 业务逻辑放 `services`；
- API route 不写复杂业务逻辑。

### 5.4 003-database.mdc

定义数据库规则：

- MySQL 为系统数据库；
- `file_instance` 和 `file_content` 必须分离；
- 不以文件名作为唯一标识；
- 不以路径作为内容唯一标识；
- 大文件不进入 MySQL；
- schema 变更必须写 migration。

### 5.5 004-parser.mdc

定义解析器规则：

- MarkItDown 负责普通 Office / HTML / JSON / XML；
- MinerU 负责 PDF / 图片 / 扫描件 / 高价值文档；
- 解析产物按 SHA256 + `parser_profile` 保存；
- 同一 content + parser_profile 幂等；
- 低质量输出可触发重解析。

### 5.6 005-testing.mdc

定义测试规则：

- 必须覆盖中文路径；
- 必须覆盖重复文件；
- 必须验证原始文件不被修改；
- parser 测试使用小样本；
- 不依赖外部网络。

### 5.7 006-ai-boundaries.mdc

定义 AI 工具边界：

- AI 只能在当前 Spec 范围内写代码；
- AI 不得发明数据库字段；
- AI 不得引入未授权云服务；
- 发现设计缺口先更新 Spec，再写代码。

---

## 6. docs 文档体系

### 6.1 sdd_development_standard.md

定义完整 SDD 开发规范，包括：

- Spec 包结构；
- Gate 机制；
- 完成定义；
- 验收规则；
- AI 协作规则。

### 6.2 coding_standard.md

定义 Python / FastAPI / Typer / 服务模块编码规范。

### 6.3 database_standard.md

定义 MySQL 表设计、迁移、实体关系和数据一致性要求。

### 6.4 parser_integration_standard.md

定义 MinerU / MarkItDown / DirectParser 的分工、输出目录、manifest、幂等规则和低质量重解析机制。

### 6.5 ai_agent_workflow.md

定义 Cursor、Codex、Hermes 的分工：

```text
Cursor：主开发 IDE
Codex：明确任务的大块代码生成和重构
Hermes：后续沉淀 SKILL 和项目经验
```

### 6.6 cursor_usage_guide.md

说明如何在 Cursor 中打开项目、如何给 Cursor 下任务。

### 6.7 codex_usage_guide.md

说明如何把明确任务交给 Codex，不让它扩大范围。

### 6.8 git_workflow.md

定义分支命名和 commit 规范。

### 6.9 test_standard.md

定义测试样本、测试类型、必测场景。

### 6.10 feature_index.md

列出所有 Spec 的状态和开发阶段。

---

## 7. specs 设计说明

每个 Spec 都是一个完整功能包，包含：

```text
spec.md
plan.md
tasks.md
acceptance.md
test_cases.md
```

### 7.1 spec.md

说明：

- 为什么做；
- 做什么；
- 不做什么；
- 输入是什么；
- 输出是什么；
- 业务规则是什么。

### 7.2 plan.md

说明：

- 相关模块；
- 相关数据库表；
- 核心流程；
- 状态设计；
- 异常处理。

### 7.3 tasks.md

拆成 Cursor / Codex 可执行任务。

### 7.4 acceptance.md

定义验收标准。

### 7.5 test_cases.md

定义测试用例。

---

## 8. 第一批 MVP Specs

### 8.1 000-project-charter

定义项目总章程，包括项目目标、范围、非范围、核心原则。

### 8.2 001-file-inventory

实现文件盘点和资产登记。

核心目标：

```text
扫描目录 → 识别候选文档 → 计算路径哈希 → 计算 SHA256 → 写入 MySQL
```

核心输出：

```text
kb_file_instance
kb_file_content
扫描报告
```

### 8.3 002-file-content-vault

实现唯一原文仓库 raw_vault。

核心目标：

```text
同一 SHA256 只复制一份到 raw_vault
```

核心输出：

```text
raw_vault/by_hash/{sha256_prefix}/{sha256}/
  original.bin
  original_name.txt
  source_paths.json
  file_metadata.json
```

### 8.4 003-duplicate-governance

实现重复文件治理。

核心目标：

```text
识别重复 → 分组 → 主文件建议 → 清理建议 → 人工确认
```

不允许自动删除。

### 8.5 004-parser-router

实现解析路由。

核心规则：

```text
PDF / 图片 / A类核心文档 → MinerU
普通 Office / HTML / XML / JSON → MarkItDown
CSV / TXT / MD → DirectParser
```

### 8.6 005-markitdown-parser

实现普通文档 Markdown 转换。

核心输出：

```text
document.md
manifest.json
parser.log
```

### 8.7 006-mineru-parser

实现 PDF / 图片 / 扫描件 / 高价值文档解析。

核心输出：

```text
document.md
document.json
tables/
images/
manifest.json
parser.log
```

### 8.8 007-quality-checker

实现解析质量检查。

核心指标：

- `markdown_length`；
- `heading_count`；
- `table_count`；
- `image_count`；
- `garbled_ratio`；
- `quality_score`；
- `quality_status`。

### 8.9 008-review-workflow（FUTURE STUB / NOT CURRENT）

> 这不是已完成的 `008-parse-quality-checker`。权威状态见 `specs/SPEC_INDEX.md` §3。

实现人工复核闭环（未来 Spec，当前未启动）。

复核对象：

- 价值等级；
- 项目归属；
- 重复文件主文件；
- 低质量文档；
- 蒸馏结果。

---

## 9. 后续扩展 Specs

> 权威索引见 `specs/SPEC_INDEX.md`。目录编号以 SPEC_INDEX 为准。

### 9.1 009-quality-report-summary（DONE）

只读消费 008 `parse_quality_report.json`，输出 Markdown / JSON 摘要；不连 MySQL、不读 raw_vault / parsed、不修复。

### 9.2 010-evidence-chain（DONE）

从 parsed 三件套构建证据链，写入 `kb_document_chunk` / `kb_evidence`：

- document_chunk（section / page MVP）
- char offset / page_no / bbox（best-effort）
- quote_text / source_location / evidence_uid

只读 parsed + SELECT registry；不调用 parser、不写 curated/embedding/review。

### 9.3 011-curated-project-assets（DONE）

从 010 evidence + registry 元数据生成项目化知识资产（**规则/模板 MVP**，非 LLM 蒸馏）：

```text
curated/projects/{project_code}/
  00_project_card.md       # MVP
  10_evidence_index.md     # MVP
  source_documents.md      # MVP
```

- CLI: `build-curated-project`
- 写入 `kb_project` / `kb_project_document` / `kb_curated_asset`
- 产物引用 `evidence_uid` / `content_uid` / `document_uid`
- **不做：** LLM 蒸馏、embedding、review workflow、parser 调用、search-service、Streamlit

权威边界见 `specs/SPEC_INDEX.md` §4.3。

### 9.4 012-search-service（DONE）

基于 MySQL FULLTEXT 的**只读**检索服务（无 embedding、无 parser、无 raw_vault/parsed 读取）：

- 检索域（MVP）：`kb_document.title`、`kb_document_chunk.content`、`kb_evidence` 文本、`kb_project`、`kb_curated_asset.asset_title`
- CLI: `search-kb`（`--query`、`--scope`、`--project-code`、`--limit`、`--offset`）
- `scope=all`：五 scope COUNT+SELECT → merge → relevance 排序 → global offset/limit
- 命中须带 `evidence_uid` / `document_uid` / `content_uid`（适用时）
- `--project-code` 过滤经 `kb_project_document`（不依赖 `kb_evidence.project_uid` backfill）
- **不做：** LLM、embedding、review workflow、parser、Streamlit、DB 写入（MVP SELECT-only）

权威边界见 `specs/SPEC_INDEX.md` §4.3。

### 9.5 013-streamlit-admin（DONE）

只读 Streamlit 管理台（无 parser、无 pipeline 触发、无 DB 写入 MVP）：

- 页面（MVP）：KB Search、Evidence Explorer、Projects & Curated、Parse Registry、Quality Reports、Inventory Snapshot
- Search：进程内调用 `SearchService`（与 CLI `search-kb` 同合同）
- 只读：`reports_root` 下 008/009 报告；`curated_root` 下 Markdown
- SELECT：`kb_document` / chunk / evidence / project / curated / parse registry 等
- 启动：`PYTHONPATH=backend streamlit run frontend/streamlit_admin/app.py`
- **不做：** 触发 inventory/vault/parse/build CLIs、review workflow、LLM/embed、raw_vault/parsed 读取、原始文件变更

权威边界见 `specs/SPEC_INDEX.md` §4.4。

### 9.6 901-docker-compose-deployment

实现本地 Docker Compose 部署。

### 9.7 902-test-dataset

构造不含敏感资料的小样本测试集。

---

## 10. backend 后端目录说明

```text
backend/app/
  api/       FastAPI 路由
  cli/       Typer CLI 命令
  core/      配置、数据库、日志、工具函数
  models/    SQLAlchemy ORM
  schemas/   Pydantic DTO
  services/  核心业务服务
  workers/   后续任务 Worker
```

第一阶段优先实现：

```text
services/inventory_scanner.py
services/file_content_vault.py
services/duplicate_detector.py
services/parser_router.py
services/markitdown_parser.py
services/mineru_parser.py
services/quality_checker.py
services/review_service.py
```

---

## 11. config 配置说明

### 11.1 app.example.yaml

定义：

- storage 根路径；
- MySQL 连接；
- parser 参数；
- quality 阈值；
- raw 文件保护规则。

### 11.2 parser_rules.yaml

定义解析器路由规则。

### 11.3 value_rules.yaml

定义 A/B/C/D 价值分层关键词。

### 11.4 project_taxonomy.yaml

定义项目词典，用于项目归属推断。

### 11.5 quality_actions.yaml

定义质量检查后的动作：

```text
低质量 MarkItDown → MinerU 重解析
空 Markdown → 人工复核
乱码率高 → 人工复核
```

---

## 12. sql 目录说明

```text
sql/
  001_init_schema_v1_1.sql
  migrations/
```

如果后续修改表结构，必须新增 migration，不允许直接改已发布脚本。

---

## 13. scripts 目录说明

### 13.1 init_db.sh

初始化 MySQL 数据库。

### 13.2 run_pipeline.sh

执行基础流水线：

```text
scan
build-parse-queue
parse
quality-check
```

### 13.3 create_sample_dataset.sh

创建小样本测试文件，避免使用真实敏感资料测试。

---

## 14. ai_tasks 目录说明

### 14.1 cursor_task_template.md

给 Cursor 的任务模板。

### 14.2 codex_task_template.md

给 Codex 的任务模板。

### 14.3 review_checklist.md

代码和功能 Review 清单。

### 14.4 hermes_skill_backlog.md

后续可沉淀的 Hermes SKILL：

```text
PERSONAL_KB_SPEC_REVIEW_SKILL
PERSONAL_KB_FILE_INVENTORY_SKILL
PERSONAL_KB_DUPLICATE_GOVERNANCE_SKILL
PERSONAL_KB_PARSE_DEBUG_SKILL
PERSONAL_KB_PROJECT_DISTILL_SKILL
PERSONAL_KB_ACCEPTANCE_REVIEW_SKILL
```

---

## 15. Cursor 开发流程

### 15.1 打开项目

```bash
cursor pkb_sdd_cursor
```

### 15.2 开发第一个功能

建议从 `001-file-inventory` 开始。

给 Cursor 的提示词：

```text
你现在是本项目的开发助手。
请严格遵守 .cursor/rules。
当前开发 Spec 是 specs/001-file-inventory。
请先阅读 spec.md、plan.md、tasks.md、acceptance.md、test_cases.md。
然后只实现 tasks.md 中 T001-T003。
不要扩大范围。
不要移动、删除、重命名任何原始文件。
```

### 15.3 开发后检查

每次提交前检查：

```text
是否对应明确 Spec？
是否只实现 tasks？
是否满足 acceptance？
是否有测试？
是否有数据库 migration？
是否误动 raw_vault、parsed、curated？
是否违反原始文件只读原则？
```

---

## 16. Codex 使用方式

Codex 适合处理明确任务，例如：

```text
请基于 specs/001-file-inventory 和 sql/001_init_schema_v1_1.sql，
补全 backend/app/models/file.py 和 backend/app/services/inventory_scanner.py。
禁止修改其他模块。
完成后补 tests/test_inventory_scanner.py。
```

不建议给 Codex 的任务：

```text
帮我把整个项目都做完
重构所有代码
你看着办
顺便把前端也做了
```

---

## 17. Hermes 使用方式

Hermes 不建议作为第一阶段主开发工具。

更适合后续做：

- Spec Review；
- 开发经验复盘；
- 解析问题排查；
- 项目蒸馏流程沉淀；
- SKILL 生成。

---

## 18. 推荐开发顺序

### 阶段一：MVP 文件治理和解析闭环

```text
000-project-charter
001-file-inventory
002-file-content-vault
003-duplicate-governance
004-parser-router
005-markitdown-parser
006-mineru-parser
007-quality-checker
008-review-workflow
```

目标：

```text
扫描目录
登记文件实例和内容对象
复制唯一内容到 raw_vault
识别重复
生成解析任务
调用 MarkItDown / MinerU
生成 parsed 产物
质量评分
进入人工复核
```

### 阶段二：知识资产和检索

```text
009-quality-report-summary
010-evidence-chain
011-curated-project-assets
012-search-service
013-streamlit-admin
```

目标：

```text
证据链
项目卡
源文档清单
全文检索
管理台
```

### 阶段三：部署和测试

```text
901-docker-compose-deployment
902-test-dataset
```

目标：

```text
本地可部署
有测试样本
可回归验证
```

---

## 19. 验收标准

一套 SDD Cursor 工作区合格的标准是：

```text
1. Cursor 打开后能自动读取 .cursor/rules。
2. 每个 Spec 有 spec/plan/tasks/acceptance/test_cases。
3. docs 里有开发、数据库、解析器、AI 协作规范。
4. backend 有基础目录骨架。
5. config 有 V1.1 配置模板。
6. sql 有 V1.1 初始化脚本位置。
7. scripts 有初始化和流水线脚本。
8. ai_tasks 有 Cursor/Codex/Hermes 模板。
9. README 能指导开发者从 001-file-inventory 开始。
10. 所有规范都强调原始文件只读和不自动删除重复文件。
```

---

## 20. 一句话总结

这套 SDD Spec + Cursor 项目目录的目的不是“多写文档”，而是让 Cursor、Codex、Hermes 都被约束在同一套工程边界内：

```text
V1.1 详细设计负责定义系统；
SDD Spec 负责定义功能；
Cursor Rules 负责约束 AI；
Tasks 负责驱动编码；
Acceptance 负责防止失控。
```

最终目标是把个人历史项目文档真正治理成：

```text
可盘点
可去重
可解析
可追溯
可复核
可蒸馏
可检索
可复用
```
