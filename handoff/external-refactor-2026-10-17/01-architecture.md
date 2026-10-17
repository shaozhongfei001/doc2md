# 01 · 架构总览

## 1. 系统定位与边界

**做什么**：把异构文档变成结构化、可追溯、可检索的知识资产。
**不做什么**（明确的设计边界）：

- 不做在线协作 / 不做文档编辑（原件只读）
- 不做向量库（当前检索是 MySQL FULLTEXT；`kb_embedding_ref` 仅为占位表）
- 不做 Web API 服务（只有 CLI + 只读 Streamlit 管理界面）
- 不在 DB 存正文（正文在磁盘，DB 只存登记/索引/证据）

---

## 2. 分层结构

```
┌─────────────────────────────────────────────────────────────────────┐
│ 入口层                                                              │
│   backend/app/cli/main.py        Typer CLI，19 个子命令（唯一操作入口）│
│   frontend/streamlit_admin/      Streamlit 只读管理界面（013）        │
│   scripts/*.sh|py                建库/造样例数据/跑流水线             │
├─────────────────────────────────────────────────────────────────────┤
│ 服务层（业务编排，全部"一个服务管一个阶段"）                          │
│   inventory_scanner          扫描 + sha256 + 登记 kb_file_instance   │
│   duplicate_governance       精确重复治理（内容/主实例）              │
│   file_content_vault         内容寻址入库 raw_vault                   │
│   parser_router              路由决策（按扩展名 + 014 PDF 嗅探）      │
│   markitdown_parser          Office/文本 → 三件套                    │
│   mineru_pdf_parser          PDF → 三件套 + ir_source（017）          │
│   image_parser               PDF页/图片 → OCR/VLM 三件套（015）       │
│   parse_registry             解析任务/结果/产物登记（唯一 DB 写路径） │
│   parse_quality_checker      质量检查（8 类 + 016 语义 5 类）          │
│   parse_quality_report_summarizer  质量报告汇总（009/016）            │
│   evidence_chain             chunk 切分 + 证据生成（010 + 017 扩展）  │
│   curated_project_assets     规则/模板 MVP（011）                     │
│   search_service             只读 FULLTEXT 检索（012）                │
│   document_ir_schema         IR 契约 + 纯函数校验器（017 P1）         │
│   document_ir_builder        content_list → IR / 降级 IR（017 P2）    │
├─────────────────────────────────────────────────────────────────────┤
│ 适配器层（外部依赖隔离，可替换）                                      │
│   adapters/ocr_paddle        PaddleOCR（可选依赖，缺失优雅降级）       │
│   adapters/vlm_adapter       VLM Protocol + Mock（真实 VLM 未接入）    │
│   adapters/image_preprocess  去噪/二值化/纠偏（cv2，缺失降级）         │
│   adapters/image_quality     清晰度评分（Laplacian / Pillow 回退）     │
├─────────────────────────────────────────────────────────────────────┤
│ 核心层（无业务语义的基础设施）                                        │
│   core/config          YAML 配置加载（app.yaml）                      │
│   core/database        engine / session_factory                       │
│   core/ids             sha256 计算                                    │
│   core/vault_paths     raw_vault 路径派生                             │
│   core/parsed_paths    parsed 产物路径派生（017 增 3 键）              │
│   core/parser_routing  路由规则表 + fail-closed 不变式（014）         │
│   core/pdf_sniffing    PDF 文本层嗅探 + 内容寻址缓存（014）           │
│   models/*             SQLAlchemy 模型（22 张表）                      │
└─────────────────────────────────────────────────────────────────────┘
```

**依赖方向**：`cli → services → (core, adapters, models)`。`core` 不依赖 `services`。
唯一例外：`adapters/image_preprocess` 复用 `adapters/image_quality.import_cv2`（同层内）。

---

## 3. 两条主数据流

### 3.1 文档主线（PDF / Office / 文本）

```
① 扫描登记        inventory_scanner.scan(dir)
                  → kb_file_instance (path, sha256, ext) + kb_file_content (去重后唯一内容)
② 去重治理        duplicate_governance
                  → 选主实例（master_file_instance_uid），重复实例不重复入库
③ 内容入库        file_content_vault.copy_to_vault(sha256)
                  → raw_vault/by_hash/ab/cd/<sha>/original.bin（原件副本，只读）
④ 路由            parser_router.route_parsers()
                  → 扩展名 + PDF 文本层嗅探（014）→ route_type
                    PDF_DIGITAL / PDF_SCANNED_OR_IMAGE / MARKITDOWN / ROUTE_MISMATCH / UNKNOWN
⑤ 解析            mineru_pdf_parser（PDF）或 markitdown_parser（Office/文本）
                  → parsed/by_hash/ab/cd/<sha>/
                       parsed_text.md          （正文，Markdown）
                       parsed_metadata.json    （解析元数据）
                       parse_manifest.json     （产物清单 + asset sha256 + ir_source_files）
                       assets/images/*.jpg     （抽出的图/表截图）
                       ir_source/content_list.json + middle.json   （017 新增，PDF 结构化中间件）
⑥ 质量检查        parse_quality_checker.check()
                  → 8 类结构/完整性检查 + 016 五类语义检查 → 报告 JSON
⑦ 报告汇总        parse_quality_report_summarizer.summarize()（009，schema 1.0/1.1）
⑧ 证据链          evidence_chain.build()
                  → kb_document_chunk（按 token 上限切块）
                  → kb_evidence（section_quote / table_cell / image_region）
⑨ 检索            search_service.search()（MySQL FULLTEXT，只读）
⑩ 管理界面        Streamlit 只读（013）
```

### 3.2 图片线（015，与主线共享三件套契约）

```
候选（kb_parse_result 中 route_type=IMAGE）
  → 预处理（deskew/denoise/binarize，cv2；缺失则全部 skip 并如实标注）
  → 质量评分（Laplacian 方差；无 cv2 回退 Pillow 梯度）
  → 引擎路由（引擎真值表：clear+avail→PaddleOCR / low→VLM / missing→降级）
  → OCR/VLM（Mock 结果**必须被拒绝落盘**）
  → 三件套（parser_name=image_ocr，parser_profile 区分 paddleocr/vlm）
```

### 3.3 IR 子链（017，PDF 主线的下游）

```
ir_source/content_list.json + parsed_text.md + parse_manifest.asset_files
  → document_ir_builder.build_from_mineru_content_list()
  → document_ir.json（schema document-ir/1.0）
  → validate_document_ir()（13 组校验，fail-closed）
  [后续 P3/P4/P5 未完成：tables/figures 深度校验、渲染 rendered.md、CLI、证据链接入]
```

---

## 4. 存储布局（磁盘）

```
<workspace>/
├── source_registry/          源目录登记（可选）
├── raw_vault/by_hash/ab/<sha>/original.bin        原件副本（只读，唯一真源）
├── parsed/by_hash/ab/cd/<sha>/                    解析产物（三件套 + assets + ir_source）
├── curated/                                       精编资产（011）
├── quarantine/                                    隔离区（解析失败/异常文件）
├── reports/                                       质量报告 / 路由报告 / 汇总
└── .staging/                                      解析临时目录（成功后清理）
```

**路径派生唯一入口**：`core/vault_paths.py`、`core/parsed_paths.py`。禁止在业务代码里手拼路径。

---

## 5. 数据库（22 张表，MySQL 8）

| 域 | 表 | 作用 |
|---|---|---|
| 文件 | `kb_file_instance` / `kb_file_content` | 实例（路径级）与内容（sha 级，去重后唯一） |
| 去重 | `kb_duplicate_group` / `kb_version_candidate_group` | 精确重复组 / 版本候选组 |
| 仓库 | `kb_raw_vault_object` | 原件副本登记（vault_uid、sha、状态） |
| 解析任务 | `kb_parse_job` / `kb_parse_run` / `kb_parse_result` | 任务 / 运行 / 结果（含 route_type、parser_name） |
| 产物 | `kb_parsed_artifact` | 产物登记（artifact_type：parsed_text / metadata / manifest / document_ir / rendered_md …，**VARCHAR 列，零迁移**） |
| 文档 | `kb_document` / `kb_document_chunk` | 文档与切块（`start_offset/end_offset`） |
| 证据 | `kb_evidence` | 证据行（`source_char_start/end`、`evidence_uid`、locator JSON） |
| 质量 | `kb_document_quality` | 质量结果 |
| 精编 | `kb_project` / `kb_project_document` / `kb_curated_asset` / `kb_review_item` / `kb_manual_correction` | 011 规则/模板 MVP |
| 检索 | `kb_embedding_ref` | **占位**（未接向量库） |
| 运维 | `kb_task_log` / `kb_schema_version` | 任务日志 / schema 版本 |

> **重要**：`kb_document_chunk.start_offset/end_offset` 与 `kb_evidence.source_char_start/source_char_end`
> 是**两套字段名**，语义对应但不可互改（见 `02-design-decisions.md` D-14）。

---

## 6. CLI（唯一操作入口，19 个命令）

| 阶段 | 命令 | 幂等/干跑 |
|---|---|---|
| 采集 | `copy-to-vault`、`govern-duplicates` | 是 |
| 路由 | `route-parsers`（`--routing-rules-version` / `--sniff-cache` / `--dry-run`） | 是 |
| 解析 | `parse-mineru-pdf`、`parse-markitdown`、`parse-images`、`parse`（占位） | 是（`--force` 重建） |
| 登记 | `register-parse-report`、`build-parse-queue` | 是 |
| 查询 | `list-parse-jobs`、`show-parse-job`、`list-parse-results`、`list-parsed-artifacts`、`reconcile-parsed-artifacts` | 只读 |
| 质量 | `check-parse-quality`（`--no-semantic`）、`summarize-parse-quality` | 是 |
| 下游 | `build-evidence-chain`、`build-curated-project`、`search-kb` | 是 / 只读 |
| IR（未实现） | `build-document-ir`（017 P4 待做） | — |

---

## 7. 运行时不变式（重构时不可破坏）

| # | 不变式 | 实现位置 |
|---|---|---|
| I-1 | 原件/仓库只读 | `core/config.ensure_readonly()`，服务构造时调用 |
| I-2 | 解析结果幂等（同 sha + 同 adapter 版本不重复写） | 各 parser 的 `plan_one` / `ALREADY_SUCCESS` |
| I-3 | `--dry-run` 零写入（不进 staging、不写报告、不写 DB） | parser_router / quality / IR(P4) |
| I-4 | 校验失败不落盘半成品 | `document_ir_schema`、mineru asset 对齐 |
| I-5 | 缺数据 `null` + 计数，不估算 | IR builder、quality checker |
| I-6 | 单条失败不中断批处理 | 所有 `parse_many` / `check` 循环 |
| I-7 | 写 DB 仅经 `ParseRegistryService` | 其余服务禁止 raw SQL 写 |

---

## 8. 规模与分布（重构工作量的依据）

| 区域 | 文件 | 行数 | 说明 |
|---|---|---|---|
| `backend/app/services` | 18 | ~9,700 | 业务复杂度集中地（`parse_quality_checker` 1,733 / `document_ir_schema` 1,616 / `image_parser` 1,196 / `parse_registry` 1,180 / `mineru_pdf_parser` 1,019） |
| `backend/app/cli` | 1 | 1,216 | 19 个子命令，单文件偏大（重构候选） |
| `backend/app/core` | 7 | ~1,200 | 基础设施，稳定 |
| `backend/app/adapters` | 4 | ~610 | 外部依赖隔离 |
| `backend/app/models` | 1 | ~450 | 22 张表 |
| `backend/tests` | 20 | 16,161 | 587 用例（`test_document_ir.py` 1,745 行 / 115 例最大） |
| `frontend/streamlit_admin` | 2 | 1,207 | 只读界面 |

**复杂度热点（重构优先看）**：`parse_quality_checker`（8+5 类检查全塞一个类）、`cli/main.py`（19 命令单文件）、`image_parser`（预处理+评分+路由+落盘）、`mineru_pdf_parser`（1,019 行，含 014 嗅探注入与 017 ir_source）。
