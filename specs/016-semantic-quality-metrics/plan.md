# Plan: 语义质检指标（016 实现计划）

> **项目**：`doc2md`（个人历史项目文档知识库）
> **项目路径**：`/home/szf/dev/doc2md`
> **版本基线**：V1.1-SDD / Loop S1
> **编写日期**：2026-10-16
> **前置条件**：001–013、902 已完成；Owner 已批准 spec.md §11 O-1 ~ O-4
> **编写角色**：Tech Lead（spec-lead）— Loop S1 G0
> **建议分支**：`feature/016-semantic-quality-metrics`

---

## 1. 背景

见 `spec.md` §1。008 只查产物存在性（`parse_quality_checker.py:576-657` 对 `parsed_text.md` 仅 `is_file()`），语义层缺陷全绿漏过。016 在四类既有检查之后新增第五类语义检查。

---

## 2. 输入数据

### 2.1 MySQL（只读，与 008 相同）

候选加载 `_load_candidates`（`parse_quality_checker.py:415-453`）**零改动**。语义检查只对 `result_status == "SUCCESS"` 的候选执行。

### 2.2 文件系统（只读新增开口）

| 文件 | 016 操作 | 用途 |
|------|----------|------|
| `parsed_text.md` | **只读 open 读内容**（新增） | garbled / 表格 / 空正文检测 |
| `parsed_metadata.json` | **只读 open 读内容**（新增） | `ocr_confidence`、页数信息 |

路径解析复用既有 `_resolve_parsed_dir` / `build_parsed_artifact_paths`。读文件 OSError / JSON 解析失败 → 该候选记 `errors` 语义化 issue 或复用既有 code，不中断。

### 2.3 配置

`config/quality_semantics.yaml`（新文件，缺失用默认值）：

```yaml
semantic_checks:
  enabled: true
  garbled:
    min_sample_chars: 200      # 正文短于此不评估乱码率
    warn_ratio: 0.02
    error_ratio: 0.10
  confidence:
    min_mean: 0.70             # ocr_confidence.mean 低于此 → OCR_LOW_CONFIDENCE
    ocr_parser_names: ["image_ocr", "mineru"]
  tables:
    enabled: true              # pipe 表格结构检查
  empty_text:
    enabled: true
```

`SemanticCheckConfig` dataclass 承载；`ParseQualityCheckerService.__init__` 增加可选注入（测试可传内存配置）。

---

## 3. 输出数据

### 3.1 报告演进（schema_version 1.1）

```json
{
  "report_type": "parse_quality_report",
  "schema_version": "1.1",
  "semantic_checks_enabled": true,
  "summary": { "...": "既有键不变", "checked_semantic_count": 0 },
  "issue_counts": { "既有18项": 0, "OCR_GARBLED_RATIO": 0, "OCR_LOW_CONFIDENCE": 0,
                    "OCR_CONFIDENCE_UNAVAILABLE": 0, "TABLE_STRUCTURE_INCOMPLETE": 0,
                    "EMPTY_BUT_NONZERO_PAGES": 0 }
}
```

- 兼容性：`--no-semantic` 或 yaml `enabled=false` 时 `schema_version` 仍输出 `1.0` 且 `issue_counts` 只含既有 18 键、`semantic_checks_enabled` 键不出现——**与 008 现状逐字段一致**。
- 每条语义 issue 的 `evidence` 必须含实测值与阈值，例如：`{"garbled_ratio": 0.13, "threshold_warn": 0.02, "threshold_error": 0.10, "total_chars": 1240, "sampled": true}`。

### 3.2 009 summarizer 联动

`parse_quality_report_summarizer.py:23` 的 `ISSUE_CODES` 追加 5 项；`:69` 的 `ALLOWED_PARSER_NAMES` 追加 `image_ocr`。分桶逻辑（`_classify_noise`, `:120-121` 按 code 硬编码 `STALE_RAW_VAULT_PATH`）不动：5 个新 code 默认落入「需人工审阅」的非噪声路径（009 既有默认行为），不在 016 中新增分桶规则（O-4 / Q4）。

---

## 4. 模块设计

### 4.1 `parse_quality_checker.py` 内部新增（不新增文件）

```text
SEMANTIC_ISSUE_CODES = (
    "OCR_GARBLED_RATIO", "OCR_LOW_CONFIDENCE", "OCR_CONFIDENCE_UNAVAILABLE",
    "TABLE_STRUCTURE_INCOMPLETE", "EMPTY_BUT_NONZERO_PAGES",
)
ISSUE_CODES = 既有18项 + SEMANTIC_ISSUE_CODES   # 顺序：既有在前，追加在后
ALLOWED_PARSER_NAMES = frozenset({"markitdown", "mineru", "image_ocr"})
RECOMMENDATION_BY_CODE 增补 5 条人工建议文案
```

### 4.2 `_check_semantic_quality(candidate, manifest, artifact_paths) -> list[ParseQualityIssue]`

在 `check()` 主循环中、既有四类检查之后调用（仅当 `semantic_enabled and candidate.result_status == "SUCCESS"`）：

```text
1. EMPTY_BUT_NONZERO_PAGES:
   text = read(parsed_text)  # 失败 → 既有 MISSING_PARSED_TEXT 已覆盖，此处跳过
   text.strip() == "" and (manifest.content_size_bytes or 0) > 0 → ERROR
   （page_count>0 判定：parsed_metadata.json 若含 pages/page_count 字段则并集判断）

2. OCR_GARBLED_RATIO:
   total_chars = len(text)；< min_sample_chars → 跳过（无 issue）
   garbled_chars = count(ch ∈ {U+FFFD} ∪ C0/C1控制符(除\n\t\r) ∪ 私有区 ∪ 异常符号run)
   ratio 分档 → WARNING / ERROR

3. TABLE_STRUCTURE_INCOMPLETE（tables.enabled）:
   逐行扫描 pipe 表格块；表头列数 ≠ 分隔行列数 或 任一体行 ≠ 表头列数 → 残缺
   incomplete>0 → WARNING，evidence 含行号列表（截断至前 20 个）

4. OCR_LOW_CONFIDENCE / OCR_CONFIDENCE_UNAVAILABLE（parser ∈ ocr_parser_names）:
   metadata = read(parsed_metadata.json)  # 失败 → 跳过
   conf = metadata.get("ocr_confidence")
   conf is None 或 conf.mean is None → OCR_CONFIDENCE_UNAVAILABLE (INFO)
   conf.mean < min_mean → OCR_LOW_CONFIDENCE (WARNING)
```

乱码字符判定函数 `_is_garbled_char(ch)` 为**纯函数**，单测直接覆盖；判定表冻结在代码中（不随 yaml 变），yaml 只调阈值。

### 4.3 CLI

`check-parse-quality` 新增 `--no-semantic`（默认 False）。`scope` 块增加 `"semantic": true|false`。

---

## 5. 关键设计决策

### 5.1 D-1 启发式而非 LLM

008 NG016/NG017 红线延续：全部确定性规则。乱码检测用字符类别比率而非语言模型——可复现、可单测、可变异复验。

### 5.2 D-2 缺数据不编造（反假绿核心）

三条硬规则进代码评审与验收：

1. 无 `ocr_confidence` → 只产 INFO `OCR_CONFIDENCE_UNAVAILABLE`，**绝不**产 `OCR_LOW_CONFIDENCE`；
2. 正文 < `min_sample_chars` → 乱码检查整体跳过（不是「ratio=0 通过」）；
3. 无 pipe 表格 → 表格检查跳过。

### 5.3 D-3 阈值必须真实接线

所有阈值从 `SemanticCheckConfig` 读取，检测函数签名显式接收阈值参数。验收 A016-06 用「阈值调高 → issue 消失」证明接线真实；若 Dev 硬编码，该变异测试必然失败。

### 5.4 D-4 `--no-semantic` 冻结 008 行为

第五类是可关断增量：关闭后报告与 008 现状逐字段一致（含 schema_version 回落 1.0），为 008 既有消费者提供零风险升级路径。

### 5.5 D-5 语义检查不新增错误码类型

读文件失败等异常复用 008 既有 `MISSING_PARSED_TEXT` / `INVALID_PARSE_MANIFEST_JSON` 体系；检测函数内部未预见异常 → 候选级 `errors[]`（沿用 008 check 主循环的容错模式），不中断批处理。

---

## 6. 与 015 的字段契约对接

015 spec §6 定义 `parsed_metadata.json.ocr_confidence = {"mean","min","sample_count"}`。016 只读 `mean`；字段缺失或 `mean=null` → INFO 路径（D-2）。015 产物上线前，mineru 产物无 `ocr_confidence` → 同样只产 INFO，不误报。

---

## 7. Dev 实现边界

### 7.1 白名单

| 操作 | 文件 |
|------|------|
| 修改 | `backend/app/services/parse_quality_checker.py`（§4 范围） |
| 修改 | `backend/app/services/parse_quality_report_summarizer.py`（**仅** `ISSUE_CODES` / `ALLOWED_PARSER_NAMES` 两个常量追加） |
| 修改 | `backend/app/cli/main.py`（`check-parse-quality` 增 `--no-semantic`） |
| 新增 | `config/quality_semantics.yaml` |
| 新增 | `backend/tests/test_parse_quality_checker_semantic.py` |
| 修改 | `specs/016-semantic-quality-metrics/tasks.md` |

### 7.2 禁止修改

```text
backend/app/services/mineru_pdf_parser.py / markitdown_parser.py / image_parser.py
backend/app/core/parser_routing.py
backend/app/models/**  sql/**  generated/**
raw_vault/**  parsed/ 下的真实产物  data/**
specs/001..013/**（封版）  specs/902/**
config/parser_rules.yaml  config/quality_actions.yaml
```

### 7.3 测试先行与 fixture 策略

先写 `test_parse_quality_checker_semantic.py`（红）→ 实现 → 绿。fixture 全部现场构造：tmp_path 下手工写 `parsed_text.md` / `parsed_metadata.json` / `parse_manifest.json` + 直接向测试 DB 插入 `kb_parse_result` 行（对齐 `test_parse_quality_checker.py` 既有模式）。乱码 fixture = 正常中文段落混入 3% U+FFFD 与控制符；表格 fixture = 手工 ragged pipe table。

---

## 8. 异常处理设计

| 场景 | 处理 |
|------|------|
| `parsed_text.md` 读取 OSError | 语义检查跳过该候选（既有 MISSING_* 已覆盖存在性）；continue |
| `parsed_metadata.json` JSON 非法 | confidence 检查跳过；**不**新增 code（008 对 metadata 无 JSON 校验 code，016 不扩张） |
| 正文极短（< min_sample） | 乱码检查跳过，零 issue（D-2） |
| 阈值 yaml 缺失/字段缺失 | 代码默认值；WARNING 日志一次 |
| 阈值非法（负数/>1） | `ValueError` 启动即失败（fail-fast，配置错误不应静默） |
| 单候选检测异常 | 候选级容错，continue；批处理不中断 |

---

## 9. TL 决策表（Dev 必遵）

| # | 问题 | 决策 |
|---|------|------|
| Q1 | 检查执行序 | 第五类在既有四类之后；仅 `status=SUCCESS` 候选 |
| Q2 | 乱码判定 | 字符类别比率，冻结于代码；阈值走 yaml |
| Q3 | 读文件失败 | 复用既有 code 体系，不新增错误类 code；检测异常 → 候选级容错 |
| Q4 | 009 分桶 | 新 code 走 009 既有人工审阅默认路径，016 不加分桶规则 |
| Q5 | schema_version | 语义开启→`1.1`；`--no-semantic`→`1.0` 逐字段复现 008 |
| Q6 | confidence 缺失 | 仅 INFO，绝不产 LOW_CONFIDENCE |
| Q7 | `image_ocr` 白名单扩展归属 | 016（008 checker + 009 summarizer 同步） |
| Q8 | 阈值默认值 | garbled 0.02/0.10；confidence 0.70；min_sample 200 |

---

## 10. Acceptance Criteria 摘要

见 `acceptance.md`（A016-01 ~ A016-10，否定式 + 阈值变异复验）。

---

## 11. 流水线与 STOP 点

```text
G0 规格 → Owner 裁定 O-1~O-4 → P2 DB Review（确认 SELECT-only）
→ P4 Dev（测试先行）→ STOP → P5 QA → P6 E2E → P7 TL Final Review
```

---

**Plan 结束** — Dev 先读 `acceptance.md` 与 §7 白名单再写代码。
