# Spec: PDF 内容嗅探路由修复（014 PDF Content Sniffing）

> **项目**：个人历史项目文档知识库 `doc2md`（原 `pkb_sdd` 演进）
> **版本基线**：V1.1-SDD / Loop S1 规格门禁 G0
> **前置条件**：001–013、902 已完成并 merge `main`；004-parser-router 已交付
> **详细实现计划**：见同目录 `plan.md`
> **定位**：004 路由器的**缺陷修复型演进** Spec — 修复 `.pdf` 一律判 `PDF_DIGITAL`、`PDF_SCANNED_OR_IMAGE` 永不产生的死枚举问题
> **注意**：`specs/SPEC_INDEX.md` §4.2 当前明确「Do not start: `014` or any unlisted spec」。本目录生效以 Owner 完成 Active Spec Selection Review 为前提（见 §12 开放问题 O-1）。

---

## 1. 背景

### 1.1 缺陷现状（代码级证据）

`backend/app/core/parser_routing.py:104-110`：

```text
if effective_ext == ".pdf":
    return _route(
        RouteType.PDF_DIGITAL,
        "ext_pdf_digital",
        FutureParserHint.MINERU_FAMILY,
        "file_ext .pdf maps to PDF_DIGITAL",
    )
```

- 所有 `.pdf` 无条件判为 `PDF_DIGITAL`。
- `RouteType.PDF_SCANNED_OR_IMAGE`（`parser_routing.py:28`）已定义，但**全代码库无任何赋值路径**，是死枚举。
- 后果：扫描版 PDF 以 `PDF_DIGITAL` 进入 007 MinerU 管线；MinerU `-m auto` 虽可自行 OCR，但路由层丧失了「该内容需要 OCR 路线」的语义，下游（008 质检按 route_type 聚合、未来 OCR 专属质检与计费估算）全部失真。

### 1.2 004 既有边界与本次变更

004 规格（`specs/004-parser-router/spec.md` §4 / A006）明确禁止「读取 `original.bin` 内容做判断」，因此 MVP 把所有 `.pdf` 判为 `PDF_DIGITAL` 是**当时有意的设计妥协**（004 plan §23.4 Q3）。

014 是**经授权的边界修订**：仅对 `.pdf` 路由开放「只读打开 vault 内 `original.bin` 做文本层嗅探」的能力，其余文件类型的「不读内容」红线**不变**。该修订必须经 Owner 批准（O-1）。

### 1.3 在链路中的位置

```text
001 scan → 002 copy-to-vault → [003] → 【004+014 route-parsers（PDF 嗅探）】
  → 005 markitdown / 007 mineru-pdf（消费 route_type）
  → 008+016 质检（按 route_type 聚合）
```

---

## 2. 目标

1. 为 `.pdf` 路由增加**确定性内容嗅探**：基于 pypdf 探测文本层（可抽取字符数 / 页均字符数），区分 `PDF_DIGITAL` 与 `PDF_SCANNED_OR_IMAGE`。
2. **Fail-closed**：嗅探失败、加密 PDF、pypdf 缺失、零页 PDF 等一切不确定情形，**绝不**默认 `PDF_DIGITAL`，统一落入 `PDF_SCANNED_OR_IMAGE` 并记录明确 `rule_name` 与 `reason`（理由见 plan §8.4：误判 digital 会导致无 OCR 的空解析，误判 scanned 仅多花 OCR 算力）。
3. `ROUTING_RULES_VERSION` 升 `004_mvp_v2`；保留 `--routing-rules-version 004_mvp_v1` 显式回退旧行为（纯元数据、不读 bin）。
4. 嗅探结果**可缓存**（按 sha256 + sniffer 版本寻址），避免每次 route 重复读文件；缓存必须防投毒（校验失败即重算）。
5. 保持 004 全部既有红线中未修订部分：原始文件只读、幂等、单条失败不中断、不写 DB、不写 parsed/。
6. pytest 测试先行，验收用例为**否定式可执行**（见 `acceptance.md`）。

---

## 3. 范围（014 包含）

| 项 | 说明 |
|----|------|
| `backend/app/core/pdf_sniffing.py` 新模块 | pypdf 文本层嗅探，纯函数，可独立单测 |
| `parser_routing.py` 演进 | `ROUTING_RULES_VERSION = "004_mvp_v2"`；新增 sniff 相关 `rule_name`；`match_route_type` 保持纯元数据签名不变（嗅探在 service 层注入，见 plan §9） |
| `ParserRouterService` 演进 | PDF 候选在 v2 规则下触发嗅探；嗅探结果写入报告 `decisions[].pdf_sniff` |
| 007 `_build_plan` 最小联动 | `MineruPdfParserService._build_plan` 复用同一嗅探函数，保证 007 侧 route 判定与 004 一致（否则 router 判 scanned、007 仍按旧规则判 digital，语义分裂） |
| 嗅探缓存 | `{sniff_cache_root}/by_hash/{sha256[:2]}/{sha256}.json`，内容寻址 + 版本校验 |
| CLI `route-parsers` 扩展 | `--routing-rules-version`、`--no-sniff-cache`、`--dry-run` |
| 配置 | `config/pdf_sniffing.yaml`（阈值；Dev 自带默认值，yaml 缺失时用默认） |
| pytest + CLI E2E | 见 `acceptance.md` |

## 4. 非目标（014 明确不做）

| 非目标 | 说明 |
|--------|------|
| OCR / 图像检测扫描件 | 只探测**文本层**；不渲染页面、不做图像熵分析 |
| 修改 raw_vault / original.bin | 只读打开 |
| 写 DB / 改 schema / migration | 缓存走文件系统，不落 `kb_file_content` |
| 修改 004 规则表其他条目 | 仅 `.pdf` 分支演进 |
| 解密 PDF | 加密文件不做字典爆破/口令尝试，直接 fail-closed |
| IMAGE 独立图片路由修复 | 属于 015 |
| 语义质检 | 属于 016 |
| 修改 `parser_rules.yaml` 既有 routing_rules 段 | 该文件当前未被 `parser_routing.py` 消费，014 不动它 |

---

## 5. 数据流

```text
kb_file_content（vault_status=COPIED, file_ext=.pdf）
  → v2 规则：定位 raw_vault/.../original.bin（只读）
  → 查嗅探缓存（sha256 + sniffer_version 校验）
  → miss/失效 → pypdf 嗅探（采样前 N 页）→ 写缓存
  → SniffResult(text_chars, pages_sampled, status)
  → route_type = PDF_DIGITAL | PDF_SCANNED_OR_IMAGE（fail-closed）
  → parser_route_report_{UTC}.json（decisions[].pdf_sniff 附证据）
```

**014 磁盘写入**：`sniff_cache/**`、`reports_root/parser_route_report_*.json`。
**014 不写入**：`raw_vault/`、`parsed/`、`curated/`、`quarantine/`、MySQL。
`--dry-run`：**零磁盘写入**（不写缓存、不写报告，仅 stdout）。

---

## 6. 嗅探判定规则（摘要，详见 plan §8）

| SniffResult.status | 条件（默认阈值，yaml 可调） | route_type | rule_name |
|--------------------|------------------------------|------------|-----------|
| `TEXT_LAYER_PRESENT` | `total_chars >= 100` 且 `avg_chars_per_page >= 20` | PDF_DIGITAL | `pdf_sniff_digital` |
| `TEXT_LAYER_ABSENT` | 不满足上式 | PDF_SCANNED_OR_IMAGE | `pdf_sniff_scanned` |
| `ENCRYPTED` | pypdf `is_encrypted` | PDF_SCANNED_OR_IMAGE | `pdf_sniff_encrypted` |
| `EMPTY_DOCUMENT` | 0 页 | PDF_SCANNED_OR_IMAGE | `pdf_sniff_empty` |
| `SNIFF_ERROR` | 文件损坏/解析异常 | PDF_SCANNED_OR_IMAGE | `pdf_sniff_error` |
| `DEPENDENCY_MISSING` | pypdf 未安装 | PDF_SCANNED_OR_IMAGE | `pdf_sniff_unavailable` |

采样上限：`sniff_pages_max = 20`（页数 ≤20 全量，>20 取前 20 页，`avg` 按采样页计）。**确定性**：同 sha256 + 同 sniffer 版本 → 同结果。

---

## 7. 业务规则

1. 原始文件只读；嗅探只读打开 vault `original.bin`；禁止 `shutil.move` / `unlink` / `rename`。
2. 幂等：同 sha256 + 同 rules/sniffer 版本 → 同 `route_type` / `rule_name` / `reason`。
3. 单 content 失败记 `errors[]` 并 continue；嗅探自身异常**不抛出**，转化为 `SNIFF_ERROR` fail-closed 结果。
4. 缓存键 = sha256；缓存校验 = sha256 匹配 + `sniffer_version` 匹配；校验失败静默重算（不沿用脏缓存）。缓存仅承载**内容确定性判定**（TEXT_LAYER_PRESENT / TEXT_LAYER_ABSENT / ENCRYPTED / EMPTY_DOCUMENT）；`DEPENDENCY_MISSING`（环境态）与 `SNIFF_ERROR`（可能为瞬时态）不写缓存，故缓存不承载环境态权威、可整体重建（澄清 D-4）。
5. 缓存写失败仅 WARNING，不影响路由结果。
6. `--routing-rules-version 004_mvp_v1` 必须**逐字节复现** v1 行为（所有 `.pdf` → `PDF_DIGITAL`，不读 bin），用于回归对照。
7. CLI 入口调用 `ensure_readonly()`。
8. 报告新增 `routing_rules_version` 与每条 decision 的 `pdf_sniff` 证据块（`status`、`text_chars`、`pages_sampled`、`from_cache`、`sniffer_version`）。

---

## 8. 硬约束清单（Dev / QA 必遵）

1. 014 只演进 `.pdf` 路由判定；其他扩展名规则逐字不变。
2. 014 不调用 MinerU / MarkItDown / OCR 引擎。
3. 014 不渲染 PDF 页面为图像。
4. 014 不写 `parsed/`、`curated/`、`quarantine/`、`raw_vault/`、MySQL。
5. 014 不修改 SQL schema、不新增 migration。
6. 014 不修改 `inventory_scanner.py` / `file_content_vault.py` / `duplicate_governance.py` / `markitdown_parser.py`。
7. 对 `mineru_pdf_parser.py` 的修改**仅限** plan §9.3 白名单的 `_build_plan` 嗅探注入。
8. 嗅探 fail-closed：任何不确定 → 非 `PDF_DIGITAL`。
9. dry-run 零副作用。
10. 新增依赖仅限 `pypdf`（纯 Python）；需 Owner 批准（O-2）。

若实现阶段发现必须写 DB / 改 schema → **STOP → TL**。

---

## 9. 与 004 / 007 / 015 / 016 的边界

| Spec | 关系 |
|------|------|
| **004** | 014 是其缺陷修复演进；`parser_routing.py` 归属 014 白名单；004 其余语义封闭 |
| **007** | 消费 `PDF_DIGITAL` / `PDF_SCANNED_OR_IMAGE`（`IN_SCOPE_ROUTE_TYPES` 已含两者，`mineru_pdf_parser.py:56-61` 无需扩展）；`_build_plan` 注入嗅探保证两侧一致 |
| **015** | IMAGE 路由与图片管线归属 015；014 不动 `IMAGE_EXTENSIONS` 分支 |
| **016** | 语义质检归属 016；016 可按新 route_type 聚合，014 不动质检代码 |

---

## 10. 成功标准（Spec 级）

1. 数字版 PDF fixture → `PDF_DIGITAL`；扫描版 fixture → `PDF_SCANNED_OR_IMAGE`（死枚举激活）。
2. 加密/损坏/空页/pypdf 缺失 → 全部 fail-closed 为 `PDF_SCANNED_OR_IMAGE`，无未捕获异常。
3. 第二次 route 命中缓存，`from_cache=true`，决策逐字段一致。
4. `--routing-rules-version 004_mvp_v1` 行为与 004 现状完全一致。
5. raw_vault 与原始用户文件 hash/listing 不变。
6. dry-run 零磁盘写入。
7. 001–013 既有 pytest 回归通过。

---

## 11. 后续阶段

见 `plan.md` 流水线（P2 DB Review（确认无 schema 变更）→ P4 Dev → P5 QA → P6 E2E → P7 TL Final Review）。

---

## 12. Owner 裁定记录（2026-10-16，全部 RESOLVED）

| # | 问题 | 裁定 |
|---|------|------|
| **O-1** | SPEC_INDEX §4.2「Do not start 014」 | **APPROVED**：Loop S1（014→015→016→017）整体激活，SPEC_INDEX §1 已登记 |
| **O-2** | 新增第三方依赖 `pypdf` | **APPROVED**：`pypdf` 进 `backend/requirements.txt` |
| **O-3** | 修订 004 A006「不读 original.bin」红线 | **APPROVED**：限定开口 = 只读、仅 `.pdf`、仅文本层探测 |
| **O-4** | 新增 `sniff_cache/` 磁盘写入位置 | **APPROVED**：`storage.sniff_cache_root`，派生缓存可整体删除重建 |
| **O-5** | 嗅探阈值默认值 | **FROZEN**：`digital_min_total_chars=100`、`digital_min_avg_chars_per_page=20`、`pages_max=20`（`config/pdf_sniffing.yaml`；016 数据回流后可再校准，届时另提变更） |

---

**Spec 结束** — 实现细节见 `plan.md`、`acceptance.md`。
