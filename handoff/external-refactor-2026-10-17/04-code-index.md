# 04 · 代码索引（类/函数签名，AST 生成）

> 用途：**查符号**，不要通读。若需看实现，用行号跳转（文件内行数见 `03-file-map.md`）。
> 生成口径：仅列模块顶层 `class` 与其**公开方法**、顶层公开函数（`_` 前缀不列）。

## `app/adapters/image_preprocess.py` (225 行)
> 图像预处理适配器（015 plan §5.3 / §8.4 D-4）。

- **class `PreprocessOutput`** — 方法：—
- `def preprocess_image(image_path, staging_dir, ops)`

## `app/adapters/image_quality.py` (161 行)
> 图像质量评分适配器（015 plan §5.2 / §8.3 D-3）。

- **class `_ScorerUnavailable`** — 方法：—
- **class `ImageQualityResult`** — 方法：available
- `def import_cv2()`
- `def score_image(image_path, scorer)`

## `app/adapters/markitdown_adapter.py` (126 行)

- **class `MarkItDownAdapterError`** — 方法：—
- **class `AdapterResult`** — 方法：—
- **class `MarkItDownAdapter`** — 方法：check_import, convert

## `app/adapters/ocr_paddle.py` (115 行)
> PaddleOCR 引擎适配器（015 plan §5.1 / §6 真值表）。

- **class `EngineDependencyMissing`** — 方法：—
- **class `PaddleOcrClient`** — 方法：recognize
- `def import_paddleocr()`
- `def check_paddleocr_available()`

## `app/adapters/vlm_adapter.py` (109 行)
> VLM 适配器（015 plan §8.2 D-2 / O-3）。

- **class `OcrResult`** — 方法：—
- **class `VlmClient`** — 方法：recognize
- **class `MockVlmClient`** — 方法：recognize
- `def build_vlm_client(vlm_config)`
- `def client_is_mock(client)`

## `app/cli/main.py` (1216 行)

- `def scan(path, config, source_root)`
- `def copy_to_vault(config, limit, sha256, content_uid, refresh_metadata_only)`
- `def govern_duplicates(config, limit, sha256, content_uid)`
- `def route_parsers(config, limit, sha256, content_uid, rules_version, sniff_cache, dry_run)`
- `def parse_markitdown(config, sha256, content_uid, limit, dry_run)`
- `def parse_mineru_pdf(config, sha256, content_uid, limit, dry_run, force, timeout, register)`
- `def register_parse_report(report_path, config, dry_run)`
- `def list_parse_jobs(config, limit, status, parser_name)`
- `def show_parse_job(run_uid, config, include_results, include_artifacts)`
- `def list_parse_results_cmd(config, run_uid, content_uid, sha256, status, limit)`
- `def list_parsed_artifacts_cmd(config, content_uid, sha256, artifact_type, limit)`
- `def reconcile_parsed_artifacts_cmd(config, sha256, content_uid, limit, dry_run)`
- `def check_parse_quality(config, sha256, content_uid, parser_name, status, limit, output, fail_on_issue)`
- `def summarize_parse_quality(config, input_path, output, output_format, severity, issue_code, parser_name, top)`
- `def build_evidence_chain(config, content_uid, sha256, limit, dry_run, force, output)`
- `def build_curated_project(config, project_code, project_name, content_uid, manifest, limit, dry_run, force)`
- `def search_kb(config, query, scope, project_code, content_uid, document_uid, limit, offset)`
- `def build_parse_queue()`
- `def parse(limit)`
- `def parse_images(config, sha256, content_uid, limit, engine, timeout, dry_run, force)`

## `app/core/config.py` (114 行)

- **class `StorageConfig`** — 方法：—
- **class `MysqlConfig`** — 方法：—
- **class `RawConfig`** — 方法：—
- **class `AppConfig`** — 方法：—
- `def load_config(config_path)`
- `def ensure_readonly(config)`

## `app/core/database.py` (42 行)

- `def build_database_url(mysql)`
- `def create_db_engine(config)`
- `def create_session_factory(engine)`
- `def session_scope(session_factory)`

## `app/core/file_types.py` (89 行)

- `def should_skip_dir(name)`
- `def is_document_candidate(path)`
- `def guess_mime_type(path)`

## `app/core/ids.py` (26 行)

- `def normalize_path(path)`
- `def compute_source_path_hash(path)`
- `def compute_sha256(path, chunk_size)`

## `app/core/parsed_paths.py` (33 行)

- **class `ParsedArtifactPaths`** — 方法：—
- `def build_parsed_content_dir(parsed_root, sha256)`
- `def build_parsed_artifact_paths(parsed_dir)`

## `app/core/parser_routing.py` (265 行)

- **class `RouteType`** — 方法：—
- **class `FutureParserHint`** — 方法：—
- `def normalize_file_ext(ext)`
- `def ext_from_path(path)`
- `def match_route_type()`
- `def pdf_sniff_reason(sniff)`
- `def route_pdf_with_sniff(sniff)`

## `app/core/pdf_sniffing.py` (335 行)
> 014 PDF 内容嗅探（纯函数 + 文件缓存）。

- **class `SniffStatus`** — 方法：—
- **class `PdfSniffConfig`** — 方法：—
- **class `PdfSniffResult`** — 方法：evidence, cache_payload
- `def load_sniff_config(config_path)`
- `def sniff_pdf(original_bin)`
- `def cache_path_for(cache_root, sha256)`
- `def load_sniff_cache(cache_root, sha256, sniffer_version)`
- `def save_sniff_cache(cache_root, sha256, result)`
- `def resolve_pdf_sniff(original_bin, sha256)`

## `app/core/vault_paths.py` (41 行)

- **class `VaultArtifactPaths`** — 方法：—
- `def vault_uid_for(sha256)`
- `def build_vault_dir(raw_vault_root, sha256)`
- `def build_vault_artifact_paths(vault_dir)`

## `app/main.py` (11 行)

- `def health()`

## `app/models/document.py` (49 行)

- **class `KbDocument`** — 方法：—

## `app/models/duplicate.py` (30 行)

- **class `KbDuplicateGroup`** — 方法：—

## `app/models/evidence.py` (68 行)

- **class `KbDocumentChunk`** — 方法：—
- **class `KbEvidence`** — 方法：—

## `app/models/file.py` (78 行)

- **class `Base`** — 方法：—
- **class `KbFileInstance`** — 方法：—
- **class `KbFileContent`** — 方法：—

## `app/models/parse_registry.py` (140 行)

- **class `KbParseRun`** — 方法：—
- **class `KbParseResult`** — 方法：—
- **class `KbParsedArtifact`** — 方法：—

## `app/models/project.py` (94 行)

- **class `KbProject`** — 方法：—
- **class `KbProjectDocument`** — 方法：—
- **class `KbCuratedAsset`** — 方法：—

## `app/models/vault.py` (33 行)

- **class `KbRawVaultObject`** — 方法：—

## `app/schemas/search.py` (108 行)

- **class `SearchValidationError`** — 方法：—
- **class `SearchProjectNotFoundError`** — 方法：—
- **class `SearchQuery`** — 方法：validate_and_build
- **class `SearchHit`** — 方法：—
- **class `SearchResponse`** — 方法：—

## `app/services/curated_project_assets.py` (745 行)

- **class `DocumentMapping`** — 方法：—
- **class `PlannedAsset`** — 方法：—
- **class `CuratedProjectBuildResult`** — 方法：—
- **class `CuratedProjectAssetsService`** — 方法：build
- `def normalize_project_code(project_code)`
- `def project_uid_for(project_code)`
- `def curated_uid_for(project_code, asset_type, version_no)`

## `app/services/document_ir_builder.py` (500 行)
> 017 P2：把解析产物装配为 Document IR（`document_ir.json` 的纯数据层）。

- **class `_CellSeed`** — 方法：—
- **class `_PipeTableParser`** — 方法：handle_starttag, handle_data, handle_endtag
- `def build_from_mineru_content_list()`
- `def build_from_markitdown_text()`
- `def load_content_list(path)`

## `app/services/document_ir_schema.py` (1616 行)
> 017 Document IR（统一文档中间表示）schema 常量、纯数据结构与校验器。

- **class `DocumentIrValidationError`** — 方法：—
- **class `BBox`** — 方法：to_dict, from_dict
- **class `ContentRef`** — 方法：to_dict
- **class `Block`** — 方法：to_dict
- **class `TableCell`** — 方法：to_dict
- **class `IrTable`** — 方法：to_dict
- **class `IrFigure`** — 方法：to_dict
- **class `CoordinateSystem`** — 方法：to_dict
- **class `DocumentStats`** — 方法：to_dict, from_dict
- **class `DocumentIr`** — 方法：document_uid, reading_order_ids, to_dict, validate
- `def validate_bbox(value, path, code)`
- `def document_uid_for(sha256, parser_adapter_version)`
- `def validate_top_level(payload)`
- `def validate_block_required_keys(payload)`
- `def validate_block_ids(payload)`
- `def validate_block_types(payload)`
- `def validate_block_field_ranges(payload)`
- `def validate_reading_order(payload)`
- `def validate_coordinate_system(payload)`
- `def validate_content_refs(payload)`
- `def validate_tables(payload)`
- `def validate_figures(payload)`
- `def validate_caption_links(payload)`
- `def validate_figure_caption_targets(payload)`
- `def asset_path_from_relative_path(relative_path)`
- `def validate_asset_alignment(payload, manifest_assets)`
- `def derive_stats(blocks, tables, figures)`
- `def validate_stats(payload)`
- `def validate_document_ir(payload)`

## `app/services/duplicate_governance.py` (464 行)

- **class `GovernError`** — 方法：—
- **class `DuplicateGovernanceResult`** — 方法：to_dict
- **class `DuplicateGovernanceService`** — 方法：govern_duplicates
- `def is_copy_like_filename(file_name)`
- `def select_master_candidate(instances)`
- `def build_cleanup_suggestions(duplicate_group_uid, sha256, content_uid, master, instances, vault_path, content_master_file_instance_uid)`

## `app/services/evidence_chain.py` (744 行)

- **class `EvidenceCandidate`** — 方法：—
- **class `PlannedChunk`** — 方法：—
- **class `PlannedEvidence`** — 方法：—
- **class `EvidenceChainBuildResult`** — 方法：—
- **class `EvidenceChainService`** — 方法：build

## `app/services/file_content_vault.py` (407 行)

- **class `VaultCopyError`** — 方法：—
- **class `VaultCopyResult`** — 方法：to_dict
- **class `FileContentVaultService`** — 方法：copy_to_vault

## `app/services/image_parser.py` (1196 行)
> 015 图片解析流水线（image parse pipeline）服务。

- **class `ImageParseConfig`** — 方法：—
- **class `ImageParsedPaths`** — 方法：—
- **class `ParseCandidate`** — 方法：—
- **class `EngineRoute`** — 方法：—
- **class `ImageParsePlan`** — 方法：to_dict
- **class `ImageParseItem`** — 方法：to_dict
- **class `ImageParseError`** — 方法：to_dict
- **class `ImageParseManyResult`** — 方法：to_dict
- **class `_EngineTimeout`** — 方法：—
- **class `ImageParserService`** — 方法：parse_many
- `def load_image_parse_config(path)`
- `def build_image_parsed_paths(parsed_root, sha256)`
- `def resolve_engine_route()`

## `app/services/inventory_scanner.py` (334 行)

- **class `ScanError`** — 方法：—
- **class `ScanResult`** — 方法：to_dict
- **class `InventoryScanner`** — 方法：scan

## `app/services/markitdown_parser.py` (603 行)

- **class `ParseMarkitdownError`** — 方法：to_dict
- **class `ParseMarkitdownItem`** — 方法：to_dict
- **class `ParseMarkitdownResult`** — 方法：to_dict
- **class `MarkItDownParserService`** — 方法：parse_markitdown

## `app/services/mineru_pdf_parser.py` (1019 行)

- **class `SubprocessRunner`** — 方法：—
- **class `MagicPdfRunResult`** — 方法：—
- **class `MineruParseError`** — 方法：to_dict
- **class `MineruParsePlan`** — 方法：to_dict
- **class `MineruParseItem`** — 方法：to_dict
- **class `MineruParseManyResult`** — 方法：to_dict
- **class `MineruPdfParserService`** — 方法：plan_one, parse_one, parse_many
- `def check_magic_pdf_available()`

## `app/services/parse_quality_checker.py` (1733 行)

- **class `SemanticCheckConfig`** — 方法：—
- **class `ParseQualityCandidate`** — 方法：—
- **class `ParseQualityIssue`** — 方法：to_dict
- **class `ParseQualityReport`** — 方法：to_dict
- **class `ParseQualityCheckerService`** — 方法：check
- `def load_semantic_check_config(path)`
- `def count_garbled_chars(text)`
- `def split_table_cells(line)`
- `def analyze_markdown_tables(text)`
- `def extract_confidence_mean(metadata)`
- `def metadata_page_count(metadata)`

## `app/services/parse_quality_report_summarizer.py` (628 行)

- **class `ParseQualitySummaryResult`** — 方法：—
- **class `ParseQualityReportSummarizerService`** — 方法：summarize
- `def classify_noise_bucket(issue)`
- `def discover_latest_input_report(reports_root)`
- `def validate_input_report(data)`

## `app/services/parse_registry.py` (1180 行)

- **class `ParseRegistryError`** — 方法：—
- **class `RegistryErrorEntry`** — 方法：to_dict
- **class `RegisterReportResult`** — 方法：—
- **class `ReconcileResult`** — 方法：—
- **class `ParseRegistryService`** — 方法：register_parse_report, reconcile_parsed_artifacts, create_parse_run, finish_parse_run, fail_parse_run, record_parse_result, record_parsed_artifact, list_parse_runs, get_parse_run, list_parse_results, list_parsed_artifacts
- `def generate_run_uid(now)`
- `def generate_result_uid()`
- `def generate_artifact_uid()`

## `app/services/parser_router.py` (435 行)

- **class `RouteError`** — 方法：—
- **class `ParserRouteDecision`** — 方法：to_dict
- **class `ParserRouteResult`** — 方法：to_dict
- **class `ParserRouterService`** — 方法：route_parsers

## `app/services/search_service.py` (642 行)

- **class `SearchService`** — 方法：search
- `def hit_to_dict(hit)`
- `def build_success_payload(config, response, generated_at)`
- `def build_error_payload(config, error_code, message, query_text, scope, project_code, generated_at)`

