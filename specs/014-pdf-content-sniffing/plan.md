# Plan: PDF 内容嗅探路由修复（014 实现计划）

> **项目**：`doc2md`（个人历史项目文档知识库）
> **项目路径**：`/home/szf/dev/doc2md`
> **版本基线**：V1.1-SDD / Loop S1
> **编写日期**：2026-10-16
> **前置条件**：001–013、902 已完成；Owner 已批准 spec.md §12 O-1 ~ O-5
> **编写角色**：Tech Lead（spec-lead）— Loop S1 G0
> **建议分支**：`feature/014-pdf-content-sniffing`

---

## 1. 背景与当前阶段

见 `spec.md` §1。核心缺陷：`parser_routing.py:104-110` 把所有 `.pdf` 判 `PDF_DIGITAL`，`PDF_SCANNED_OR_IMAGE` 是死枚举。014 在**仅 PDF、只读、文本层探测**的限定开口下修复。

---

## 2. 014 目标

1. 新增 `backend/app/core/pdf_sniffing.py`：基于 pypdf 的确定性文本层嗅探。
2. `ROUTING_RULES_VERSION` → `004_mvp_v2`；PDF 分支在 v2 下经嗅探区分 digital / scanned。
3. `--routing-rules-version 004_mvp_v1` 显式回退（逐字节复现 004 现状行为）。
4. 内容寻址嗅探缓存，防投毒，可整体删除重建。
5. 007 `_build_plan` 注入同一嗅探函数，保证 router 与 007 判定一致。
6. 幂等、单条失败不中断、dry-run 零副作用、中文路径兼容。

---

## 3. 014 非目标

见 `spec.md` §4。特别强调：不做 OCR、不渲染页面、不写 DB、不改其他扩展名分支。

---

## 4. 输入数据

### 4.1 MySQL（只读，与 004 相同）

| 表 | 014 操作 |
|----|----------|
| `kb_file_content` | 只读 |
| `kb_raw_vault_object` | 只读（定位 `vault_path`） |
| `kb_file_instance` | 只读（ext fallback） |
| 其余表 | 不读写 |

候选条件不变：`sha256 IS NOT NULL AND status='CONTENT_REGISTERED' AND vault_status='COPIED'`。

### 4.2 Vault 输入（014 新增的唯一开口）

```text
{raw_vault_root}/by_hash/{sha256[0:2]}/{sha256[2:4]}/{sha256}/original.bin
```

- 必须经 `build_vault_dir()` / `build_vault_artifact_paths()` 定位，禁止自行拼接。
- **只读 open**；仅当 `file_ext`（或 fallback）归一化为 `.pdf` 且 rules_version=v2 时才打开。
- vault 路径缺失 / `original.bin` 不存在 → 不嗅探，沿用 004 v1 行为（`PDF_DIGITAL`）+ WARNING reason（无法读内容时不引入新不确定性，保持与 004 报告兼容；见 §8.5 决策 D-3）。

### 4.3 配置输入

`config/pdf_sniffing.yaml`（**新文件**，缺失时全部用代码内默认值）：

```yaml
pdf_sniffing:
  sniffer_version: "pdf_sniff_v1"
  pages_max: 20                # 采样页数上限
  digital_min_total_chars: 100 # 采样页累计可抽取字符下限
  digital_min_avg_chars_per_page: 20
  cache_enabled: true
```

`config/app.yaml`（`app.example.yaml` 同步）：`storage.sniff_cache_root: ./sniff_cache`。

---

## 5. 输出数据

### 5.1 嗅探缓存（新产物）

```text
{sniff_cache_root}/by_hash/{sha256[0:2]}/{sha256}.json
```

```json
{
  "cache_type": "pdf_sniff_cache",
  "sha256": "<sha256>",
  "sniffer_version": "pdf_sniff_v1",
  "status": "TEXT_LAYER_PRESENT",
  "text_chars": 5320,
  "pages_total": 12,
  "pages_sampled": 12,
  "avg_chars_per_page": 443.3,
  "created_at": "2026-10-16T00:00:00Z"
}
```

**缓存读取校验**（任一不满足即弃用并重算，不写错误日志以外的东西）：

1. JSON 可解析且 `cache_type == "pdf_sniff_cache"`；
2. `sha256` 与请求内容一致；
3. `sniffer_version` 与当前一致。

### 5.2 路由报告扩展

`parser_route_report_{UTC}.json`：

- 顶层新增 `routing_rules_version: "004_mvp_v2"`；
- 每条 PDF decision 新增 `pdf_sniff` 块：

```json
{
  "route_type": "PDF_SCANNED_OR_IMAGE",
  "rule_name": "pdf_sniff_scanned",
  "pdf_sniff": {
    "status": "TEXT_LAYER_ABSENT",
    "text_chars": 0,
    "pages_sampled": 8,
    "from_cache": false,
    "sniffer_version": "pdf_sniff_v1"
  }
}
```

非 PDF decision 不含 `pdf_sniff` 键（而非 null），保持 v1 报告对非 PDF 的 schema 不变。

### 5.3 CLI 汇总

在 004 既有汇总行后追加：`PDF sniffed: N (digital: N, scanned: N, cache_hits: N)`。

---

## 6. 关键数据对象

```text
SniffStatus(str Enum)
  TEXT_LAYER_PRESENT | TEXT_LAYER_ABSENT | ENCRYPTED
  | EMPTY_DOCUMENT | SNIFF_ERROR | DEPENDENCY_MISSING

PdfSniffResult(dataclass)
  status: SniffStatus
  text_chars: int
  pages_total: int
  pages_sampled: int
  avg_chars_per_page: float
  from_cache: bool
  sniffer_version: str
  detail: str            # 人类可读；SNIFF_ERROR 时含异常类型名（不含栈）
```

---

## 7. 模块设计

### 7.1 新增 `backend/app/core/pdf_sniffing.py`

```text
SNIFFER_VERSION = "pdf_sniff_v1"

def sniff_pdf(original_bin: Path, *, config: PdfSniffConfig) -> PdfSniffResult
def load_sniff_cache(cache_root: Path, sha256: str, sniffer_version: str) -> PdfSniffResult | None
def save_sniff_cache(cache_root: Path, sha256: str, result: PdfSniffResult) -> None
```

`sniff_pdf` 内部：

1. pypdf import 失败 → `DEPENDENCY_MISSING`（不抛）。
2. `PdfReader(strict=False)` 打开；`is_encrypted` → `ENCRYPTED`（不尝试解密口令）。
3. `len(pages) == 0` → `EMPTY_DOCUMENT`。
4. 取前 `pages_max` 页 `extract_text() or ""`，累计 `text_chars`；任一页抛异常 → 整档 `SNIFF_ERROR`（fail-closed，不做部分采样折算）。
5. 阈值比较 → `TEXT_LAYER_PRESENT` / `TEXT_LAYER_ABSENT`。
6. 一切未预见异常 → `SNIFF_ERROR`，`detail=type(exc).__name__`。

### 7.2 `parser_routing.py` 演进（最小 diff）

```text
ROUTING_RULES_VERSION = "004_mvp_v2"
ROUTING_RULES_VERSION_V1 = "004_mvp_v1"

PDF_SNIFF_RULE_NAMES = frozenset({
    "pdf_sniff_digital", "pdf_sniff_scanned", "pdf_sniff_encrypted",
    "pdf_sniff_empty", "pdf_sniff_error", "pdf_sniff_unavailable",
})

def route_pdf_with_sniff(sniff: PdfSniffResult) -> tuple[RouteType, str, str, str, str]
```

`match_route_type(...)` 签名与 v1 行为**一字不改**（004 既有测试全保留）。v2 的 PDF 判定由 service 层先嗅探、再调 `route_pdf_with_sniff`；非 PDF 分支继续走 `match_route_type`。

`route_pdf_with_sniff` 映射表 = `spec.md` §6。所有 sniff 状态的 `future_parser_hint` 均为 `MINERU_FAMILY`（MinerU `-m auto` 同时覆盖 digital 与 scanned）。

### 7.3 `ParserRouterService` 演进

`route_parsers(...)` 新增参数：`rules_version: str = ROUTING_RULES_VERSION`、`use_sniff_cache: bool = True`、`dry_run: bool = False`。

`_route_content` 内 PDF 分支（v2）：

```text
ext == .pdf:
  vault_dir 定位（复用 007 _resolve_vault_dir 同款逻辑；vault/bin 缺失 → 回退 v1 判定 + WARNING，见 D-3）
  cache_enabled 且 use_sniff_cache → load_sniff_cache
  miss → sniff_pdf() →（非 dry_run 且 cache_enabled）save_sniff_cache
       └ 澄清 D-4：仅内容确定性判定入缓存——TEXT_LAYER_PRESENT / TEXT_LAYER_ABSENT / ENCRYPTED / EMPTY_DOCUMENT；DEPENDENCY_MISSING（环境态）与 SNIFF_ERROR（可能为瞬时态）不写缓存，缓存不承载环境态权威、可整体重建
  route_pdf_with_sniff(result) → decision
```

### 7.4 007 `_build_plan` 联动（白名单内最小改动）

`mineru_pdf_parser.py::_build_plan` 当前直接调 `match_route_type`（`mineru_pdf_parser.py:415-419`）。014 在其后追加：

```text
if route_type == RouteType.PDF_DIGITAL and original_bin.is_file():
    sniff = <同 §7.3 的缓存+嗅探管线>
    route_type, _, rule_name, _hint, reason = route_pdf_with_sniff(sniff)
```

保证「router 判 scanned 的内容，007 不会按 digital 处理」。`IN_SCOPE_ROUTE_TYPES` 已含两个 PDF 类型，无需扩展。

---

## 8. 关键设计决策

### 8.1 D-1 嗅探位置：router，而非 MinerU `_build_plan` 内部私有

| 候选 | 评价 |
|------|------|
| 放 router（service 层） | **采纳**。`route_type` 是 router 的合同；008/016 按 route_type 聚合需要权威来源；007 `_build_plan` 复用同一函数保持一致 |
| 只放 `_build_plan` | 否决。router 报告仍输出错误 route_type，缺陷未修复 |

嗅探**函数**放 `core/pdf_sniffing.py`（纯函数），嗅探**触发**放 service 层（router service + 007 plan），`match_route_type` 保持纯元数据 → 004 全部既有单测不破。

### 8.2 D-2 fail-closed 方向：不确定 → `PDF_SCANNED_OR_IMAGE`

- 误判 digital 的代价：007 可能产出空 `parsed_text.md`（无 OCR），且 008 现状查不出「该 OCR 而未 OCR」。
- 误判 scanned 的代价：MinerU 多走 OCR 路径，仅算力损耗。
- 故所有不确定状态（加密/损坏/空页/依赖缺失）统一归 scanned 侧，`rule_name` 区分原因。

### 8.3 D-3 vault/bin 缺失时回退 v1 判定

无法读内容时不引入新不确定性：沿用 `ext_pdf_digital` + reason 追加 `sniff skipped: original.bin not accessible`。与 004 报告语义兼容，008 的 `MISSING_RAW_VAULT_OBJECT` 会独立发现该问题。

### 8.4 D-4 缓存为派生物，非权威

缓存可整体 `rm -rf` 重建；缓存永不被当作路由权威来源（sha256 内容寻址 + 版本校验保证了这一点）。dry-run 不写缓存。

### 8.5 D-5 v1 回退通道

`--routing-rules-version 004_mvp_v1` 完全绕过嗅探（不 import pypdf、不读 bin、不读缓存），用于对照回归与应急降级。

---

## 9. Dev 实现边界

### 9.1 允许修改（白名单）

| 操作 | 文件 |
|------|------|
| 新增 | `backend/app/core/pdf_sniffing.py` |
| 修改 | `backend/app/core/parser_routing.py`（§7.2 范围） |
| 修改 | `backend/app/services/parser_router.py`（§7.3 范围） |
| 修改 | `backend/app/services/mineru_pdf_parser.py`（**仅** §7.4 的 `_build_plan` 注入） |
| 修改 | `backend/app/cli/main.py`（`route-parsers` 新增选项） |
| 修改 | `backend/app/core/config.py`（`storage.sniff_cache_root` 可选字段） |
| 新增 | `config/pdf_sniffing.yaml` |
| 修改 | `config/app.example.yaml` |
| 修改 | `backend/requirements.txt`（+`pypdf`，O-2 批准后） |
| 新增 | `backend/tests/test_pdf_sniffing.py`、`backend/tests/test_parser_router_v2.py` |
| 新增 | PDF fixtures（由测试代码用 pypdf 现场生成，**不**提交二进制 fixture） |
| 修改 | `specs/014-pdf-content-sniffing/tasks.md`（勾选） |

### 9.2 禁止修改

```text
backend/app/services/inventory_scanner.py
backend/app/services/file_content_vault.py
backend/app/services/duplicate_governance.py
backend/app/services/markitdown_parser.py
backend/app/services/parse_quality_checker.py   # 016 范围
backend/app/models/**                            # 无 schema 变更
sql/**  generated/**  raw_vault/**  data/**
specs/001..013/**  specs/902/**                  # 封版规格
config/parser_rules.yaml                         # 014 不消费它
```

### 9.3 测试先行

先写 `test_pdf_sniffing.py` / `test_parser_router_v2.py`（红）→ 实现 → 绿。fixture 生成器：

- **digital**：pypdf `PdfWriter` + 注入文本页（若 pypdf 不便写文本，则用 `reportlab` 之外的最简手段：pypdf 空白页 + 手工构造 content stream，或用 annotation；Dev 自选，但必须纯生成、可重复）。
- **scanned**：pypdf 空白页 PDF（无文本层）。
- **encrypted**：pypdf `writer.encrypt("pw")`。
- **corrupted**：截断的字节序列（`.pdf` 扩展名，非法内容）。

---

## 10. 异常处理设计

| 场景 | 处理 |
|------|------|
| pypdf 未安装 | `DEPENDENCY_MISSING` → fail-closed scanned；WARNING 日志；批处理继续 |
| PDF 加密 | `ENCRYPTED` → scanned + `pdf_sniff_encrypted`；continue |
| PDF 损坏/截断 | `SNIFF_ERROR` → scanned + `pdf_sniff_error`；continue |
| 缓存文件损坏/JSON 非法 | 弃用重算；WARNING |
| 缓存目录不可写 | WARNING；路由结果不受影响 |
| vault bin 缺失 | 回退 v1 判定（D-3）；WARNING |
| 单条意外异常 | `errors[]`；continue |
| `--sha256` 不存在 | 空结果 exit 0（对齐 004） |

---

## 11. 幂等性设计

| 场景 | 行为 |
|------|------|
| 重复 `route-parsers` | 第二次命中缓存，`from_cache=true`；`route_type`/`rule_name`/`reason` 逐字段一致 |
| 缓存与当前版本不符 | 重算并覆盖缓存文件（同名原子写：先写 tmp 再 rename） |
| dry-run | 不写缓存/报告；重复 dry-run 结果一致 |
| v1 / v2 切换 | 报告 `routing_rules_version` 如实记录；v1 不读缓存 |

---

## 12. Acceptance Criteria 摘要

详见 `acceptance.md`（A014-01 ~ A014-12，全部否定式可执行）。

---

## 13. TL 决策表（Dev 必遵）

| # | 问题 | 决策 |
|---|------|------|
| Q1 | 嗅探触发位置 | service 层（router + 007 plan），`match_route_type` 保持纯元数据 |
| Q2 | fail-closed 方向 | 一切不确定 → `PDF_SCANNED_OR_IMAGE` |
| Q3 | 缓存位置 | `storage.sniff_cache_root`（默认 `./sniff_cache`），文件派生缓存，不落 DB |
| Q4 | 阈值来源 | `config/pdf_sniffing.yaml`，缺失用代码默认值 |
| Q5 | v1 兼容 | `--routing-rules-version 004_mvp_v1` 逐字节复现旧行为 |
| Q6 | 加密 PDF | 不解密、不尝试口令 → `pdf_sniff_encrypted` |
| Q7 | PDF fixtures | 测试内用 pypdf 现场生成，不提交二进制 |
| Q8 | 007 修改范围 | 仅 `_build_plan` 嗅探注入；`IN_SCOPE_ROUTE_TYPES` 不动 |

---

## 14. 流水线与 STOP 点

```text
G0 规格（本文档）→ Owner 裁定 O-1~O-5 → P2 DB Review（确认零 schema 变更）
→ P4 Dev（测试先行）→ STOP → P5 QA → P6 E2E → P7 TL Final Review
```

Dev **不得**自我宣布验收通过。

---

**Plan 结束** — Dev 先读 `acceptance.md` 与 §9 白名单再写代码。
