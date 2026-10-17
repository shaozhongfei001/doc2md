# 09 · 规格与验收索引（001~017）

> 规格是**验收依据**，不是文档装饰。每个 spec 目录结构：`spec.md`（需求）/ `plan.md`（设计+决策表）/ `tasks.md`（任务与勾选）/ `test_cases.md`（用例表）/ `acceptance.md`（**硬判据**）。
> 早期（001~013）验收条目标题形如 `## A001 范围符合`（三位编号）；014 起改为 `## A014-01 …`（带横线）。

## 1. 一页总表

| Spec | 状态 | 后端模块 | 测试文件 | 验收条目 |
|---|---|---|---|---|
| 001 file-inventory | DONE | `inventory_scanner.py` | `test_inventory_scanner.py` | `## A001~` 清单式 |
| 002 file-content-vault | DONE | `file_content_vault.py` | `test_file_content_vault.py` | 清单式 |
| 003 duplicate-governance | DONE | `duplicate_governance.py` | `test_duplicate_governance.py` | 清单式 |
| 004 parser-router | DONE | `parser_router.py` + `core/parser_routing.py` | `test_parser_router.py` | 清单式 |
| 005 markitdown-parser | DONE | `markitdown_parser.py` | `test_markitdown_parser.py` | 清单式 |
| 006 parse-job-registry | DONE | `parse_registry.py` | `test_parse_job_registry.py` | 清单式 |
| 007 mineru-pdf-parser | DONE | `mineru_pdf_parser.py` | `test_mineru_pdf_parser.py` | 清单式 |
| 008 parse-quality-checker | DONE | `parse_quality_checker.py` | `test_parse_quality_checker.py` | 清单式（18 个 issue code） |
| 009 quality-report-summary | DONE | `parse_quality_report_summarizer.py` | `test_parse_quality_report_summarizer.py` | 清单式 |
| 010 evidence-chain | DONE | `evidence_chain.py` | `test_evidence_chain.py` | 清单式（chunk + evidence） |
| 011 curated-project-assets | DONE | `curated_project_assets.py` | `test_curated_project_assets.py` | 清单式 |
| 012 search-service | DONE | `search_service.py` | `test_search_service.py` | 清单式 |
| 013 streamlit-admin | DONE | `frontend/streamlit_admin/` | （人工验证为主） | 清单式（含 P4/P5/P6/P7 报告） |
| **014 pdf-content-sniffing** | **QA_PASS** | `core/pdf_sniffing.py`、`core/parser_routing.py`、`parser_router.py`、`mineru_pdf_parser.py` | `test_pdf_sniffing.py`(21)、`test_parser_router_v2.py`(28) | **A014-01~12** / TC 13 |
| **015 image-parse-pipeline** | **QA_PASS**（5 项 NOT_COVERED） | `image_parser.py` + `adapters/{image_preprocess,image_quality,ocr_paddle,vlm_adapter}.py` | `test_image_parser.py`(55) | **A015-01~12** / TC 13 |
| **016 semantic-quality-metrics** | **QA_PASS** | `parse_quality_checker.py`（第五类）、`parse_quality_report_summarizer.py`（常量追加） | `test_parse_quality_checker_semantic.py`(60) | **A016-01~10** / TC 13 |
| **017 unified-document-ir** | 设计 REVIEWED；**P1/P2 已实现**，P3~P7 未做 | `document_ir_schema.py`(1,616)、`document_ir_builder.py`(500)、`core/parsed_paths.py`、`mineru_pdf_parser.py`(additive) | `test_document_ir.py`(115) | **A017-01~12** / TC 19 |
| 902 test-dataset | DONE | `scripts/create_sample_dataset.py` | `test_sample_dataset.py`(21) | 清单式 |

## 2. 014：PDF 内容嗅探（QA_PASS）

- **要解决的问题**：`PDF_SCANNED_OR_IMAGE` 曾是死枚举，扫描件被送进文本模式 → 产出空正文却不报错。
- **核心判据**：A014-01~12，含"仅 `TEXT_LAYER_PRESENT` 才可判 `PDF_DIGITAL`"的模块级 fail-closed 不变式。
- **独立 QA**：`acceptance.md` §3 变异（白名单调整 → 2 failed；路由规则改 DIGITAL → import 期 `AssertionError`）+ §7/§7.1 增量变更登记。
- **2026-10-17 增量**：`mineru_pdf_parser.py` 增失败可诊断性（`_process_output_tail`），受测 md5 由 `d8383fde…` 变为 `a82a3653…`，QA 复核 PASS WITH NOTES。

## 3. 015：图片解析管线（QA_PASS，5 项 NOT_COVERED）

- **核心判据**：A015-01~12。反假绿核心是 **A015-06「Mock 诚实性」**：`if ocr_result.mock:` 拒绝落盘，两组变异被捕获。
- **覆盖不到**（N-1~N-5）：真实图像质量 / 真实 PaddleOCR 推理 / 真实 VLM / CLI 级单条 FAILED / GIF 首帧。
- **已知遗留**：
  - D-2：无 cv2 时 Pillow 阈值（20.0）区分度不足（清晰图实测 9.91 与 20.3 恰夹在阈值两侧）→ 需真实语料重校准。
  - A015-12 修复：缺 cv2 环境的用例改为可用性分支断言（不再硬套 Laplacian 量纲）。

## 4. 016：语义质检（QA_PASS）

- **核心判据**：A016-01~10；新增 5 个 issue code（乱码率/低置信度/置信度缺失/表格残缺/空正文非零页）。
- **反假绿**：12 次变异 12/12 捕获（含"阈值改硬编码 → 用例红"、"缺 confidence 时编造 LOW_CONFIDENCE → 红"）。
- **兼容红线**：`--no-semantic` 逐字段复现 008（schema 1.0 / 18 键 / `scope` 无 `semantic` 键 / 不读 `parsed_text.md`）。
- **规格冲突已裁定**：`:516` 单处等价归一化（白名单例外）、009 输入侧双接受 1.0/1.1、`scope.semantic` 条件输出。

## 5. 017：统一文档中间表示（进行中）

| 阶段 | 状态 | 产物 |
|---|---|---|
| P1 基座 | **PASS** | `document_ir_schema.py`（契约 + 13 组纯函数校验器）、`test_document_ir.py`、`parsed_paths` 三键 |
| P1 裁定 | 完成 | `evidence/TL-RULINGS-P1.md`（R-01~R-18）、`AUDIT-P1.md`（13 项 SPEC_GAP） |
| P2 构建 | **已实现**（待独立 QA） | `document_ir_builder.py`、parser `ir_source` 保留、`document_ir.json` 真实产出 |
| P3 表图深度校验 | 未做 | `html↔cells` 双向、asset 实际存在性 |
| P4 渲染与服务 | 未做 | `ir_renderer.py`（3 条降级）、`document_ir_service.py`（幂等/dry-run）、CLI `build-document-ir` |
| P5 接入 | 未做 | `ir_vlm_fallback.py`、`evidence_chain._chunk_from_ir()` |
| P6/P7 | 未做 | 独立 QA、收口 |

**A017-01~12 的硬判据要点**：schema 契约（封闭枚举/必填键/坐标声明）、`block_id` 跨重跑稳定、span 回引、cells 矩阵、asset fail-closed、三条渲染降级、VLM 整页降级（两变异）、`NO_IR_SOURCE` 语义、旧三件套零改动、证据链扩展（`field_value` 零数据）、幂等/dry-run/零迁移、五变异（3/3 必须捕获）+ 中文路径。

## 6. 去哪里看细节（省 Token 的查法）

| 你想知道 | 看这个 |
|---|---|
| 某条验收到底要求什么 | `specs/<id>/acceptance.md`，只读对应 `A0xx-yy` 段 |
| 设计决策与理由 | `specs/<id>/plan.md` §5（关键决策）/ §8（决策表） |
| 谁改了什么文件（白名单） | `specs/<id>/plan.md` §6.1 / `tasks.md` 顶部 |
| 跨规格文件冲突 | `specs/S1_WHITELIST_CONFLICT_MATRIX.md`（34 行矩阵 + §6/§7 变更留痕） |
| 017 的争议裁定 | `specs/017-unified-document-ir/evidence/TL-RULINGS-P1.md` |
| 017 的契约审计 | 同上目录 `AUDIT-P1.md`（76 条契约、13 项 GAP、3 项实测基线） |
