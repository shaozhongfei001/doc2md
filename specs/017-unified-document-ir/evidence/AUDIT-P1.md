# 017 Document IR — P1 基座字段契约审计（AUDIT-P1）

> **角色**：aud17-contract（只读契约审计者，非实现者、非 QA 判定者）
> **日期**：2026-10-17
> **性质**：把「规格」转成**可逐项判定的字段契约清单**，供 017 后续各阶段当尺子。**不发明规则**：本文所有取值、nullability、约束均标注出处（`文件:行号` 或章节号）；文档未写清的一律进 §10 `SPEC_GAP`，不自行补齐。
> **权威顺序**（冲突时以此为准）：`docs/architecture/DOCUMENT_IR_DESIGN_V1.md`（schema 权威，`spec.md` L8 自证） → `spec.md`/`plan.md`（含 §8 决策表） → `acceptance.md` / `test_cases.md`。
> **写入边界**：本文为本次**唯一**写入文件；未改 `backend/**`、`config/**`、`sql/**`、`specs/**` 其它文件；未 `git add` / `git commit`。

---

## 0. 输入基线（现拼 md5，2026-10-17）

| 文件 | md5 | 行数 |
|---|---|---|
| `docs/architecture/DOCUMENT_IR_DESIGN_V1.md` | `dc278a2aae3ef7dff45b242c7165d096` | 351 |
| `specs/017-unified-document-ir/spec.md` | `0416ea9213b3e7456ca1d6d4b05772c2` | 78 |
| `specs/017-unified-document-ir/plan.md` | `857083cef37774a01c2de3eda88b4c09` | 231 |
| `specs/017-unified-document-ir/tasks.md` | `1603d2dd7d98dd6ffc83576a34574f1b` | 254 |
| `specs/017-unified-document-ir/acceptance.md` | `293b651daf9b6bd5844a19b434dadadf` | 120 |
| `specs/017-unified-document-ir/test_cases.md` | `69e82420f1e56ea87f213752906e7c2f` | 120 |
| `specs/017-unified-document-ir/evidence/QA_PREP_017.md` | `840a7c82a41af6152a2d0c0a57a777be` | 325 |
| `backend/app/services/mineru_pdf_parser.py`（只读） | `a82a3653ec7998d30eabfd16e99b0a9e` | 980 |
| `backend/app/services/evidence_chain.py`（只读） | `b4815f76c32e900251ff3e7b41272c09` | — |
| `backend/app/core/parsed_paths.py`（只读） | `d8d94f75d0b0ffda1af4014a3d73e9f4` | 27 |
| `backend/app/models/evidence.py`（只读） | `646b8dc11d73393bc66c27fbaa1e9385` | 69 |

零迁移基线复核：`git status --porcelain sql/migrations/` → **输出为空**（与 QA_PREP G-07 一致）。

---

## 1. 顶层字段契约（`document_ir.json`，出处 design §3.1 L43-82）

| # | 键 | 必填 | 类型 | 可 null | 约束 / 取值 | 出处 |
|---|---|---|---|---|---|---|
| T01 | `schema_version` | 必填 | string | 否 | 常量 `"document-ir/1.0"` | design L45；spec L56；A017-01 |
| T02 | `document_uid` | **未定义** | string | 未定义 | 示例 `doc_...`；缺席是否失败无判据 | design L46 → **GAP-03** |
| T03 | `content_uid` | **未定义** | string | 未定义 | 示例 `cnt_...`；同上 | design L47 → **GAP-03** |
| T04 | `sha256` | **未定义** | string | 未定义 | 示例 `<64hex>`；与 `provenance`/manifest 的绑定关系未定义 | design L48 → **GAP-03** |
| T05 | `source_vault_path` | **未定义** | string | 未定义 | 示例为 hash 分片路径 | design L49 → **GAP-03** |
| T06 | `provenance` | **未定义** | object | 未定义 | 6 子键见 §1.1 | design L50-57 → **GAP-03** |
| T07 | `page_count` | **未定义** | int | 未定义 | 非分页文档（`markitdown_text_only`）取 `null`/`0` 未规定 | design L58 → **GAP-03** |
| T08 | `coordinate_system` | **必填** | object | 否 | 3 子键见 §1.2；`unit` 非空（A017-01） | design L59-63 |
| T09 | `blocks` | 必填 | array\<object\> | 否 | 元素契约见 §2 | design L64 |
| T10 | `tables` | 必填 | array\<object\> | 否 | 无表格文档 → 空数组 `[]`（A017-04 对照） | design L65 |
| T11 | `figures` | 必填 | array\<object\> | 否 | 元素契约见 §6 | design L66 |
| T12 | `reading_order` | 必填 | array\<string\> | 否 | 全局 `block_id` 序列；`blocks[i].reading_order` 是该序列中的索引；与 blocks 一一对应、为 `0..n-1` 置换 | design L67/L82；A017-02 |
| T13 | `stats` | 必填 | object | 否 | 5 键见 §7；**必须由实际 blocks 复算，不得硬编码** | design L68-74；plan L144 |

### 1.1 `provenance` 子键（design L50-57、L80、L275）

| # | 键 | 必填 | 类型 | 可 null | 约束 / 取值 | 出处 |
|---|---|---|---|---|---|---|
| P01 | `parser_name` | 未定义（建议必填） | string | 未定义 | 示例 `mineru`；与 manifest `parser_name` 一致 | design L51 |
| P02 | `parser_profile` | 未定义 | string | 未定义 | v2 路径 = `mineru_ir_v2`；旧 = `mineru_default_v1` | design L52/L75；plan L75；`mineru_pdf_parser.py:36` |
| P03 | `parser_adapter_version` | 未定义 | string | 未定义 | 示例 `017_mvp_v1`；幂等键之一（同 sha256 + 同版本 → 确定） | design L53/L35；A017-02 |
| P04 | `route_type` | 未定义 | string | 未定义 | 取值域来自 `RouteType`（如 `PDF_DIGITAL`） | design L54 |
| P05 | `ir_source` | 未定义 | string（封闭枚举） | 未定义 | ∈ `{mineru_content_list, mineru_middle_json, docling, vlm_fallback, markitdown_text_only}` | design L55/L80；spec L65-70 |
| P06 | `generated_at` | 未定义 | string（ISO8601 `Z`） | 未定义 | **唯一允许跨重跑差异的字段**（A017-02「仅时间戳可差异」） | design L56；A017-02 |

### 1.2 `coordinate_system` 子键（design L59-63、L81）

| # | 键 | 必填 | 类型 | 可 null | 约束 / 取值 | 出处 |
|---|---|---|---|---|---|---|
| K01 | `origin` | 必填（整体声明一次） | string | 否 | 示例 `top_left`；未给出封闭枚举 | design L60 |
| K02 | `unit` | **必填** | string | **否** | ∈ `{pdf_point, pixel}`，**非空**；实现期抽 3 篇实测后冻结（OQ-7=A）；禁止静默混用 | design L61/L81/L337；A017-01 |
| K03 | `page_ref` | 必填（整体声明一次） | string | 否 | 示例 `per_page` | design L62 |

---

## 2. `blocks[]` 字段契约（design §3.2 L84-117；A017-01）

| # | 键 | 必填 | 类型 | 可 null | 约束 | 出处 |
|---|---|---|---|---|---|---|
| B01 | `block_id` | **必填** | string | 否 | `b` + 4 位序号（`b0001`…），**按 reading_order 分配**；文档内唯一、跨重跑稳定；**禁随机 UUID** | design L110；plan Q1 L206；A017-02 |
| B02 | `type` | **必填** | string | 否 | 8 类封闭枚举（§3 E01）；枚举外值校验必须拒绝 | design L109；A017-01；T003 |
| B03 | `page_no` | **必填键** | int | **是** | 从 1 开始；`page_idx + 1`；非分页文档为 `null` | design L111/L255；plan L120 |
| B04 | `bbox` | **必填键** | object `{x0,y0,x1,y1}` | **是** | 内部 4 键均 float；存在时必须符合 K02 声明的单位；**缺则 `null` + `stats.blocks_without_bbox` 计数；绝不估算填充** | design L111/L34；plan Q2 L207、D-2 L141；A017-12 变异 2 |
| B05 | `reading_order` | **必填** | int | 否 | 值域 `0..len(blocks)-1`，无重号/无越界/无空洞 | design L82/L92；A017-02 |
| B06 | `confidence` | **必填键** | number | **是** | ∈ `[0,1]`；不可得置 `null`，**不得虚构**（VLM 路径一律 `null`） | design L112/L275；plan D-2 L143 |
| B07 | `content_ref` | **必填** | object | 未定义（`kind=none` 时对象仍须存在？未规定） | 契约见 §4；缺失内容不得静默降级 | design L94-100/L113-116；A017-03 |
| B08 | `attrs` | **未定义为必填** | object | 未定义 | 5 个已知子键见下表；未声明「必填/缺键即失败」 | design L101-105 → **GAP-03** |
| B09 | 类型专属键 | — | — | — | **block 内无按 type 分支的专属键**：table/figure 详情在 `tables[]`/`figures[]`（按 `block_id` 关联），caption 专属只有 `attrs.caption_of`，formula 的 LaTeX 在 `content_ref.text` | design L113-163 |

**`attrs` 子键**

| # | 键 | 适用 type | 类型 | 可 null | 约束 | 出处 |
|---|---|---|---|---|---|---|
| A01 | `heading_level` | heading | int | 是 | 来自 MinerU `text_level`（1 级→1，2 级同理）；缺省渲染用 `##` | design L102/L249/L175 |
| A02 | `caption_of` | caption | int→string | 是 | caption 块指向其说明对象的 `block_id`；**必须指向存在块**（T003） | design L103/L117/L254 |
| A03 | `language` | 任意 | string | 是 | 示例 `zh`；无生成规则 | design L104 |
| A04 | `raw_type` | paragraph（由未知 type 归并而来） | string | 否（该场景） | 记录 MinerU 原值；**不得原样透传为 `type`、不得丢弃该块** | design L109/L259；A017-01 |
| A05 | `vlm_model` | 任意（VLM 路径） | string | 是 | VLM 兜底时增记 | design L275 |

---

## 3. block `type` 封闭枚举与未知归并规则

| # | 规则 | 内容 | 出处 |
|---|---|---|---|
| E01 | 封闭枚举 | `{heading, paragraph, table, figure, formula, caption, header, footer}`（**8 类，无扩展位**） | design L109；spec L57；T003 L108 |
| E02 | 未知 type 归并 | 未知 `type` → `type="paragraph"` + `attrs.raw_type=<原值>`；**禁止透传为块类型、禁止丢弃该块**（块数守恒） | design L109/L259；A017-01 否定断言；TC017-03 |
| E03 | `content_ref.kind` 枚举 | `{text_span, inline_text, table_ref, figure_ref, asset, none}`（**6 值**） | design L113 |
| E04 | `provenance.ir_source` 枚举 | `{mineru_content_list, mineru_middle_json, docling, vlm_fallback, markitdown_text_only}`（**5 值**） | design L80；spec L65-70 |
| E05 | `coordinate_system.unit` 枚举 | `{pdf_point, pixel}`（**2 值**） | design L81；A017-01 |
| E06 | `block_id` 形式 | `b` + 4 位序号；`>9999` 块的进位形式**未定义**（备注，不影响现有验收） | design L110 |

**MinerU item → block.type 判定（design §6.1 L247-257，实现按行落实）**

| content_list item | 判定 | block.type | 关键填充 |
|---|---|---|---|
| `type=text` | `text_level=1/2…` | heading | `attrs.heading_level`；`content_ref` = `text_span`（对齐 .md offset）或 `inline_text` |
| `type=text` | 无 `text_level` | paragraph | 同上 |
| `type=image` | — | figure | `figures[]`：`asset_path`←`img_path`（重写）、`caption`←`img_caption` 拼接、`bbox`←`img_bbox` |
| `type=table` | — | table | `tables[]`：`html`←`table_body`、`cells` 由 HTML 解析、`caption`←`table_caption`、`bbox`←`table_bbox` |
| `type=equation` | — | formula | `content_ref=inline_text`（text 为 LaTeX）、`bbox`←item bbox |
| caption 独立项 | `img_caption`/`table_caption` | caption | `attrs.caption_of`←目标 `block_id` |
| 页眉页脚 | middle.json `para_blocks` 的 page_header/page_footer 区 | header / footer | V1 可选；MinerU 版本差异大，允许缺失 |
| 未知 `type` | — | paragraph | `attrs.raw_type` |

---

## 4. `content_ref` 契约（design L94-100、L113-116；A017-03）

| # | 子键 | 适用 kind | 类型 | 可 null | 约束 | 出处 |
|---|---|---|---|---|---|---|
| R01 | `kind` | 全部 | string | 否 | 6 值枚举（E03） | design L113 |
| R02 | `text` | `inline_text`（`text_span` 示例亦出现） | string | 未定义 | `inline_text`：内容仅存在于 IR（VLM/OCR），直接承载正文 | design L96/L115 → **GAP-02** |
| R03 | `source` | `text_span` | string | 未定义 | 示例 `"parsed_text.md"`；**是否强制/取值域未定义**（仅出现在示例，未入字段约定） | design L97 → **GAP-02** |
| R04 | `start_offset` | `text_span` | int | 否（该 kind） | 半开区间左端，指向 `parsed_text.md`；与 010 chunk offset 体系对齐 | design L98/L114 → **GAP-01（命名冲突）** |
| R05 | `end_offset` | `text_span` | int | 否（该 kind） | 半开区间右端（**不含**）；`end_offset > len(parsed_text)` **必须被校验器拒绝** | design L99/L114；A017-03 否定断言 |
| R06 | 退化形式 | `table_ref`/`figure_ref`/`asset`/`none` | — | — | 语义已定（指向 `tables[].table_id` / `figures[].figure_id`），但**字段名与「显式标记」方式未定义**（A017-03「退化为 block 引用须显式标记」不可判定） | design L113-116；A017-03 → **GAP-02** |

---

## 5. `tables[]` 与 `cells[]` 契约（design §3.3 L119-141；A017-04）

| # | 键/规则 | 必填 | 类型 | 可 null | 约束 | 出处 |
|---|---|---|---|---|---|---|
| C01 | `table_id` | 是 | string | 否 | 示例 `t001`；文档内唯一 | design L123 |
| C02 | `block_id` | 是 | string | 否 | 回引 `blocks[]` 中 `type=table` 的块；必须存在 | design L124 |
| C03 | `page_no` | 是 | int | 是 | 同 B03 语义 | design L125 |
| C04 | `bbox` | 是 | object | 是 | 同 B04 语义；`table_bbox` | design L126/L252 |
| C05 | `html` | 是 | string | 未定义 | 保留 MinerU `table_body` 原文（含 rowspan/colspan），作为高保真渲染源；解析失败时原样保留 | design L127/L140；plan L196 |
| C06 | `n_rows` | 是 | int | 否 | **语义未定义**（逻辑行数 or 展开矩阵最大 row+1） | design L128 → **GAP-05** |
| C07 | `n_cols` | 是 | int | 否 | 同上 | design L129 → **GAP-05** |
| C08 | `cells` | 是 | array\<object\> | 否 | 畸形 table_body → `cells=[]` 且 `html` 原样保留、不中断文档、不伪造 cells | design L130；plan L196；A017-04 否定断言 |
| C09 | `cells[].row` / `.col` | 是 | int | 否 | **展开矩阵坐标**：逻辑单元左上角锚点 | design L131/L139 |
| C10 | `cells[].rowspan` / `.colspan` | 是 | int | 否 | ≥1；与 row/col 共同决定覆盖范围 | design L131 |
| C11 | `cells[].is_header` | 是 | bool | 否 | 表头标记 | design L131 |
| C12 | `cells[].text` | 是 | string | 否 | 单元格文本；是 `table_cell` 证据的 `quote_text` 来源 | design L131/L206 |
| C13 | `cells[].bbox` | **未定义** | object | 示例为 `null` | 示例存在但字段约定未提；必填/可空/取值均未定义 | design L131 vs L139-141 → **GAP-05** |
| C14 | `caption_block_id` | 是 | string | 是 | 必须指向存在的 caption 块；`table_caption` 关联生效 | design L134/L252；A017-04 |
| C15 | `confidence`（表级） | 是 | number | 是 | 表级置信度；单元格级 V1 **不建模**（OQ-3=A） | design L135/L141/L333 |
| C16 | 虚格规则 | 规则 | — | — | 被 `rowspan/colspan` 覆盖的虚格**不出现在 cells 中** | design L139；A017-04 |
| C17 | 格级 confidence | 规则 | — | — | **任何 cell 不得含 `confidence` 键**（OQ-3=A 不建模）；现 acceptance/TC 写成「表级为 null 时」的条件式 → 判据被削弱 | A017-04 L33；TC017-13 → **GAP-06** |
| C18 | 矩阵合法性 | 规则 | — | — | cells 坐标**无越界**（`row+rowspan<=n_rows`、`col+colspan<=n_cols`）、**无重叠**；违反 → 校验器 fail-closed、候选 FAILED | A017-04；T003；TC017-16 |
| C19 | `html ↔ cells` 双向可重建 | 规则 | — | — | cells → 重建 HTML 与原 `html` **语义等价**（行列与合并关系一致） | A017-04；TC017-12 |

---

## 6. `figures[]` 契约（design §3.4 L143-163；A017-05）

| # | 键/规则 | 必填 | 类型 | 可 null | 约束 | 出处 |
|---|---|---|---|---|---|---|
| F01 | `figure_id` | 是 | string | 否 | 示例 `f002`；文档内唯一 | design L147 |
| F02 | `block_id` | 是 | string | 否 | 回引 `type=figure` 的块；必须存在 | design L148 |
| F03 | `page_no` | 是 | int | 是 | 同 B03 | design L149 |
| F04 | `bbox` | 是 | object | 是 | 来自 `img_bbox`；同 B04 | design L150/L251 |
| F05 | `asset_path` | 是 | string | 未定义 | **相对 `parsed_dir`**，指向 `assets/images/…`；文件必须实际存在；**不得残留 staging 绝对路径** | design L151/L161；A017-05；plan L113 |
| F06 | `asset_sha256` | 是 | string（64hex） | 未定义 | 与 `parse_manifest.json.asset_files[].sha256` **对齐校验**；不一致 → 候选 `FAILED`、IR **不落盘** | design L152/L161；plan Q3 L208；A017-05 |
| F07 | `ocr_text` | 是 | string | 是 | MinerU V1 不提供 → `null`；VLM 可填充 | design L153/L163 |
| F08 | `caption` | 是 | string | 是 | ← `img_caption` 拼接；`null` 时渲染用 `figure_id` | design L154/L251；plan D-2 L142 |
| F09 | `caption_block_id` | 是 | string | 是 | 指向存在的 caption 块 | design L155 |
| F10 | `image_quality_score` | 是 | number | 是 | ∈ `[0,1]`，adapter 启发式估算；阈值 `image_quality_threshold`（默认 0.5，`config/document_ir.yaml`） | design L156/L162；plan L47 |
| F11 | `confidence` | 是 | number | 是 | 同 B06 | design L157 |
| F12 | `degraded` 标记 | 规则 | — | — | design 要求低质量图渲染时「标 `degraded: true`」，但 **`degraded` 未出现在 figures[] schema 中，落点（IR 字段 or 渲染产物）未定义** | design L162；TC017-23 → **GAP-11** |
| F13 | `img_path` 重写 | 规则 | — | — | 重写为 `assets/images/` 下**相对路径**；目标形式（平坦化 basename / 保留子目录 / 重名冲突）未定义 | design L251；plan L113；A017-05 → **GAP-08** |
| F14 | manifest 对齐基准 | 规则 | — | — | 现 `asset_files[].relative_path` 基准 = `assets_dir`（实测 `mineru_pdf_parser.py:816-822`，`assets_dir = parsed_dir/"assets"` `:228-229`），而 IR `asset_path` 基准 = `parsed_dir` → **转换与匹配键未定义** | A017-05 → **GAP-07** |

---

## 7. `stats` 契约（design L68-74；plan D-2 L144；A017-12）

| # | 键 | 必填 | 类型 | 含义 | 复算口径（A017-12 变异 1 的判定基础） | 出处 |
|---|---|---|---|---|---|---|
| S01 | `block_count` | 是 | int | 块总数 | `= len(blocks)` | design L69 |
| S02 | `table_count` | 是 | int | 表数 | **`len(tables)` 还是 `type=table` 的块数 → 未定义** | design L70 → **GAP-09** |
| S03 | `figure_count` | 是 | int | 图数 | 同上 | design L71 → **GAP-09** |
| S04 | `blocks_without_bbox` | 是 | int | 缺 bbox 块数 | `= count(b.bbox is None)`；缺 bbox 不得失败 | design L72；plan Q2 |
| S05 | `blocks_low_confidence` | 是 | int | 低置信块数 | **阈值未定义**（M3 定 `config/document_ir.yaml: low_confidence_threshold`，但未落入 `plan.md §8`） | design L73 → **GAP-09/GAP-12** |
| S06 | 复算硬约束 | 规则 | — | — | `stats.*` **必须由实际 blocks 重算**，硬编码为常量必须被断言捕获（FAIL） | plan D-2 第 4 条 L144；A017-12 变异 1；TC017-48 |

---

## 8. 决策表映射：Q1~Q10 → 字段 / 验收（plan §8 L204-215）

| # | 决策（plan §8） | 落地判据（影响哪个字段 / 哪条 acceptance） |
|---|---|---|
| Q1 | `block_id`=`b`+序号按 reading_order，禁随机 | B01 / E06；A017-02（TC017-05/07） |
| Q2 | 缺 bbox → `null` + stats 计数，禁伪造 | B04 + S04；A017-12 变异 2（TC017-49） |
| Q3 | asset sha256 不对齐 → fail-closed FAILED | F06 + A017-05（TC017-19：IR 零写入） |
| Q4 | header/footer 剔除正文流 + 文末注释 | E01（块仍保留）+ design §4.1 L177；A017-12（TC017-52） |
| Q5 | v1 产物 `NO_IR_SOURCE` 跳过，证据链走旧路径 | 报告状态键 `NO_IR_SOURCE`（plan L193）；A017-08/09（TC017-31/36） |
| Q6 | `field_value` S2 零数据行，仅冻结格式 | 无 IR 新字段；A017-10（TC017-42/43） |
| Q7 | VLM 默认禁用；mock 文本不过校验不落盘 | P05=`vlm_fallback` + A05 + B06=`null`；A017-07（TC017-26/28/29） |
| Q8 | 零迁移；必要项 → STOP 报 Owner | `metadata_json.block_id`（无新列）；A017-11（TC017-47） |
| Q9 | `parsed_text.md` 不动，渲染落 `rendered.md` | manifest `rendered_md_path`；A017-06（TC017-24/25） |
| Q10 | bbox 单位抽 3 篇实测后冻结 | K02；A017-01 + P6 E2E 实测记录 |

> **GAP-12**：QA_PREP 已由 TL 裁定 `Q11`（`--force` 清理三产物）/`Q12`（校验器只读）/`Q13`（dry-run 4 态），但 `plan.md §8` **现表仍只有 Q1~Q10**（plan L204-215），裁定未落表 → 三条判据缺权威出处，见 §10。

---

## 9. 验收映射：A017-01~12 → 模块与字段（供后续阶段定位）

| 验收 | 依赖模块（plan §4.1/§4.2） | 依赖契约条目 | 关键 TC |
|---|---|---|---|
| A017-01 | `document_ir_schema.py`、`document_ir_builder.py` | T01/T08、K02、B02/B07、B08、E01/E02 | TC01-04 |
| A017-02 | `builder`（block_id 分配）、`schema`（置换校验） | B01、B05、E06、P03、P06 | TC05-07、TC50 |
| A017-03 | `builder`（span 对齐）、`schema`（越界拒绝，只读 parsed_dir 见 Q12） | R01-R06（**注意 GAP-01/02**） | TC08-10 |
| A017-04 | `builder`（HTML→cells）、`schema`（矩阵校验） | C01-C19（**注意 GAP-05/06**） | TC11-16 |
| A017-05 | `builder` + `document_ir_service.py`（fail-closed） | F05/F06/F13/F14（**注意 GAP-07/08**） | TC17-19 |
| A017-06 | `ir_renderer.py`（纯函数） | design §4.1 L175-183；F08/F10/F12；`config` `render.*`（plan L49-50） | TC20-25 |
| A017-07 | `ir_vlm_fallback.py` | P05、B06、A05；design §6.3 L275-276 | TC26-29 |
| A017-08 | `mineru_pdf_parser.py`（T009，S1 合并后） | manifest `ir_source_files` + `parser_profile`（P02）；`NO_IR_SOURCE`（Q5） | TC30-33 |
| A017-09 | `evidence_chain.py` 派发点 + `parsed_paths.py` | 旧三件套零改动；`_chunk_from_ir()` 不触发（Q5） | TC34-37 |
| A017-10 | `evidence_chain.py` + `models/evidence.py`（只读，不改） | `metadata.block_id`/`cell`/`figure_id`/`asset_path`；`evidence_uid` v2（design L210） | TC38-43 |
| A017-11 | `document_ir_service.py`、`cli/main.py`、`parsed_paths.py` | artifact_type `document_ir`/`rendered_md`；幂等键 P03；`--force` 清理（Q11） | TC44-47 |
| A017-12 | 全部 + `config/document_ir.yaml` | S01-S06、B04、F10；`low_confidence_threshold`（M3） | TC48-52 |

---

## 10. `SPEC_GAP` 清单（13 项，逐项可判定）

> 判定口径：**缺字段名/缺约束/两文档冲突** → 该验收项在实现前**无法客观判定**，不得由 Dev 自行补齐（违反「不发明规则」）。

| # | 缺口（缺什么） | 出处 / 冲突面 | 影响的验收 | 建议裁定方 |
|---|---|---|---|---|
| **GAP-01** | `content_ref` span 字段名**冲突**：设计稿 = `start_offset/end_offset`（L98-99/L114），而 `acceptance.md` L26/L28 与 `test_cases.md` L22-24 仍为 `offset_start/offset_end`；QA_PREP M1 已裁定取 `start_offset/end_offset` 但**收口未落地**（现拼 md5 仍为旧值 `293b651d…`/`69e82420…`） | design L98-99/L114 vs acceptance L26/L28、TC L22-24 | A017-03、TC017-08/09/10 | spec-lead（按 M1 执行 5 行/9 token 替换） |
| **GAP-02** | `content_ref` 的 `text_span` 之外的**字段名未定义**：`table_ref`/`figure_ref`/`asset`/`none` 无键名与「显式标记」方式；`source` 只出现在示例未入约定；`text_span` 是否允许同时带 `text` 未定 | design L94-100 vs L113-116；A017-03「须显式标记」 | A017-03（退化形式不可判定） | ir-architect（design §3.2 补键名表） |
| **GAP-03** | **顶层必填键集未定义**：A017-01 只断言 `schema_version`/`coordinate_system`/`blocks` + block 7 键；`document_uid`/`content_uid`/`sha256`/`source_vault_path`/`provenance`/`page_count`/`reading_order`/`tables`/`figures`/`stats`/`attrs` 的缺席是否失败无判据；`page_count` 在 `markitdown_text_only` 路径取值（`null`/`0`）未定 | design §3.1 仅示例；A017-01 | A017-01、A017-06（MarkItDown 降级） | ir-architect + TL（`plan.md §8` 增 required 清单） |
| **GAP-04** | **`bbox` 数值合法性约束未定义**：无 `x1>x0`、`y1>=y0`、非负、与 `coordinate_system.unit` 匹配的数值范围要求 → 校验器无法拒绝畸形 bbox | design L111/L34（只说「须符合统一坐标规范」）；T003 只列 cells 越界 | A017-01（schema 契约） | ir-architect（design §3.2 补约束） |
| **GAP-05** | **`cells[]` 元素键集与 `n_rows/n_cols` 语义未定义**：示例含 `cells[].bbox`（L131）但字段约定（L139-141）只提 row/col/rowspan/colspan/is_header/text；`n_rows/n_cols` 是逻辑行数还是展开矩阵最大索引+1 未定 | design L128-131 vs L139-141 | A017-04（越界判定、TC017-11/16） | ir-architect |
| **GAP-06** | **格级 confidence 判据与 OQ-3=A 不一致**：接受标准写成条件式「表级 confidence 为 null 时不产格级键」（acceptance L33 / TC017-13），而裁定是「不建模」→ 无法捕获「表级非 null 时产格级键」的变异 | acceptance L33、TC017-13 vs design L141/L333 | A017-04 | spec-lead（改无条件否定断言） |
| **GAP-07** | **figures 与 manifest 的对齐键/基准未定义**：`asset_files[].relative_path` 基准 = `assets_dir`（实测 `mineru_pdf_parser.py:816-822` + `:228-229`），IR `asset_path` 基准 = `parsed_dir`；匹配键（sha256 / basename / relative_path 转换）未规定 | A017-05「逐项对齐」；design L161 | A017-05、TC017-17 | ir-architect + Dev 提案后 TL 裁定 |
| **GAP-08** | **`img_path` 重写目标形式未定义**：只写「`assets/images/` 下相对路径」；实际 `_collect_assets` 是**平坦化 basename**（`:806-813`），跨页同名文件冲突处理未定义 | design L251；plan L113；代码 `:806-813` | A017-05、TC017-18 | ir-architect |
| **GAP-09** | **`stats` 计数口径未定义**：`table_count`/`figure_count` 是 `len(tables)/len(figures)` 还是 `type=table/figure` 的块数；`blocks_low_confidence` 的**阈值键与默认值**设计稿无（M3 定 `low_confidence_threshold`，未入 `plan.md §8`） | design L70-73 | A017-12 变异 1（无判定基线）、TC017-48 | TL（`plan.md §8` 补口径）+ spec-lead |
| **GAP-10** | **asset 缺失时渲染形式冲突**：design L179 = 占位 `[图像 {figure_id}: …]`；acceptance L47 = `![figure_id](...)` 形式 → 两种形态互斥，测试无法同时满足 | design L179 vs acceptance L47 | A017-06、TC017-22 | TL 裁定（择一，另一份改） |
| **GAP-11** | **`degraded: true` 落点未定义**：design L162 要求低质量图「标 `degraded: true`」，但 `figures[]` schema（L145-158）无该键，也未说明是否只出现在渲染产物 | design L162 vs L145-158；TC017-23 | A017-06 | ir-architect |
| **GAP-12** | **`evidence_uid` v2 的 `locator` 序列化格式未定义**：公式 `f"evidence\|v2\|{chunk_uid}\|{evidence_type}\|{block_id}\|{locator}"`（L210）中 `locator` 对 `table_cell` 是 `cell={row,col,rowspan,colspan}`，字典顺序/分隔符未规定 → uid 不确定，破坏幂等 | design L210；A017-10/11 | A017-10、A017-11（幂等） | ir-architect（定义确定性序列化） |
| **GAP-13** | **Q11/Q12/Q13 裁定未落入 `plan.md §8`**：现表仅 Q1~Q10（plan L204-215），但 TL 已裁定 `--force` 清理三产物、校验器只读、dry-run 4 态（QA_PREP §六 M7/§2.3 Q-A/Q-B） | QA_PREP vs plan L204-215 | A017-11（TC017-45/46）、T003、T009 | spec-lead 写入 `plan.md §8` |

**备注（不影响验收，仅记录）**：① `block_id` 超过 `b9999` 的进位形式未定义（design L110）；② design L82「reading_order 跨页单调递增」与「全局序列索引」表述并存，未定义剔除 header/footer 后是否允许跨页交错——两者均不构成现有 12 条验收的阻塞点。

---

## 11. 只读事实基线（代码锚点，**按符号锚定**，行号仅当次实测）

| 事项 | 实测结论 | 锚点 |
|---|---|---|
| `parsed_dir` 产物路径键 | 现有 **4 键**：`parsed_dir`/`parsed_text`/`parsed_metadata`/`parse_manifest`；017 只做 additive 3 键 | `parsed_paths.py:7-11`、`:20-26` |
| `assets_dir` | `= parsed_dir / "assets"` | `mineru_pdf_parser.py:228-229` |
| `asset_files[]` 键与基准 | 3 键 `relative_path`（**相对 `assets_dir`**，形如 `images/xxx.jpg`）/`source_name`/`sha256` | `_collect_assets` `:816-822` |
| 资产收集行为 | **平坦化**复制到 `assets/images/<source.name>`（`images_dir = assets_dir/"images"`）→ 跨页同名风险 | `:806-813` |
| `parser_profile` 现值 | `PARSER_PROFILE = "mineru_default_v1"`（017 升 `mineru_ir_v2`） | `:36` |
| manifest 现有键 | 无 `document_ir_path`/`rendered_md_path`/`ir_source_files`；`assets_dir`/`asset_files` 视情况写 | `:654-684` |
| `--force` 清理集合 | 仅 `parsed_text`/`parsed_metadata`/`parse_manifest` + `assets_dir`；**不含**三新产物（Q11 的缺口所在） | `_clear_parsed_outputs` `:841-851`；唯一调用点 `if force:` `:529-530` |
| **证据链 mineru 页级路径的真实行为** | `_chunk_mineru` 读 `metadata.get("pages")`，但 MinerU `metadata_payload`（`:629-639`）**不写 `pages`** → 实际**必然回退** `_chunk_markitdown`（按 `#` heading 切 section）。A017-09「IR 缺位走旧路径」的基线是 **markitdown section 切分**，不是页级 | `evidence_chain.py:516-569`；`mineru_pdf_parser.py:629-639` |
| 现有 chunk/evidence 常量 | `CHUNK_TYPE_TEXT`/`CHUNK_LEVEL_SECTION`/`CHUNK_LEVEL_PAGE`/`EVIDENCE_TYPE_SECTION_QUOTE`（**无 table/figure/block**） | `evidence_chain.py:34-37` |
| v1 uid 公式（零变化红线） | `chunk\|v1\|…`、`evidence\|v1\|{chunk_uid}\|{start}\|{end}\|{evidence_type}` | `:118-139` |
| `start_offset/end_offset` 实名 | chunk 侧实名即 `start_offset/end_offset`；evidence 侧另有 `source_char_start/source_char_end`（**两组合法并存，勿连带改**） | `models/evidence.py:27-28`、`:55-56`；`evidence_chain.py:586/590-591` |
| 零迁移基线 | `sql/migrations/` 仅既有文件；`git status --porcelain sql/migrations/` 空 | 本次实测 |
| 本仓真实 MinerU 产物 | **无**（`parsed/`、`raw_vault/`、`content_list.json`、`document_ir.json`、`parse_manifest.json` 全 0 命中）→ OQ-7 实测与 P6 E2E 需 Owner 供 ≥3 篇真实 PDF | QA_PREP §三 |

---

## 12. 交付口径

- **契约条目合计 76 条**：顶层 13（T01-T13）+ `provenance` 6（P01-P06）+ `coordinate_system` 3（K01-K03）+ block 9（B01-B09）+ `attrs` 5（A01-A05）+ 枚举/映射规则 6（E01-E06）+ `content_ref` 6（R01-R06）+ tables/cells 19（C01-C19）+ figures 14（F01-F14）+ stats 6（S01-S06）= **76**。
- **`SPEC_GAP` 13 项**（GAP-01~13），另 2 项非阻塞备注。
- **决策映射**：Q1~Q10 全覆盖（10/10）；**裁定未落表部分（Q11~Q13）计入 GAP-13**。
- **验收映射**：A017-01~12 全覆盖（12/12），每项已定位到模块 + 契约条目 + 关键 TC。
- **本文件不构成任何验收结论**：仅为字段契约尺子与缺口登记；017 实现仍受 S1 合并前置与 `SPEC_INDEX.md` 约束。

**AUDIT-P1 结束**
