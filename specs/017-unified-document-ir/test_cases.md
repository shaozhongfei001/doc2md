# Test Cases: 统一文档中间表示 Document IR（017）

> **Plan 对照**：`plan.md` §3 / §4 / §5 / §6.3 / §7
> **验收对照**：`acceptance.md` A017-01 ~ A017-12
> **设计对照**：`docs/architecture/DOCUMENT_IR_DESIGN_V1.md` §3（schema）/ §4（渲染）/ §5（证据）/ §6.1（映射）
> **测试文件**：`backend/tests/test_document_ir.py` / `test_ir_renderer.py` / `test_evidence_chain_ir.py`
> **反假绿原则**：**不得只测正例**。每条"必须产出 X"必须同时存在"不产出 X / 变异后必须失败"的对照断言；每个 fail-closed 分叉（A017-01/02/03/06/08/10）必须配**变异复验**用例（变异→FAIL、恢复→PASS）。

---

## 用例总表

| 编号 | 验收 | 场景 | 输入 | 预期 |
|------|------|------|------|------|
| **TC017-01** | A017-01 | 顶层契约 + 8 类枚举全落 | 全覆盖 `content_list.json`（8 类块齐备） | `schema_version="document-ir/1.0"`；`coordinate_system.unit ∈ {pdf_point,pixel}` 非空；`assert all(b["type"] in BLOCK_TYPES for b in blocks)` |
| **TC017-02** | A017-01 | 必填键完整性 | 含缺 bbox / 缺 caption 块 | 每块 7 个必填键全在；不可得者为显式 `null`，**不得缺键** |
| **TC017-03** | A017-01 | 未知 type 归并（否定） | `type="unknown_chart"` 项 | 归并为 `paragraph` 且 `attrs.raw_type=="unknown_chart"`；**否定**：不得原样透传、不得丢弃该块；块数守恒 |
| **TC017-04** | A017-01 | **变异**：枚举外 type 透传 | 变异 builder 令未知 type 直写 `type` | 校验器必须拒绝 → 测试 **FAIL**；恢复后 PASS |
| **TC017-05** | A017-02 | block_id 跨重跑稳定 | 同 `content_list.json` 连续构建两次 | 两次 `block_id` 集合与映射逐字节一致（`b0001`… 按 reading_order）；除时间戳外 IR 确定 |
| **TC017-06** | A017-02 | reading_order 一致性 | 同上产物 | `reading_order` 为 `0..len(blocks)-1` 置换：无重号 / 无越界 / 无空洞 |
| **TC017-07** | A017-02 | **变异**：随机 UUID 分配 | 变异 builder 用 `uuid4()` 生成 block_id | 稳定性断言 **FAIL**；渲染轨/证据链 block_id 引用失配被捕获；恢复后 PASS |
| **TC017-08** | A017-03 | span 回引正确 | 块文本 + `parsed_text.md` | `text[offset_start:offset_end]` 与块文本一致（一致性阈值断言为真） |
| **TC017-09** | A017-03 | 越界 offset 拒绝 | 构造 `offset_end > len(text)` 的 IR | 校验器拒绝；**否定**：不得静默通过 |
| **TC017-10** | A017-03 | **变异**：offset 偏移 1 位 | 变异 builder 令 `offset_start += 1` | 回引一致性断言 **FAIL**；恢复后 PASS |
| **TC017-11** | A017-04 | cells 矩阵合法 | rowspan/colspan 表 fixture | cells 坐标无越界；无重叠；虚格（被覆盖位）**不入 cells** |
| **TC017-12** | A017-04 | html ↔ cells 双向重建 | 同表 | cells → 重建 HTML 与原 `html` 语义等价（行列与合并关系一致） |
| **TC017-13** | A017-04 | caption 关联 + 格级置信度不建模 | 含 `table_caption` 表；表级 confidence=null | `caption_block_id` 指向存在 caption 块；表级 confidence=null 时**不产**格级 `confidence` 键（OQ-3=A） |
| **TC017-14** | A017-04 | 对照：无表格文档 | 纯文本 content_list | `tables[]` 为空数组，零 issue |
| **TC017-15** | A017-04 | HTML 解析失败降级（否定） | 畸形 `table_body` | 该表 `cells=[]` + `html` 原样保留 + `stats` 计数；**否定**：不得中断文档、不得伪造 cells |
| **TC017-16** | A017-04 | cells 重叠 fail-closed | 构造重叠 cell 坐标 | 校验器拒绝（fail-closed），候选 FAILED |
| **TC017-17** | A017-05 | asset 路径与 sha256 对齐 | figure fixture + manifest | `asset_path` 为 parsed_dir 相对路径（指向 `assets/images/`）；`asset_sha256` 与 `manifest.asset_files[].sha256` 逐项对齐；文件实际存在 |
| **TC017-18** | A017-05 | img_path 重写（否定） | MinerU `img_path` 为 staging 绝对路径 | 重写为 `assets/images/` 相对路径；**否定**：不得残留 staging 绝对路径/已清理路径 |
| **TC017-19** | A017-05 | **fail-closed 核心**：sha256 篡改 | 篡改 fixture 使 asset sha256 与 manifest 不一致 | 候选 `FAILED`；`document_ir.json` **不落盘**（半成品零写入）；`errors[]` 含 sha256 与原因；批处理继续 |
| **TC017-20** | A017-06 | rendered.md 确定性渲染 | 正常 IR，`table_format="html_prefer"` | 由 IR 纯函数渲染；首选 `table.html`；顺序严格按 reading_order |
| **TC017-21** | A017-06 | 降级路径 1：html 缺失 | `table.html` 置空但 cells 完整 | cells → MD pipe 降级渲染；表格内容不丢失 |
| **TC017-22** | A017-06 | 降级路径 2：asset 缺失/caption null | figure 无 asset / caption=null | 用 `figure_id` 占位，`![figure_id](...)` 形式可预测；**否定**：不产空链接 |
| **TC017-23** | A017-06 | 降级路径 3：低质量图 | `image_quality_score < image_quality_threshold` | 渲染轨降级为 caption-only 占位（不嵌图），标 `degraded: true` |
| **TC017-24** | A017-06 | **红线否定**：parsed_text.md 零改动 | 渲染前后 | `rendered.md` **不写** `parsed_text.md`；`parsed_text.md` 内容与 hash 执行前后**完全不变**（OQ-5=B） |
| **TC017-25** | A017-06 | **变异**：渲染器写 parsed_text.md | 变异 renderer 指向 `parsed_text.md` | 红线断言 **FAIL**（hash 变化被捕获）；恢复后 PASS |
| **TC017-26** | A017-07 | VLM 默认禁用零调用 | `vlm_fallback.enabled=false` | VLM 路径零调用（monkeypatch 客户端"被调用即 fail"仍通过） |
| **TC017-27** | A017-07 | 校验失败整页降级（否定） | VLM 返回不合 schema 的页产物 | 该页整页降级为**单个 figure 块**（asset 引用页图）；**否定**：不得把部分合规块混入 `blocks[]` |
| **TC017-28** | A017-07 | **变异 A**：跳过 schema 校验 | 注释掉校验直接落盘 | 脏块落盘断言命中 → 测试 **FAIL**；恢复后 PASS |
| **TC017-29** | A017-07 | **变异 B**：Mock 伪造文本 | `MockVlmClient` 返回伪造非空文本 | 管线**不得**写出含该文本的 IR/渲染产物；恢复后 PASS |
| **TC017-30** | A017-08 | v2 路径 ir_source 保留 | `mineru_ir_v2` 产物 | staging 清理前 `content_list.json`（必需）+ `middle.json`（可选）拷入 `parsed_dir/ir_source/`；manifest 增记 `ir_source_files`；`parsed_text.md` 与既有 v1 字段不变 |
| **TC017-31** | A017-08 | v1 产物兼容（对照/否定） | `mineru_default_v1` 产物（无 `ir_source/`） | 候选记 **`NO_IR_SOURCE` 跳过**；**否定**：不得 `FAILED`、不得报错中断；证据链走既有页级路径 |
| **TC017-32** | A017-08 | content_list 非法 vs 缺失严格区分 | JSON 语法非法的 `content_list.json` | 候选 `FAILED`，错误入报告，批处理继续（与"缺失→跳过"语义**严格区分**） |
| **TC017-33** | A017-08 | **变异**：v1 缺 ir_source 判 FAILED | 变异 service 对无 ir_source 候选置 FAILED | 兼容断言 **FAIL**；恢复后 PASS |
| **TC017-34** | A017-09 | 三件套契约冻结 | 017 全流程后 | `parsed_text.md` / `parsed_metadata.json` 内容 + hash 零改动；`parse_manifest.json` 仅 additive 字段（`document_ir_path`/`rendered_md_path`/`ir_source_files`） |
| **TC017-35** | A017-09 | 既有测试零回归 | `pytest test_mineru_pdf_parser.py test_markitdown_parser.py test_evidence_chain.py test_parser_router.py` | 既有用例**零修改**通过 |
| **TC017-36** | A017-09 | IR 缺位走旧路径（否定） | 无 `document_ir.json` 的候选 | `_chunk_from_ir()` **不触发**；完全走现有 `_chunk_markitdown`/`_chunk_mineru` 路径（行为逐字段一致） |
| **TC017-37** | A017-09 | 001–016 全量回归 | 全仓 pytest | 全量通过；017 不改 001–016 任何已完成契约 |
| **TC017-38** | A017-10 | chunk 按块聚合 | IR 存在 | paragraph 相邻合并至 token 上限（`chunk_type=text`,`chunk_level=block`）；`table`/`figure` 每块一 chunk（`chunk_type=table`/`figure`） |
| **TC017-39** | A017-10 | 证据类型三态 | IR 存在（text/table/figure 齐备） | 文本块产 `section_quote`（span 回引）；逐 cell 产 `table_cell`（locator = `block_id`+`cell={row,col,rowspan,colspan}`）；逐 figure 产 `image_region`（locator = `block_id`+`figure_id`+`asset_path`） |
| **TC017-40** | A017-10 | block_id 回引 + 零新增列 | 落库后 | `metadata_json.block_id` 回引 IR 块（OQ-1=A 不升独立列）；chunk↔block N:1 由 `metadata.block_ids[]` 承载；`kb_document_chunk`/`kb_evidence` **无新增列** |
| **TC017-41** | A017-10 | evidence_uid 兼容 | 同一批 v1 + v2 候选 | v2 公式**仅用于新类型**；v1 `section_quote` 公式与既有 uid 值**零变化**（幂等不破） |
| **TC017-42** | A017-10 | **否定**：field_value 零数据行（S2 边界核心） | 本 Loop 结束后 | `SELECT COUNT(*) WHERE evidence_type='field_value'` **为 0**（仅格式冻结） |
| **TC017-43** | A017-10 | **变异**：field_value 产数据 / 新增列 | 变异 evidence_chain 产一行 field_value；或加列 | 零数据/零新增列断言 **FAIL**；恢复后 PASS |
| **TC017-44** | A017-11 | 幂等重复构建 | 同 sha256 + 同 adapter 版本连续两次 `build-document-ir` | 第二次不重写产物（或内容一致）；artifact 登记幂等（无重复行） |
| **TC017-45** | A017-11 | `--force` 重建 | 同候选 + `--force` | 重建产物并更新登记 |
| **TC017-46** | A017-11 | dry-run 零写入 | `build-document-ir --dry-run` | `parsed/` 无新增文件；`kb_parsed_artifact` 无新行；raw_vault 不变；stdout 含 `would_build`/`would_skip` |
| **TC017-47** | A017-11 | **零迁移硬断言** | 全流程后 | `sql/migrations/**` 无任何新增文件（git status 校验）；`artifact_type` 新值走既有 VARCHAR 列 |
| **TC017-48** | A017-12 | **变异 1**：stats 硬编码 | 变异 builder 令 `stats.*` 为常量 | stats 一致性断言 **FAIL**（D-2 第 4 条）；恢复后 PASS |
| **TC017-49** | A017-12 | **变异 2**：缺 bbox 估算填充 | 变异 builder 用页面均分填 bbox | "缺 bbox 必须为 null" 断言 **FAIL**（D-2 第 1 条）；恢复后 PASS |
| **TC017-50** | A017-12 | **变异 3**：校验器放宽 | 变异校验器允许重复 `reading_order` | A017-02 一致性断言 **FAIL**（D-3 fail-closed）；恢复后 PASS |
| **TC017-51** | A017-12 | 中文路径全链路 | `中文路径/` 下 `content_list.json` 与产物 | 全链路 UTF-8 通过；报告 `ensure_ascii=False` 无乱码；`asset_path` 相对路径可定位 |
| **TC017-52** | A017-12 | middle.json 可选路径缺失 | 仅 `content_list.json`，无 `middle.json` | 不失败；header/footer 判别降级为类型判定（OQ-6 剔除正文流 + 文末注释仍生效） |

---

## fail-closed 分叉 → 变异复验映射（强制 1:1）

| fail-closed 分叉 | 正例用例 | 变异复验用例（变异→FAIL / 恢复→PASS） |
|------------------|----------|----------------------------------------|
| A017-01 封闭枚举（schema 契约） | TC017-01 / 02 / 03 | **TC017-04**（枚举外 type 透传 → 校验器必须拒绝） |
| A017-02 block_id 稳定 / reading_order 置换 | TC017-05 / 06 | **TC017-07**（随机 UUID → 稳定性 FAIL）；**TC017-50**（校验器放宽重复 reading_order → FAIL） |
| A017-03 span 回引正确性 | TC017-08 / 09 | **TC017-10**（offset 偏移 1 位 → 一致性 FAIL） |
| A017-06 渲染轨 parsed_text.md 零改动 | TC017-20 / 21 / 22 / 23 / 24 | **TC017-25**（渲染器改写 parsed_text.md → 红线 FAIL） |
| A017-08 v1 产物兼容跳过 | TC017-30 / 31 / 32 | **TC017-33**（v1 缺 ir_source 判 FAILED → 兼容断言 FAIL） |
| A017-10 证据扩展边界（field_value 零数据 / 零新增列） | TC017-38 ~ 42 | **TC017-43**（field_value 产数据行 / 新增列 → FAIL） |

**补充 fail-closed（非分叉但同属红线，亦配变异）**：A017-05 asset sha256 对齐（TC017-19，fail-closed 核心）；A017-07 VLM schema 校验（TC017-28 变异 A / TC017-29 变异 B）；A017-12 stats/bbox 不编造（TC017-48 / 49）。

---

## pytest 映射（建议）

| Test Case | 建议 test function（文件::函数） |
|-----------|----------------------------------|
| TC017-01 / 02 | `test_document_ir.py::test_top_level_contract_and_closed_enum` / `test_required_keys_present_with_explicit_null` |
| TC017-03 / 04 | `test_unknown_type_merged_to_paragraph` / `test_mutation_unknown_type_passthrough_rejected` |
| TC017-05 / 06 / 07 | `test_block_id_stable_across_reruns` / `test_reading_order_is_permutation` / `test_mutation_random_uuid_breaks_stability` |
| TC017-08 / 09 / 10 | `test_span_backref_matches_text` / `test_out_of_range_offset_rejected` / `test_mutation_offset_shift_fails` |
| TC017-11 / 12 / 13 | `test_cells_matrix_valid_rowspan_colspan` / `test_html_cells_roundtrip` / `test_table_caption_link_and_no_cell_confidence` |
| TC017-14 / 15 / 16 | `test_no_table_doc_empty_tables` / `test_malformed_table_body_degrades` / `test_overlapping_cells_rejected` |
| TC017-17 / 18 | `test_figure_asset_path_and_sha256_aligned` / `test_img_path_rewritten_relative` |
| TC017-19 | `test_asset_sha256_mismatch_failed_no_ir_written` |
| TC017-20 / 21 / 22 / 23 | `test_ir_renderer.py::test_render_deterministic_html_prefer` / `test_render_table_cells_pipe_fallback` / `test_render_figure_missing_asset_placeholder` / `test_render_low_quality_caption_only` |
| TC017-24 / 25 | `test_render_does_not_touch_parsed_text` / `test_mutation_renderer_writes_parsed_text_fails` |
| TC017-26 / 27 | `test_vlm_disabled_zero_calls` / `test_vlm_schema_fail_page_downgrade_single_figure` |
| TC017-28 / 29 | `test_mutation_skip_vlm_schema_check_fails` / `test_mutation_mock_vlm_fabricated_text_not_written` |
| TC017-30 / 31 / 32 / 33 | `test_mineru_v2_ir_source_retained` / `test_v1_no_ir_source_skipped_not_failed` / `test_invalid_content_list_failed_batch_continues` / `test_mutation_v1_marked_failed_breaks_compat` |
| TC017-34 / 35 / 36 / 37 | `test_three_artifacts_frozen` / 既有测试全量 / `test_missing_ir_falls_back_to_legacy_chunking` / 全仓回归 |
| TC017-38 / 39 / 40 / 41 | `test_evidence_chain_ir.py::test_chunk_from_ir_block_aggregation` / `test_evidence_types_section_table_cell_image_region` / `test_block_id_in_metadata_no_new_columns` / `test_evidence_uid_v2_only_for_new_types` |
| TC017-42 / 43 | `test_field_value_zero_rows` / `test_mutation_field_value_row_or_new_column_fails` |
| TC017-44 / 45 / 46 / 47 | `test_build_idempotent_and_registry_no_dup` / `test_force_rebuild_updates_registry` / `test_dry_run_zero_write` / `test_zero_migration_git_status` |
| TC017-48 / 49 / 50 | `test_mutation_hardcoded_stats_fails` / `test_mutation_estimated_bbox_fails` / `test_mutation_loose_validator_fails` |
| TC017-51 / 52 | `test_chinese_path_full_chain` / `test_missing_middle_json_degrades_header_footer` |

---

## 覆盖确认

- **A017-01 ~ A017-12 全覆盖**：12/12 验收项均有对应用例（见每行「验收」列）。
- **fail-closed 分叉 6/6 配变异复验**：A017-01/02/03/06/08/10 → TC017-04/07(+50)/10/25/33/43。
- **反假绿变异总数**：A017-07 两变异（TC017-28/29）+ A017-12 三变异（TC017-48/49/50）+ 分叉六变异（TC017-04/07/10/25/33/43）= **11 项变异必须全部被捕获**，缺一验收不通过。
- **不得只测正例**：每条"产出"用例均配对照（TC017-14 / 31 / 36）或否定断言（TC017-03 / 15 / 18 / 24 / 27 / 42）。

---

**Test Cases 结束** — Dev 按 TC017-01 → TC017-52 顺序实现，变异复验须证据留痕（变异前后 pytest 输出）。
