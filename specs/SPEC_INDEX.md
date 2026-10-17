# Spec Index

> This file is the authoritative index for active, completed, deprecated, and future specs.
>
> When Cursor / ChatGPT / Agent workflows choose a spec, they must read this file first.
> Do not infer the active spec from directory numbering alone.

---

## 1. Active Completed Chain

| Phase | Active Spec Directory | Implementation Status | Notes |
|---|---|---:|---|
| 001 | `specs/001-file-inventory/` | DONE | File inventory / scan |
| 002 | `specs/002-file-content-vault/` | DONE | Raw file content vault |
| 003 | `specs/003-duplicate-governance/` | DONE | Exact duplicate governance |
| 004 | `specs/004-parser-router/` | DONE | Parser route planning only |
| 005 | `specs/005-markitdown-parser/` | DONE | MarkItDown parser adapter |
| 006 | `specs/006-parse-job-registry/` | DONE | Parse job/result/artifact registry |
| 007 | `specs/007-mineru-pdf-parser-adapter/` | DONE | MinerU PDF parser adapter |
| 008 | `specs/008-parse-quality-checker/` | DONE | Parse quality checker |
| 009 | `specs/009-quality-report-summary/` | DONE | Parse quality report summary |
| 010 | `specs/010-evidence-chain/` | DONE | Evidence chain (chunk + evidence) |
| 011 | `specs/011-curated-project-assets/` | DONE | Curated project assets (rule/template MVP) |
| 012 | `specs/012-search-service/` | DONE | Read-only MySQL FULLTEXT search service |
| 013 | `specs/013-streamlit-admin/` | DONE | Read-only Streamlit admin UI |
| 014 | `specs/014-pdf-content-sniffing/` | QA_PASS (Loop S1, 2026-10-16) | PDF 内容嗅探路由修复（PDF_SCANNED_OR_IMAGE 死枚举激活）；独立 QA verdict=PASS（ir-architect，A014-01~12 全 PASS，M1/M2 变异已恢复原 md5）。**2026-10-17 增量**：`mineru_pdf_parser.py` 加失败可诊断性（`d8383fde…` → `a82a3653…`），已登记 acceptance §7 并经独立 QA 复核 **PASS WITH NOTES**（§7.1） |
| 015 | `specs/015-image-parse-pipeline/` | **QA_PASS** (Loop S1, 2026-10-17) | 独立图片解析管线（预处理→质量评分→PaddleOCR/VLM 路由）；独立验收 qa-015：**A015-01~11 PASS、A015-12 修复后 PASS**，verdict = **PASS WITH NOT_COVERED**（5 项 NOT_COVERED：真实图像语料 0 张 / 真实 PaddleOCR / 真实 VLM / CLI 级单条 FAILED / GIF 首帧）；A015-06 两组变异均被捕获；收尾指纹 `test_image_parser.py` `9018b0f8…`/1145 行。遗留 D-2（Pillow 阈值区分度，清晰图 9.91 与 20.3 分居阈值 20.0 两侧）**留作 015 后续修订**（不并入 016；校准需真实图像语料，待 Owner 提供后单开工单） |
| 016 | `specs/016-semantic-quality-metrics/` | ACTIVE (Loop S1, 2026-10-17 开工) | 008 质检第五类语义检查（乱码率/置信度/表格/空正文）。**边界（TL 2026-10-17 裁定）**：D-2（015 Pillow 回退阈值校准）**不并入 016** —— 016 白名单不含 `image_quality.py`/`config/image_parse.yaml`，且校准需真实图像语料（仓内 0 张），混入会让 016 验收被外部依赖拖住；D-2 留作 015 后续修订，待 Owner 供真实图片 |
| 017 | `specs/017-unified-document-ir/` | DESIGN REVIEWED (SPECS READY) | 统一文档中间表示（document_ir.json）；设计稿已评审，规格五件套齐备（spec/plan/tasks/test_cases/acceptance，OQ-1~7 全 DECIDED 2026-10-16），**实现排期在 S1 之后** |
| 902 | `specs/902-test-dataset/` | DONE | Non-sensitive sample dataset + idempotent generator |

---

## 2. Deprecated / Superseded Stub Specs

These directories are early roadmap or stub specs. They are preserved for historical context, but must not be used as the active implementation contract.

| Directory | Status | Replacement / Current Authority |
|---|---|---|
| `specs/006-mineru-parser/` | DEPRECATED | Use `specs/007-mineru-pdf-parser-adapter/` |
| `specs/007-quality-checker/` | DEPRECATED | Use `specs/008-parse-quality-checker/` |

---

## 3. Future / Not Current Specs

| Directory | Status | Notes |
|---|---|---|
| `specs/008-review-workflow/` | FUTURE STUB / NOT CURRENT | Human review workflow. This is **not** the completed 008 parse quality checker. Do not start unless this index explicitly sets it ACTIVE. |
| `specs/901-docker-compose-deployment/` | SUPPORT / FUTURE | Deployment support. Do not start while host MySQL already listens on 3306 unless this index sets it ACTIVE. |

### 3.1 Future Spec Renumber Note (2026-06)

To avoid two `009` semantics coexisting, former future stubs were renumbered:

```text
009-evidence-chain        -> 010-evidence-chain
010-curated-project-assets -> 011-curated-project-assets
011-search-service        -> 012-search-service
012-streamlit-admin       -> 013-streamlit-admin
```

`009` is the completed `009-quality-report-summary` spec.

---

## 4. Contract Rules for Agents

### 4.1 Spec Selection

Agents must follow this order when selecting a spec:

1. Read `specs/SPEC_INDEX.md`.
2. Use the `Active Spec Directory` listed in this file.
3. Do not select a spec by numeric prefix alone.
4. Do not use deprecated stub directories as implementation contracts.
5. If a directory number conflicts with this index, this index wins.

### 4.2 Current Active Phase

**Active spec:** `902-test-dataset` — `specs/902-test-dataset/` — **DONE / IMPLEMENTED**

Selection record: `docs/tl-review-2026-09-05-env-and-active-spec.md` (2026-09-05).

The most recently **completed** product phase is:

`013 Streamlit Admin` — `specs/013-streamlit-admin/` — **DONE**

Specs **001–013** remain **DONE**. Do not re-open them unless a defect spec is explicitly approved.

`902-test-dataset` is now **DONE** (2026-09-05). Gate chain: P2 PASS WITH CONSTRAINTS →
P3 GATE PASS → P4 DEV DONE → P5 QA PASS → P6 E2E PASS → P7 TL Final Review PASS.
Reports: `specs/902-test-dataset/p2_db_review.md`, `p3_implementation_gate.md`,
`p4_kickoff.md`, `p5_qa_report.md`, `p6_e2e_report.md`, `p7_final_review.md`.
Handoff: `docs/handoff-902-test-dataset.md`.

**Active specs (Loop S1, Owner-approved 2026-10-16):** `014-pdf-content-sniffing` →
`015-image-parse-pipeline` → `016-semantic-quality-metrics`，按此顺序推进。

Owner 裁定要点：

1. 014 配套全部批准：`pypdf` 进 `backend/requirements.txt`；嗅探只读打开 `original.bin` 的红线修订（仅限 `.pdf`、只读、文本层探测）；新增 `sniff_cache/` 派生缓存目录。
2. 015 按 TL 建议执行：可选依赖不进默认 requirements（`DEPENDENCY_MISSING` 优雅降级）；VLM 仅 Protocol + Mock（Mock 结果拒绝落盘，真实 VLM 接入另开 Spec）。
3. 016 阈值冻结进 `config/quality_semantics.yaml`；报告 `schema_version` 升 `1.1`。
4. `017-unified-document-ir` 设计稿已评审（`docs/architecture/DOCUMENT_IR_DESIGN_V1.md`），规格五件套已齐备（`specs/017-unified-document-ir/` 下 spec/plan/tasks/test_cases/acceptance，OQ-1~7 全 DECIDED 2026-10-16），**实现排期在 S1 之后**；S1 期间不得开工 017 实现。

Do **not** start:

1. `008-review-workflow` — still FUTURE STUB
2. `901-docker-compose-deployment` — still SUPPORT / FUTURE
3. `017` 实现（设计稿已评审，规格已齐备，待 S1 合并后开工，排期在 S1 之后）
4. Any unlisted spec

### 4.2.1 Active 902 Boundary

`902-test-dataset` provides a non-sensitive sample tree and an idempotent generator script.

It may:

- add files under `backend/tests/fixtures/sample_dataset/`
- sync a runtime copy to `data/sample_dataset/`
- extend `scripts/create_sample_dataset.sh` (optional helper `.py` next to it)
- add `backend/tests/test_sample_dataset.py` and `docs/sample_dataset.md`
- append `data/sample_dataset/` to `.gitignore`

It must not:

- connect to MySQL or change schema / ORM
- call MarkItDown, MinerU, `magic-pdf`, or any 001–013 CLI
- modify `backend/app/**`
- modify existing `backend/tests/fixtures/中文路径/**`
- write `raw_vault` / `parsed` / `curated` / `reports` / `quarantine`
- ingest real private documents or source-code repositories
- implement review workflow, Docker Compose, search, or Streamlit changes

Script:

```bash
bash scripts/create_sample_dataset.sh
```

### 4.3 Completed 012 Boundary (Reference)

`012-search-service` provides read-only MySQL FULLTEXT keyword search over 010/011-populated tables.

It may:

- read `config/app.yaml` (mysql for sessions, `pipeline_version` for logging)
- SELECT from `kb_document`, `kb_document_chunk`, `kb_evidence`, `kb_project`, `kb_project_document`, `kb_curated_asset` using existing FULLTEXT indexes (ngram)
- expose CLI `search-kb`
- write optional JSON results to operator `--output` path (not DB)

It must not:

- read `raw_vault` binary objects (`original.bin`) or `raw_vault/**` for search text
- read `parsed_text.md`, `parsed_metadata.json`, or `parse_manifest.json`
- modify parsed artifacts, curated files, or original user files
- call MarkItDown, MinerU, or `magic-pdf` at runtime
- reparse, repair, or auto-fix 008/009 quality findings
- INSERT/UPDATE/DELETE any MySQL table in MVP (SELECT-only)
- write `kb_document_chunk`, `kb_evidence`, `kb_project`, `kb_curated_asset`, parse registry, `kb_review_item`, or `kb_embedding_ref`
- use LLM query expansion, semantic similarity, embedding generation, or vector stores
- implement Streamlit admin UI (013 scope)
- introduce schema migration without P2 DB Review and migration script

`--project-code` filter must use `kb_project_document` mapping (011 does not backfill `kb_evidence.project_uid` in MVP).

CLI:

```bash
PYTHONPATH=backend python -m app.cli.main search-kb \
  --config config/app.yaml \
  --query "<keywords>" \
  --scope all|document|chunk|evidence|project|curated \
  --project-code <code> \
  --limit 20 \
  --offset 0 \
  --format json|table \
  --output /path/to/search_results.json
```

012 must not be re-opened for implementation unless a new defect spec is explicitly approved.

### 4.4 Completed 013 Boundary (Reference)

`013-streamlit-admin` provides a **read-only** Streamlit admin UI over 001–012 contracts.

It may:

- read `config/app.yaml` (`mysql`, `reports_root`, `curated_root`, `pipeline_version`)
- run `streamlit run frontend/streamlit_admin/app.py` with `PYTHONPATH=backend`
- invoke `SearchService` in-process for KB Search (same contract as CLI `search-kb`)
- SELECT from `kb_document`, `kb_document_chunk`, `kb_evidence`, `kb_project`, `kb_project_document`, `kb_curated_asset`, `kb_parse_run`, `kb_parse_result`, `kb_parsed_artifact`
- optionally SELECT from `kb_file_instance`, `kb_file_content` for Inventory Snapshot page (P3 lock)
- read `reports_root/**` for 008/009 quality report display (read-only)
- read `curated_root/projects/**` Markdown for curated asset display (read-only)

It must not:

- trigger pipeline CLIs (inventory, vault, parse, evidence build, curated build, quality checker/summary)
- read `raw_vault` binary objects (`original.bin`) or `raw_vault/**` for content display
- read `parsed_text.md`, `parsed_metadata.json`, or `parse_manifest.json` for MVP primary views
- modify parsed artifacts, curated files, or original user files
- call MarkItDown, MinerU, or `magic-pdf` at runtime
- reparse, repair, or auto-fix 008/009 quality findings from UI
- INSERT/UPDATE/DELETE any MySQL table in MVP (SELECT-only)
- write `kb_document_chunk`, `kb_evidence`, `kb_project`, `kb_curated_asset`, parse registry, inventory tables, `kb_review_item`, or `kb_embedding_ref`
- hand-write FULLTEXT `MATCH ... AGAINST` SQL in UI layer (must use `SearchService`)
- use LLM UI features, semantic similarity, embedding generation, or vector stores
- implement review workflow (008-review-workflow scope)
- introduce schema migration without P2 DB Review and migration script

Launch:

```bash
PYTHONPATH=backend streamlit run frontend/streamlit_admin/app.py
```

013 must not be re-opened for implementation unless a new defect spec is explicitly approved.

### 4.5 Completed 011 Boundary (Reference)

`011-curated-project-assets` builds rule/template curated project files from 010 evidence and registry metadata.

It may (P4+, after P2 DB Review PASS):

- read `config/app.yaml` (`curated_root`, `pipeline_version`, mysql for sessions)
- read optional project manifest YAML (`--manifest` / `config/projects/*.yaml`)
- SELECT from `kb_document`, `kb_document_chunk`, `kb_evidence`, `kb_file_content`, `kb_parse_result`, and related registry tables
- INSERT/UPSERT `kb_project`, `kb_project_document`, and `kb_curated_asset` with idempotent keys (P2 must confirm)
- write Markdown files under `{curated_root}/projects/{project_code}/` (MVP: `00_project_card.md`, `10_evidence_index.md`, `source_documents.md`)
- write optional JSON build report (`--output`)

It must not:

- read `raw_vault` binary objects (`original.bin`) for text extraction
- modify parsed artifacts or original user files
- call MarkItDown, MinerU, or `magic-pdf` at runtime
- reparse, repair, or auto-fix 008/009 quality findings
- write `kb_document_chunk`, `kb_evidence`, parse registry, `kb_review_item`, or `kb_embedding_ref`
- use LLM distillation, semantic summarization, or embedding generation
- implement search service (012) or Streamlit / admin UI (013)
- introduce schema migration without P2 DB Review and migration script

CLI:

```bash
PYTHONPATH=backend python -m app.cli.main build-curated-project \
  --config config/app.yaml \
  --project-code <code> \
  --project-name "<name>" \
  --manifest config/projects/<code>.yaml \
  --content-uid <uid> \
  --limit <n> \
  --dry-run \
  --force \
  --output /path/to/curated_build_report.json
```

011 must not be re-opened for implementation unless a new defect spec is explicitly approved.

### 4.6 Completed 010 Boundary (Reference)

`010-evidence-chain` builds chunk and evidence metadata from parsed artifacts.

It may:

- read `config/app.yaml` (`parsed_root`, `pipeline_version`, mysql for sessions)
- read `parsed_text.md`, `parsed_metadata.json`, `parse_manifest.json` (**read-only**)
- SELECT from `kb_document`, `kb_parse_result`, `kb_file_content`, and related registry tables
- INSERT/UPSERT `kb_document_chunk` and `kb_evidence` with deterministic idempotent UIDs
- write optional JSON build report (`--output`)

It must not:

- read `raw_vault` binary objects (`original.bin`) for text extraction
- modify parsed artifacts or original user files
- call MarkItDown, MinerU, or `magic-pdf` at runtime
- reparse, repair, or auto-fix 008/009 quality findings
- write parse registry during `build-evidence-chain`
- write `curated/`, project cards, vectors, embeddings, or `kb_review_item`
- use LLM chunking or semantic splitting
- introduce schema migration without a separate migration spec

CLI:

```bash
PYTHONPATH=backend python -m app.cli.main build-evidence-chain \
  --config config/app.yaml \
  --content-uid <uid> \
  --sha256 <hash> \
  --limit <n> \
  --dry-run \
  --force \
  --output /path/to/evidence_build_report.json
```

010 must not be re-opened for implementation unless a new defect spec is explicitly approved.

### 4.7 Completed 009 Boundary (Reference)

`009-quality-report-summary` is a completed read-only report consumption phase.

It may:

- read `config/app.yaml` for `reports_root` and default input discovery
- read an existing 008 `parse_quality_report.json` file (`--input` or latest under `reports_root`)
- write a Markdown or JSON summary file under `reports_root` or `--output`
- classify issues in the input report into noise buckets (`TEST_STALE_PATH`, `STALE_VAULT_PATH`, `REAL_DEFECT`)
- filter and aggregate issue data already present in the 008 report

It must not:

- read `raw_vault`
- read `parsed`
- connect to MySQL
- write DB records
- call MarkItDown, MinerU, or `magic-pdf`
- invoke `check-parse-quality` to rescan the project
- repair, reparse, delete, move, or rename files
- clean pytest dirty DB records
- write curated assets, vectors, embeddings, or project cards
- use LLM semantic judgment

Default output should be a summary under `reports_root`, for example:

`parse_quality_summary_{UTC}.md`

If a future design proposes DB writes or filesystem reads beyond the 008 JSON report, the workflow must STOP and enter TL + DB Review first.

009 must not be re-opened for implementation unless a new defect spec is explicitly approved.

### 4.8 Completed 008 Boundary (Reference)

`008-parse-quality-checker` remains a completed read-only checker. It must not be re-opened for implementation unless a new defect spec is explicitly approved.

---

## 5. Parser Output Contract

The active parsed artifact contract is:

`parsed/by_hash/{sha256[:2]}/{sha256[2:4]}/{sha256}/`

Standard files:

- `parsed_text.md`
- `parsed_metadata.json`
- `parse_manifest.json`

`parser_profile` and `parser_adapter_version` are metadata fields in `parse_manifest.json`.

They are not path segments.

---

## 6. Known Caveat from 007

007 MinerU PDF Parser Adapter has completed:

- mock subprocess validation
- real `ParseRegistryService.register_parse_report()` ingest validation
- CLI dry-run validation
- dependency-missing guard validation
- full pytest regression

But real `magic-pdf` / MinerU E2E was not completed because:

1. `magic-pdf` was not installed on PATH.
2. The available COPIED PDF database sample pointed to a stale `/tmp/p5_reqa_*` vault path.
3. There was no COPIED non-PDF live sample for CLI ROUTE_MISMATCH validation.

This caveat does not change the 007 implementation contract. 008 detects stale vault path and parsed/registry consistency issues in its JSON report; 009 may summarize those findings but must not fix them.
