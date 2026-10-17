# PDF 富文本 → AI 可用文本（含 Document IR）：真实运行结果

> **产出**：2026-10-17 22:2x CST
> **链路**：`InventoryScanner` → `FileContentVaultService` → `MineruPdfParserService.parse_many`（真实 `magic-pdf` 子进程）→ 014 内容嗅探路由 → 三件套 + `ir_source/` → `document_ir_builder` → `document_ir.json`（017）
> **解释器**：`/home/szf/anaconda3/bin/python`（3.11.7） · `magic-pdf` 1.3.12 · `transformers` 4.57.6

---

## 1. 结果文件

| 文件 | 说明 |
|---|---|
| `parsed_text.md` | **AI 可用文本**（Markdown；标题层级 + 段落 + 表格 HTML + 图片引用） |
| `parse_manifest.json` | 产物清单：`status=SUCCESS`、`asset_files[].sha256`、**`ir_source_files`**（017 additive） |
| `parsed_metadata.json` | `parser_name=mineru`、`route_type=PDF_DIGITAL`（014 嗅探真实生效） |
| **`document_ir.json`** | **017 Document IR**：4 块（heading / paragraph / table / figure）、`tables[0]` 4×4 共 16 个 cells、figure asset 与 manifest 的 sha256 对齐、`stats` 由数据复算 |
| `ir_source/content_list.json` | MinerU 结构化中间件（IR 的输入源，本次由 parser 新增保留） |
| `ir_source/middle.json` | MinerU 原始版面中间件（可选源） |
| `ir_source/mineru.config.json` | 本次实际配置（table 开、formula 关；见 §4） |
| `ir_source/magic_pdf_patched.py` | 公式模型的运行时兼容补丁（见 §4.2） |
| `assets/images/*.jpg` | 抽出的 2 张位图（表格截图 + 折线图） |
| `make_demo_pdf.py` | 样例 PDF 生成脚本（可复现；**必须用真实 CJK 字体**，见 §5） |

`parsed_text.md` 全文（表格已结构化，不再是图片）：

```markdown
# 2026 Q3 供应链季度复核报告

本季度核心企业客户营收同比增长 12.4%， 日均存款余额 8.95 亿元， 较上季度提升 6.1%。
用电量同比上升 9.8%， 纳税开票金额同比上升 11.2%， 两项交叉验证支持营收增长的真实性。
供应链上下游集中度由 62% 降至 54%， 但单一最大供应商占比仍达 23%， 需持续监测替代可行性。

表 1 主要经营指标 （单位： 万元）

<html><body><table><tr><td>指标</td><td>本期</td><td>上期</td><td>同比</td></tr>
<tr><td>营业收入</td><td>12480</td><td>11104</td><td>+12.4%</td></tr>
<tr><td>日均存款</td><td>89500</td><td>84356</td><td>+6.1%</td></tr>
<tr><td>用电量(kWh)</td><td>2310000</td><td>2103900</td><td>+9.8%</td></tr></table></body></html>

![](images/1eae9ca4….jpg)
图 1 季度营收趋势
```

`document_ir.json` 关键结构：

```json
{
  "schema_version": "document-ir/1.0",
  "document_uid": "doc_0d4cf91aa65bf22e_017_mvp_v1",
  "coordinate_system": {"origin": "top_left", "unit": "pixel", "page_ref": "per_page"},
  "blocks": [
    {"block_id": "b0001", "type": "heading",   "content_ref": {"kind": "text_span", "start_offset": 2, "end_offset": 19}},
    {"block_id": "b0002", "type": "paragraph", "content_ref": {"kind": "text_span", "start_offset": 21, "end_offset": 179}},
    {"block_id": "b0003", "type": "table",     "content_ref": {"kind": "table_ref", "table_id": "t001"}},
    {"block_id": "b0004", "type": "figure",    "content_ref": {"kind": "figure_ref", "figure_id": "f001"}}
  ],
  "tables":  [{"table_id": "t001", "n_rows": 4, "n_cols": 4, "cells": ["… 16 个 …"]}],
  "figures": [{"figure_id": "f001", "asset_path": "assets/images/1eae9ca4….jpg",
               "asset_sha256": "20273eb8…", "caption": "图 1 季度营收趋势"}],
  "stats": {"block_count": 4, "table_count": 1, "figure_count": 1,
            "blocks_without_bbox": 4, "blocks_low_confidence": 0}
}
```

> `blocks_without_bbox=4` 是**如实计数**：MinerU 1.3.12 的 `content_list.json` 不带
> bbox，按 D-2 一律置 `null` 并计数，**不做估算填充**。bbox 需从 `middle.json` 的版面块
> 另做映射（待 P2 后续补，属已知缺口）。

---

## 2. 三项限制的处置结果

| # | 限制 | 处置 | 结果 |
|---|---|---|---|
| 1 | 表格被降级为图片 | 启用表格识别模型（`table-config.enable=true`，`rapid_table`） | **已解决**：`parsed_text.md` 内嵌表格 HTML；`content_list` 该块有 `table_body`；IR 侧展开为 4×4 / 16 cells（虚格不入） |
| 2 | 公式模型崩溃（`cache_position` / 旧缓存协议） | 运行时补丁 `ir_source/magic_pdf_patched.py`：剥离 `cache_position`、将 `past_key_values` 置空并关缓存 | **崩溃已解决**（`MFR Predict 3/3` 正常执行）；但见 §4.2 的覆盖局限 |
| 3 | 无 `document_ir.json` | 新增 `app/services/document_ir_builder.py`（MinerU content_list → IR / MarkItDown 降级），并给 parser 增加 **ir_source additive 保留**（`parsed_dir/ir_source/` + `manifest.ir_source_files`） | **已解决**：`document_ir.json` 已产出并通过 `validate_document_ir` 全量校验（含 asset sha256 对齐、stats 复算、reading_order 置换） |

---

## 3. 真实数字

| 项 | 值 |
|---|---|
| 源文件 sha256 | `0d4cf91aa65bf22e607ed319b2a8b6af7e223ef45a79fdef0bd6131dad2b5603` |
| `content_list` 块数 | 4（`text(heading)` / `text` / `table` / `image`） |
| IR 块数 / 表 / 图 | 4 / 1（4×4，16 cells）/ 1（asset sha256 `20273eb8…`） |
| `parsed_text.md` | 848 字节（含表格 HTML） |
| 解析结果 | `status=SUCCESS`、`failed_count=0`、`ir_source_files` 2 项（`content_list.json` `c26900f7…` / `middle.json` `7415deda…`） |
| 路由 | `rule_name=pdf_sniff_digital` → `PDF_DIGITAL` |

---

## 4. 仍需知道的边界（不掩盖）

### 4.1 bbox 缺失
`content_list.json` 无 bbox → IR 全块 `bbox=null`、`coordinate_system.unit` 取 `pixel`（声明性）。真实 bbox 需从 `middle.json` 版面块映射，属 P2 剩余项，且单位（`pdf_point` vs `pixel`）按 Q10 仍需 **3 篇真实 PDF 实测**后冻结。

### 4.2 公式：崩溃已修，但「公式块」未验证
本机 `transformers 4.57.6` 与 magic-pdf 自带 `unimernet` 有两处不兼容（`cache_position` 未接受、`past_key_values` 为 `EncoderDecoderCache` 而非旧元组）。补丁后 MFR 正常执行，但**合成的公式样例被版面模型判为 heading**，因此没有产出 `equation` 块——「公式识别质量」仍属未验证，需要真实带公式的 PDF。

### 4.3 尚未接入服务层
`document_ir_builder` 目前由演示脚本调用，`build-document-ir` CLI / 幂等 / dry-run（T007/T010）属 P4，未实现。

---

## 5. 复现

```bash
# 1) 生成样例 PDF（必须用真实 CJK 字体，否则文本层会被逐字符拆开）
/home/szf/anaconda3/bin/python make_demo_pdf.py /tmp/demo/rich_demo2.pdf

# 2) magic-pdf 配置：table 开、formula 关（本机 formula 需走补丁，见 4.2）
export MINERU_TOOLS_CONFIG_JSON=<本目录>/ir_source/mineru.config.json

# 3) service 链路（wrapper 指向 patched 入口）
#    MineruPdfParserService(config, magic_pdf_cmd="<python> <本目录>/ir_source/magic_pdf_patched.py")
#    → parse_many(sha256=...)

# 4) 构建 IR
#    build_from_mineru_content_list(content_list=..., parsed_text=...,
#        sha256=..., content_uid=..., manifest_assets=manifest["asset_files"])
#    → ir.validate(manifest_assets=..., parsed_text=...)
```

**字体陷阱（已踩过）**：首版用 PyMuPDF 内置 `china-s`，解析结果是 `2 0 2 6 Q 3 …`
逐字符拆分——那是**造 PDF 的字体嵌入方式**问题，不是解析器缺陷；换 `uming.ttc` 后干净。
评测文本抽取质量前，必须先确认样例 PDF 的文本层质量。
