# Spec: 语义质检指标（016 Semantic Quality Metrics）

> **项目**：个人历史项目文档知识库 `doc2md`（原 `pkb_sdd` 演进）
> **版本基线**：V1.1-SDD / Loop S1 规格门禁 G0
> **前置条件**：001–013、902 已完成；008-parse-quality-checker 已交付
> **详细实现计划**：见同目录 `plan.md`
> **定位**：008 质检的**第五类检查（语义检查）**演进——现状 008 只做「产物存在性 / 字段一致性 / 跨层一致性 / 状态聚合」四类结构检查，无法发现「文件都在但内容是乱码」「SUCCESS 但正文为空」「表格残缺」「OCR 置信度低」等语义层缺陷

---

## 1. 背景

### 1.1 缺陷现状（代码级证据）

`backend/app/services/parse_quality_checker.py`：

- `ISSUE_CODES`（`:31-50`）18 个 code 全部是**结构性**检查（缺失/不匹配/状态聚合）。
- 对 `parsed_text.md` 仅做 `is_file()` 存在性检查（`_check_parsed_files`, `:576-657`），**从不读其内容**。
- 后果：MinerU/OCR 产出全乱码、空文本被标 SUCCESS、Markdown 表格行列错位，008 报告全绿——典型的「只测存在性假绿」。

### 1.2 边界修订声明

008 既有边界不禁止读 `parsed_text.md`（禁读它的是 012/013），但 008 实现上只读 manifest。016 **经授权**新增「只读打开 `parsed_text.md` 与 `parsed_metadata.json` 内容做启发式语义检查」。只读红线不变；不触发重解析、不修复（008 NG001/NG011 延续）。

### 1.3 与 014/015 的联动

- 015 新增 `parser_name="image_ocr"` 与 `parsed_metadata.json.ocr_confidence` 字段 → 016 负责扩展 008/009 的 `ALLOWED_PARSER_NAMES` 并消费 confidence。
- 014 新增 `PDF_SCANNED_OR_IMAGE` 实际路由 → 语义检查对该 route_type 同样适用（扫描件 OCR 正是乱码高发区）。

---

## 2. 目标

1. 在 `ParseQualityCheckerService.check()` 既有四类检查之后，新增**第五类语义检查** `_check_semantic_quality(...)`。
2. 新增 issue code：

| Code | Severity | 含义 |
|------|----------|------|
| `OCR_GARBLED_RATIO` | WARNING（ratio≥warn）/ ERROR（ratio≥error） | `parsed_text.md` 乱码字符比例超阈值 |
| `OCR_LOW_CONFIDENCE` | WARNING | metadata `ocr_confidence.mean` 低于阈值 |
| `OCR_CONFIDENCE_UNAVAILABLE` | INFO | OCR 族 parser 但未提供置信度（不编造、不误报） |
| `TABLE_STRUCTURE_INCOMPLETE` | WARNING | Markdown 表格存在行列数不一致等结构残缺 |
| `EMPTY_BUT_NONZERO_PAGES` | ERROR | manifest `status=SUCCESS` 但正文为空且源内容非空 |

3. 阈值外置 `config/quality_semantics.yaml`（缺失用代码默认值）；**阈值必须真实生效**（验收含阈值变异复验）。
4. 报告 `schema_version` 升 `1.1`：`issue_counts` 并入新 code；`summary` 新增 `checked_semantic_count`。
5. 009 summarizer 联动扩展 `ISSUE_CODES` / `ALLOWED_PARSER_NAMES`（否则新 code 被判 unknown）。
6. 保持只读红线：不重解析、不修复、不改 DB、不改 parsed/raw_vault；幂等；单条失败不中断。

---

## 3. 范围（016 包含）

| 项 | 说明 |
|----|------|
| `parse_quality_checker.py` 演进 | 新增 `_check_semantic_quality` + 5 个 issue code + `ALLOWED_PARSER_NAMES` 增 `image_ocr` |
| `parse_quality_report_summarizer.py` 联动 | `ISSUE_CODES` 增 5 项、`ALLOWED_PARSER_NAMES` 增 `image_ocr`（009 其余逻辑不动） |
| `config/quality_semantics.yaml` | 新文件；阈值与开关；缺失用默认值 |
| CLI `check-parse-quality` | 新增 `--no-semantic`（关闭第五类，回到 008 行为，用于对照回归） |
| pytest | `test_parse_quality_checker_semantic.py`（否定式 + 阈值变异） |
| 009 既有测试回归 | `test_parse_quality_report_summarizer.py` 零修改通过 |

## 4. 非目标（016 明确不做）

| 非目标 | 说明 |
|--------|------|
| 触发重解析 / 自动修复 | 008 NG001/NG011 延续；`config/quality_actions.yaml` 的执行不在本 Spec |
| LLM 质检 / 语义相似度 | 008 NG016/NG017 延续；本 Spec 全部确定性启发式 |
| 修改 parsed / raw_vault / DB | 只读 |
| 修改 008 既有四类检查逻辑 | 仅新增；既有 18 个 code 语义零变更 |
| 修改 005/007/015 的产物契约 | 016 只消费既有字段；契约变更属各 parser Spec |
| schema migration | 禁止 |

---

## 5. 数据流

```text
kb_parse_result 候选（既有加载逻辑不变）
  → [既有四类检查，零变更]
  → 【第五类】_check_semantic_quality(candidate, manifest):
       只读 open parsed_text.md（内容）
       只读 open parsed_metadata.json（ocr_confidence / page 信息）
       → garbled 比率 / confidence / 表格结构 / 空正文 四项检测
       → issue 列表（带 evidence：实测值、阈值、采样大小）
  → parse_quality_report_{UTC}.json（schema_version=1.1）
```

**016 磁盘写入**：仅 `reports_root/parse_quality_report_*.json`（与 008 相同）。无任何其他写入。

---

## 6. 语义检查规则（摘要，详见 plan §7）

### 6.1 OCR_GARBLED_RATIO

- 乱码字符定义：U+FFFD 替换符、C0/C1 控制字符（除 `\n\t\r`）、Unicode 私有区、非常用符号区块的连续异常密度。
- `garbled_ratio = garbled_chars / total_chars`；`total_chars < min_sample_chars(200)` → 不评估（样本不足，不产 issue，不产 INFO——避免噪音）。
- `ratio >= garbled_error_ratio(0.10)` → ERROR；`>= garbled_warn_ratio(0.02)` → WARNING。

### 6.2 OCR_LOW_CONFIDENCE / OCR_CONFIDENCE_UNAVAILABLE

- 仅对 OCR 族 parser（`image_ocr`、`mineru`）评估。
- `parsed_metadata.json.ocr_confidence.mean` 存在且 `< confidence_min(0.70)` → WARNING。
- 字段缺失/为 null → `OCR_CONFIDENCE_UNAVAILABLE`（INFO）。**绝不**在缺数据时产 `OCR_LOW_CONFIDENCE`（反编造红线）。

### 6.3 TABLE_STRUCTURE_INCOMPLETE

- 解析 `parsed_text.md` 中的 pipe 表格；表头列数与任一行列数不一致（容差 0）、或分隔行缺失 → 该表计为残缺。
- `incomplete_ratio = incomplete_tables / total_tables`；`total_tables == 0` → 不评估；`incomplete_ratio > 0` 且 `incomplete_tables >= 1` → WARNING（evidence 含表位置行号）。

### 6.4 EMPTY_BUT_NONZERO_PAGES

- manifest `status == "SUCCESS"` 且 `parsed_text.md` 全文 strip 后为空，且（`manifest.content_size_bytes > 0` 或 `parsed_metadata.json` 页数 > 0）→ ERROR。
- 与 007 `EMPTY` 状态互补：此项抓「被错误标为 SUCCESS 的空产物」。

### 6.5 适用范围

仅对 registry `status=SUCCESS` 的候选做语义检查（FAILED/SKIPPED/EMPTY 已有既有 code 覆盖）；`--no-semantic` 关闭。

---

## 7. 业务规则

1. 全程只读：DB SELECT-only；`parsed_text.md` / `parsed_metadata.json` 只读 open；raw_vault 不碰。
2. 幂等：同产物 + 同阈值配置 → 同 issue 集合（`generated_at` 除外）。
3. 单候选语义检查异常（如文件读取 OSError）→ 记 WARNING 级 issue `SEMANTIC_CHECK_ERROR`？——**否**，归入既有模式：记 `errors` 语义化 evidence 到该候选的 issue（`OCR_*` 不适用时用一个通用 `SEMANTIC_EVALUATION_FAILED` INFO？）。**TL 决策（plan §9 Q3）**：读文件失败复用既有 `MISSING_PARSED_TEXT` / `INVALID_*` 体系，不新增错误类 code；检测函数内部异常 → 该候选记 `errors[]` continue，不中断。
4. 阈值全部来自 `config/quality_semantics.yaml` 或代码默认值；**禁止硬编码阈值进断言路径**（验收变异复验）。
5. 既有 008 四类检查与 18 个 code 的行为零变更；`--no-semantic` 输出必须与 008 现状逐字段一致。
6. CLI 入口 `ensure_readonly()`。

---

## 8. 硬约束清单（Dev / QA 必遵）

1. 016 只演进质检代码与阈值配置；不改任何 parser。
2. 不写 DB、不改 schema、无 migration。
3. 不改 `parsed/`、`raw_vault/`、`curated/`、`quarantine/`。
4. 不重解析、不调用任何解析/OCR/LLM。
5. 008 既有测试零修改通过；009 既有测试零修改通过。
6. 缺数据不编造：无 confidence 字段绝不产 `OCR_LOW_CONFIDENCE`；样本不足不产 `OCR_GARBLED_RATIO`；无表格不产 `TABLE_STRUCTURE_INCOMPLETE`。
7. dry-run 概念不适用（008 本就 check-only）；但报告写入是唯一产物，须保持。
8. 中文路径兼容（fixtures 中文目录下 parsed 产物）。

发现必须写 DB / 改 schema → **STOP → TL**。

---

## 9. 与 008 / 009 / 014 / 015 的边界

| Spec | 关系 |
|------|------|
| **008** | 016 是其第五类检查演进；`parse_quality_checker.py` 归属 016 白名单；008 既有行为由 `--no-semantic` 冻结 |
| **009** | `parse_quality_report_summarizer.py` 仅扩展两个常量；噪声分桶逻辑不动；新 code 默认归入需人工审阅桶（不自动归类 REAL_DEFECT，留 plan §9 Q4） |
| **014** | 不依赖；但 014 上线后 scanned PDF 的 OCR 产物是 `OCR_GARBLED_RATIO` 的主要消费场景 |
| **015** | 016 扩展 `ALLOWED_PARSER_NAMES += image_ocr` 是 015 manifest 能通过 008 校验的**前提**；`ocr_confidence` 字段契约由 015 spec §6 定义、016 消费 |

---

## 10. 成功标准（Spec 级）

1. 乱码 fixture → `OCR_GARBLED_RATIO`；干净 fixture → 无此 issue（对照）。
2. 低 confidence metadata → `OCR_LOW_CONFIDENCE`；缺 confidence → 仅 `OCR_CONFIDENCE_UNAVAILABLE`（INFO），绝无误报。
3. 残缺表格 fixture → `TABLE_STRUCTURE_INCOMPLETE`；规范表格 → 无。
4. SUCCESS + 空正文 + 非空源 → `EMPTY_BUT_NONZERO_PAGES`。
5. 阈值调高 → 对应 issue 消失（证明阈值真实接线，非硬编码假绿）。
6. `--no-semantic` 输出与 008 现状一致；008/009 回归零修改通过。
7. 只读红线证据：parsed/raw_vault hash 不变、DB 零写。

---

## 11. Owner 裁定记录（2026-10-16，全部 RESOLVED）

| # | 问题 | 裁定 |
|---|------|------|
| **O-1** | SPEC_INDEX 激活 016 | **APPROVED**：Loop S1 整体激活，SPEC_INDEX §1 已登记 |
| **O-2** | 阈值配置位置 | **APPROVED**：新文件 `config/quality_semantics.yaml` |
| **O-3** | 默认阈值 | **FROZEN**：`garbled.warn_ratio=0.02` / `error_ratio=0.10`、`confidence.min_mean=0.70`、`min_sample_chars=200`（009 数据回流后可再校准，届时另提变更） |
| **O-4** | `schema_version` 升 1.1 | **APPROVED**：009/013 均为增量键兼容 |

---

**Spec 结束** — 实现细节见 `plan.md`、`acceptance.md`。
