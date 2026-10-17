# 03 · 文件地图（后端 46 个模块）

> 由 AST 自动生成：`LOC` = 行数；`职责` = 模块 docstring 首行；`公开 API` 仅供定位，**不要据此通读源码**。
> 查具体函数签名 → `04-code-index.md`。

| 模块 | LOC | 职责 | 公开 API（节选） | 依赖（内部） |
|---|---:|---|---|---|
| `app/__init__.py` | 0 | — | — | — |
| `app/adapters/image_preprocess.py` | 225 | 图像预处理适配器（015 plan §5.3 / §8.4 D-4）。 | `PreprocessOutput`, `preprocess_image` | `app.adapters.image_quality.import_cv2` |
| `app/adapters/image_quality.py` | 161 | 图像质量评分适配器（015 plan §5.2 / §8.3 D-3）。 | `_ScorerUnavailable`, `ImageQualityResult`, `import_cv2`, `score_image` | — |
| `app/adapters/markitdown_adapter.py` | 126 | — | `MarkItDownAdapterError`, `AdapterResult`, `MarkItDownAdapter` | `app.core.parser_routing.RouteType` |
| `app/adapters/ocr_paddle.py` | 115 | PaddleOCR 引擎适配器（015 plan §5.1 / §6 真值表）。 | `EngineDependencyMissing`, `PaddleOcrClient`, `import_paddleocr`, `check_paddleocr_available` | `app.adapters.vlm_adapter.OcrResult` |
| `app/adapters/vlm_adapter.py` | 109 | VLM 适配器（015 plan §8.2 D-2 / O-3）。 | `OcrResult`, `VlmClient`, `MockVlmClient`, `build_vlm_client`, `client_is_mock` | — |
| `app/api/__init__.py` | 0 | — | — | — |
| `app/cli/__init__.py` | 0 | — | — | — |
| `app/cli/main.py` | 1216 | — | `scan`, `copy_to_vault`, `govern_duplicates`, `route_parsers`, `parse_markitdown`, `parse_mineru_pdf` | `app.adapters.ocr_paddle.EngineDependencyMissing`, `app.adapters.ocr_paddle.check_paddleocr_available`, `app.core.config.DEFAULT_CONFIG_PATH`, `app.core.config.load_config` |
| `app/core/__init__.py` | 0 | — | — | — |
| `app/core/config.py` | 114 | — | `StorageConfig`, `MysqlConfig`, `RawConfig`, `AppConfig`, `load_config`, `ensure_readonly` | — |
| `app/core/database.py` | 42 | — | `build_database_url`, `create_db_engine`, `create_session_factory`, `session_scope` | `app.core.config.AppConfig`, `app.core.config.MysqlConfig` |
| `app/core/file_types.py` | 89 | — | `should_skip_dir`, `is_document_candidate`, `guess_mime_type` | — |
| `app/core/ids.py` | 26 | — | `normalize_path`, `compute_source_path_hash`, `compute_sha256` | — |
| `app/core/parsed_paths.py` | 33 | — | `ParsedArtifactPaths`, `build_parsed_content_dir`, `build_parsed_artifact_paths` | — |
| `app/core/parser_routing.py` | 265 | — | `RouteType`, `FutureParserHint`, `normalize_file_ext`, `ext_from_path`, `match_route_type`, `pdf_sniff_reason` | `app.core.file_types.DOCUMENT_EXTENSIONS`, `app.core.pdf_sniffing.PdfSniffResult`, `app.core.pdf_sniffing.SniffStatus` |
| `app/core/pdf_sniffing.py` | 335 | 014 PDF 内容嗅探（纯函数 + 文件缓存）。 | `SniffStatus`, `PdfSniffConfig`, `PdfSniffResult`, `load_sniff_config`, `sniff_pdf`, `cache_path_for` | — |
| `app/core/vault_paths.py` | 41 | — | `VaultArtifactPaths`, `vault_uid_for`, `build_vault_dir`, `build_vault_artifact_paths` | — |
| `app/main.py` | 11 | — | `health` | — |
| `app/models/__init__.py` | 0 | — | — | — |
| `app/models/document.py` | 49 | — | `KbDocument` | `app.models.file.Base` |
| `app/models/duplicate.py` | 30 | — | `KbDuplicateGroup` | `app.models.file.Base` |
| `app/models/evidence.py` | 68 | — | `KbDocumentChunk`, `KbEvidence` | `app.models.file.Base` |
| `app/models/file.py` | 78 | — | `Base`, `KbFileInstance`, `KbFileContent` | — |
| `app/models/parse_registry.py` | 140 | — | `KbParseRun`, `KbParseResult`, `KbParsedArtifact` | `app.models.file.Base` |
| `app/models/project.py` | 94 | — | `KbProject`, `KbProjectDocument`, `KbCuratedAsset` | `app.models.file.Base` |
| `app/models/vault.py` | 33 | — | `KbRawVaultObject` | `app.models.file.Base` |
| `app/schemas/__init__.py` | 0 | — | — | — |
| `app/schemas/search.py` | 108 | — | `SearchValidationError`, `SearchProjectNotFoundError`, `SearchQuery`, `SearchHit`, `SearchResponse` | — |
| `app/services/__init__.py` | 0 | — | — | — |
| `app/services/curated_project_assets.py` | 745 | — | `DocumentMapping`, `PlannedAsset`, `CuratedProjectBuildResult`, `CuratedProjectAssetsService`, `normalize_project_code`, `project_uid_for` | `app.core.config.AppConfig`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory`, `app.models.document.KbDocument` |
| `app/services/document_ir_builder.py` | 500 | 017 P2：把解析产物装配为 Document IR（`document_ir.json` 的纯数据层）。 | `_CellSeed`, `_PipeTableParser`, `build_from_mineru_content_list`, `build_from_markitdown_text`, `load_content_list` | `app.services.document_ir_schema.BBox`, `app.services.document_ir_schema.Block`, `app.services.document_ir_schema.ContentRef`, `app.services.document_ir_schema.CoordinateSystem` |
| `app/services/document_ir_schema.py` | 1616 | 017 Document IR（统一文档中间表示）schema 常量、纯数据结构与校验器。 | `DocumentIrValidationError`, `BBox`, `ContentRef`, `Block`, `TableCell`, `IrTable` | — |
| `app/services/duplicate_governance.py` | 464 | — | `GovernError`, `DuplicateGovernanceResult`, `DuplicateGovernanceService`, `is_copy_like_filename`, `select_master_candidate`, `build_cleanup_suggestions` | `app.core.config.AppConfig`, `app.core.config.ensure_readonly`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory` |
| `app/services/evidence_chain.py` | 744 | — | `EvidenceCandidate`, `PlannedChunk`, `PlannedEvidence`, `EvidenceChainBuildResult`, `EvidenceChainService` | `app.core.config.AppConfig`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory`, `app.core.parsed_paths.build_parsed_artifact_paths` |
| `app/services/file_content_vault.py` | 407 | — | `VaultCopyError`, `VaultCopyResult`, `FileContentVaultService` | `app.core.config.AppConfig`, `app.core.config.ensure_readonly`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory` |
| `app/services/image_parser.py` | 1196 | 015 图片解析流水线（image parse pipeline）服务。 | `ImageParseConfig`, `ImageParsedPaths`, `ParseCandidate`, `EngineRoute`, `ImageParsePlan`, `ImageParseItem` | `app.adapters.image_preprocess.PreprocessOutput`, `app.adapters.image_preprocess.preprocess_image`, `app.adapters.image_quality.ImageQualityResult`, `app.adapters.image_quality.score_image` |
| `app/services/inventory_scanner.py` | 334 | — | `ScanError`, `ScanResult`, `InventoryScanner` | `app.core.config.AppConfig`, `app.core.config.ensure_readonly`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory` |
| `app/services/markitdown_parser.py` | 603 | — | `ParseMarkitdownError`, `ParseMarkitdownItem`, `ParseMarkitdownResult`, `MarkItDownParserService` | `app.adapters.markitdown_adapter.ERROR_PARSER_RUNTIME`, `app.adapters.markitdown_adapter.MarkItDownAdapter`, `app.adapters.markitdown_adapter.MarkItDownAdapterError`, `app.adapters.markitdown_adapter.PARSER_ADAPTER_VERSION` |
| `app/services/mineru_pdf_parser.py` | 1019 | — | `SubprocessRunner`, `MagicPdfRunResult`, `MineruParseError`, `MineruParsePlan`, `MineruParseItem`, `MineruParseManyResult` | `app.core.config.AppConfig`, `app.core.config.ensure_readonly`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory` |
| `app/services/parse_quality_checker.py` | 1733 | — | `SemanticCheckConfig`, `ParseQualityCandidate`, `ParseQualityIssue`, `ParseQualityReport`, `ParseQualityCheckerService`, `load_semantic_check_config` | `app.core.config.AppConfig`, `app.core.config.PROJECT_ROOT`, `app.core.config.ensure_readonly`, `app.core.database.create_db_engine` |
| `app/services/parse_quality_report_summarizer.py` | 628 | — | `ParseQualitySummaryResult`, `ParseQualityReportSummarizerService`, `classify_noise_bucket`, `discover_latest_input_report`, `validate_input_report` | `app.core.config.AppConfig` |
| `app/services/parse_registry.py` | 1180 | — | `ParseRegistryError`, `RegistryErrorEntry`, `RegisterReportResult`, `ReconcileResult`, `ParseRegistryService`, `generate_run_uid` | `app.core.config.AppConfig`, `app.core.config.ensure_readonly`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory` |
| `app/services/parser_router.py` | 435 | — | `RouteError`, `ParserRouteDecision`, `ParserRouteResult`, `ParserRouterService` | `app.core.config.AppConfig`, `app.core.config.ensure_readonly`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory` |
| `app/services/search_service.py` | 642 | — | `SearchService`, `hit_to_dict`, `build_success_payload`, `build_error_payload` | `app.core.config.AppConfig`, `app.core.database.create_db_engine`, `app.core.database.create_session_factory`, `app.schemas.search.HIT_TYPE_RANK` |
| `app/workers/__init__.py` | 0 | — | — | — |

## 测试文件（20 个 / 587 用例）

| 文件 | 用例 | 行数 | 覆盖对象 |
|---|---:|---:|---|
| `tests/test_curated_project_assets.py` | 18 | 875 | curated project assets |
| `tests/test_document_ir.py` | 115 | 1745 | document ir |
| `tests/test_duplicate_governance.py` | 9 | 511 | duplicate governance |
| `tests/test_evidence_chain.py` | 16 | 813 | evidence chain |
| `tests/test_file_content_vault.py` | 7 | 315 | file content vault |
| `tests/test_image_parser.py` | 55 | 1145 | image parser |
| `tests/test_inventory_scanner.py` | 7 | 296 | inventory scanner |
| `tests/test_markitdown_parser.py` | 34 | 882 | markitdown parser |
| `tests/test_mineru_pdf_parser.py` | 33 | 1132 | mineru pdf parser |
| `tests/test_parse_job_registry.py` | 36 | 1233 | parse job registry |
| `tests/test_parse_quality_checker.py` | 30 | 1035 | parse quality checker |
| `tests/test_parse_quality_checker_semantic.py` | 60 | 1368 | parse quality checker semantic |
| `tests/test_parse_quality_report_summarizer.py` | 31 | 592 | parse quality report summarizer |
| `tests/test_parser_router.py` | 19 | 546 | parser router |
| `tests/test_parser_router_v2.py` | 28 | 1129 | parser router v2 |
| `tests/test_pdf_sniffing.py` | 21 | 719 | pdf sniffing |
| `tests/test_project_safety_placeholder.py` | 1 | 8 | project safety placeholder |
| `tests/test_sample_dataset.py` | 21 | 430 | sample dataset |
| `tests/test_search_service.py` | 32 | 1020 | search service |
| `tests/test_streamlit_admin_lib.py` | 14 | 271 | streamlit admin lib |
