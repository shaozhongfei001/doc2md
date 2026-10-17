# 02 · 关键设计决策（20 条，含"为什么"与"改了要连带改什么"）

> 这些决策是**跨 17 个规格迭代收敛出来的**，其中多数有踩坑史。重构可以改实现，但改这些语义前请先读"代价"。

---

## A. 身份与存储

### D-01 `sha256` 即身份（`content_uid == sha256`）
- **决策**：内容身份 = 文件内容哈希；DB 的 `content_uid` 直接等于 sha256；所有产物路径由 sha 派生 `by_hash/ab/cd/<sha>/`。
- **理由**：内容寻址天然幂等、可去重、可跨机器复现；避免自增 ID 造成的"同内容多份"。
- **若改**：路径派生（`core/vault_paths`、`core/parsed_paths`）、幂等判定、`kb_parsed_artifact` 主键关联全部要改；且历史产物无法回溯定位。

### D-02 原件与 `raw_vault` 只读
- **决策**：任何服务构造时调用 `ensure_readonly(config)`；源目录与 vault 目录只读打开。
- **理由**：错误解析不能污染原件；审计要求"原始证据不可被程序修改"。
- **若改**：直接破坏可审计性，不建议。

### D-03 三件套契约只允许 additive 扩键
- **决策**：`parsed_text.md` / `parsed_metadata.json` / `parse_manifest.json` 是 005/007 起的**冻结契约**；017 只能加键（如 `ir_source_files`），不得改既有键语义。
- **理由**：下游（质量检查、证据链、检索、管理界面）都按这三个文件工作；改语义等于全链路回归。
- **若改**：需同步 008/009/010/011/012 全部消费方 + 全部测试基线。

---

## B. 路由与解析

### D-04 PDF 路由 fail-closed：只有"确认有文本层"才判 `PDF_DIGITAL`
- **决策**：`core/parser_routing.py` 有模块级不变式——仅 `TEXT_LAYER_PRESENT` 允许路由到 `PDF_DIGITAL`；其他状态（加密、损坏、缺依赖、扫描件）一律走 `PDF_SCANNED_OR_IMAGE`。
- **理由**：历史上 `PDF_SCANNED_OR_IMAGE` 是**死枚举**（永不产生），导致扫描件被送进 MinerU 文本模式，产出空正文却不报错。
- **若改**：`parser_routing` 的等值/子集断言会在 import 期直接炸（这是刻意的护栏），先看 `test_parser_router_v2.py`。

### D-05 路由规则版本化（v2 / `004_mvp_v1`）
- **决策**：`--routing-rules-version` 支持旧版复现；旧版**逐字节复现**历史行为。
- **理由**：升级不能悄悄改变已有结论；需要能对比"新旧规则给出的路由差异"。
- **若改**：`ROUTING_RULES_VERSION(S)` 常量 + 缓存键（缓存 key 含版本）+ 报告字段。

### D-06 嗅探缓存只缓存"可复用"状态
- **决策**：只有成功/可确定的状态入内容寻址缓存；`MISSING_DEPENDENCY`、`ERROR` **不缓存**。
- **理由**：依赖缺失是环境态，缓存会把一次环境故障永久固化。
- **若改**：删掉 `CACHEABLE_SNIFF_STATUSES` 白名单会造成"修好依赖后仍被判扫描件"的幽灵 bug。

### D-07 parser_profile 参与幂等键
- **决策**：`parser_adapter_version` + `parser_profile` 决定"是否需要重解析"；换版本即视为新产物。
- **理由**：同 sha 但解析器升级应能重建；不换版本就重跑会掩盖升级效果。

---

## C. 质量与"不编造"

### D-08 缺数据一律 `null` + 计数，**禁止估算**（全项目最重要的一条）
- **决策**：bbox 缺失 → `null` 且 `stats.blocks_without_bbox` 计数；置信度缺失 → `null`；caption 缺失 → `null`；页数未知 → `page_count=null`。
- **理由**：估算值会被下游当成真实测量（例如 bbox 用于定位证据），错误比缺失更难发现。
- **代价**：数据看起来"不完整"，这是刻意的。
- **若改**：等于把项目最核心的诚实性红线拆掉。

### D-09 asset 对齐 fail-closed（sha256 为键，不是路径）
- **决策**：IR 的 `figure.asset_sha256` 必须与 `parse_manifest.asset_files[].sha256` 对齐；对不上 → 整条候选 `FAILED`，**半成品 IR 不落盘**。
- **理由**：路径会被重命名/平坦化（`_collect_assets` 会 basename 化），路径匹配不可靠；sha 才是内容身份。
- **踩坑**：早期用路径做键，跨运行重跑（图片名变化）会静默错配。

### D-10 图片线：可选依赖必须优雅降级 + Mock 诚实性
- **决策**：无 `cv2`/`paddleocr` 时全部 skip 并**如实标注原因**（`deskew:opencv_missing`），不得伪装成"执行成功"；`MockVlmClient` 的产出**必须被拒绝落盘**。
- **理由**：`Mock` 结果一旦落盘会被当成真实识别结果；假绿风险高于崩溃。
- **若改**：`image_parser.py:919` 的 `if ocr_result.mock:` 是反假绿的判定核心，被变异测试盯着。

### D-11 样本不足要"整体跳过"，不是 ratio=0 通过
- **决策**：`total_chars < min_sample_chars(200)` → 直接跳过该检查（零 issue），而不是算出 0 比例"通过"。
- **理由**：50 字里有 25 个乱码，比例 50% 但不能据此判生产事故；"通过"与"无法判定"必须可区分。
- **若改**：会产出一批"看起来健康"的短文本报告。

### D-12 阈值必须真接线（可注入即被验证）
- **决策**：语义阈值来自 `config/quality_semantics.yaml`（可注入配置），并且验收要求"把阈值调高 → issue 消失"的变异能被捕获。
- **理由**：装饰性配置（读了不用）会让"调参"变成假动作。
- **若改**：任何把阈值改成硬编码常量的重构都会被打红（这是特性）。

### D-13 `--no-semantic` 必须逐字段复现 008（schema 1.0）
- **决策**：关闭语义检查时，报告与 016 之前**完全一致**：`schema_version=1.0`、`issue_counts` 仅 18 键、`scope` 里**不出现** `semantic` 键、且不读 `parsed_text.md`。
- **理由**：兼容性要求（老报告消费者）+ 性能（不该为了关掉的功能读文件）。
- **若改**：`scope.semantic` 曾因此从"输出 false"改成"省略键"。

### D-14 两套 offset 字段并存，不可互改
- **决策**：`kb_document_chunk.start_offset/end_offset`（chunk 侧）与 `kb_evidence.source_char_start/source_char_end`（证据侧）**同时存在**，语义对应但字段名不同。
- **理由**：历史契约；`evidence_uid` 公式依赖 chunk 侧字段。
- **若改**：`evidence_uid` 会变 → 全库证据 ID 失效（不可接受）。

---

## D. Document IR（017）

### D-15 IR 契约是"窄口径 + 封闭枚举"
- **顶层 13 键全必填**（值可 `null`）：`schema_version/document_uid/content_uid/sha256/source_vault_path/provenance/page_count/coordinate_system/blocks/tables/figures/reading_order/stats`
- **block 7 必填键**：`block_id/type/page_no/bbox/reading_order/confidence/content_ref`（缺键失败，`null` 合法）
- **8 类 block type 封闭**：`heading/paragraph/table/figure/formula/caption/header/footer`；未知 type → 归并 `paragraph` + `attrs.raw_type`
- **`content_ref.kind` 六值封闭**，且**每种 kind 键集严格**（多键/缺键都失败）：`text_span{text,source,start_offset,end_offset}` / `inline_text{text}` / `table_ref{table_id}` / `figure_ref{figure_id}` / `asset{asset_path}` / `none{reason}`
- **`cells[]` 固定 7 键**：`row/col/rowspan/colspan/is_header/text/bbox`，`bbox` 恒 `null`，**任何情况下不得含 `confidence`**
- **`stats` 5 键**必须等于由数据复算的值（禁硬编码）
- **理由**：封闭枚举让"未知结构"无法静默透传；键集严格让"加字段"变成显式决策而非意外。

### D-16 `block_id` 1-based + `reading_order` 是置换
- **决策**：`block_id = "b" + f"{reading_order+1:04d}"`（首块 `b0001`）；`reading_order` 必须是 `0..n-1` 的排列，且与顶层 `reading_order` 序列双向一致。
- **理由**：稳定 ID 是跨重跑对齐（证据、渲染、diff）的前提；随机 UUID 会让重跑全部失配。
- **若改**：证据链 `metadata.block_id` 回引、渲染锚点、IR 幂等全部受影响。

### D-17 零 schema 迁移
- **决策**：001~017 不新增表/列；新产物类型（`document_ir`/`rendered_md`）写既有 `kb_parsed_artifact.artifact_type`（VARCHAR）。
- **理由**：迁移不可逆且拖慢迭代；产物类型本质是枚举值不是结构。
- **若改**：需 Owner 批准 + 迁移脚本 + 回滚方案。

### D-18 渲染轨与原文分离（`rendered.md` ≠ `parsed_text.md`）
- **决策**：IR 渲染产物写 `rendered.md`；**绝不**回写/覆盖 `parsed_text.md`（其 hash 必须前后不变）。
- **理由**：`parsed_text.md` 是解析结果与证据 span 的基准，被重写会让所有 offset 失效。

### D-19 证据类型可扩展，但 `field_value` 零数据行
- **决策**：新增 `table_cell` / `image_region` 证据类型（v2 uid 公式仅用于新类型）；`field_value` **只冻结格式、不产生数据**。
- **理由**：v1 `section_quote` 的 `evidence_uid` 不能变（已有数据）；`field_value` 属下一阶段能力，提前填数据会污染语料。

### D-20 `ir_source` 保留是 additive（017 唯一允许碰 014 冻结件的改动）
- **决策**：`mineru_pdf_parser` 在成功落盘时，把 staging 里的 `*content_list.json` / `*middle.json` 归一化拷进 `parsed_dir/ir_source/`，manifest 增记 `ir_source_files`（name/relative_path/sha256）；旧 v1 产物无此文件 → 空数组。
- **理由**：IR 需要引擎结构化输出，但 staging 会被清理；不保留就只能重跑引擎。
- **若改**：必须保持"旧行为逐字不变"（v1 路径 → 空数组），否则 014 的冻结契约回归。
