# 07 · 技术债与真实风险（别把这些当 bug 重做一遍）

> 每条都标注了 **严重度 / 现状 / 建议**。带 `[已确认]` 的是本次会话实测出来的，不是推测。

---

## 一、功能缺口

### R-1 `bbox` 全为 `null`（中）
- **现状**：MinerU 1.3.12 的 `content_list.json` **不含 bbox**；IR 里所有块 `bbox=null`，`stats.blocks_without_bbox` 如实计数。
- **影响**：证据定位不能落到坐标；`coordinate_system.unit` 目前只是声明性字段。
- **建议**：从 `middle.json`（1,469 行的版面中间件）映射 page 级 bbox 到块；或换用带 bbox 的引擎输出。
- **注意**：**不要**用"页面均分"等估算填充（违反 D-08）。

### R-2 公式识别「不崩了但没验证」（中）
- **现状** [已确认]：`transformers 4.57.6` 与 magic-pdf 内置 `unimernet` 两处不兼容：
  ① `forward()` 不接受 `cache_position`；② 新版传 `EncoderDecoderCache`，而模型按旧元组取 `past_key_values[0][0].shape`。
  已在 `docs/demo/.../magic_pdf_patched.py` 用运行时补丁绕过（MFR 正常执行）。
- **未验证**：合成的"公式"被版面模型判成 `heading`，**没有产出 `equation` 块**。
- **建议**：拿真实带公式的 PDF 验证；若要长期支持，考虑升级 magic-pdf / 换引擎，而不是维持 monkeypatch。

### R-3 表格模型依赖形态（低-中）
- **现状**：`table-config.enable=false` 时表格整块降级为图片（`img_path` 无 `table_body`）。
- **建议**：把"表格是否结构化"作为显式能力开关写进元数据，避免下游把图片当表格。

### R-4 017 只到 P2（高，进行中）
- **已完成**：P1 schema + 校验器（1,616 行）、P2 builder（500 行）+ parser `ir_source` 保留。
- **未完成**：
  - P3 表格/图深度校验（`html↔cells` 双向、asset 实际存在性）
  - P4 `ir_renderer.py`（三条降级路径）+ `document_ir_service.py`（幂等/dry-run/登记）+ CLI `build-document-ir`
  - P5 `ir_vlm_fallback.py`（整页降级）+ `evidence_chain._chunk_from_ir()`（`table_cell`/`image_region`）
  - P6 独立 QA、P7 收口
- **建议**：外部重构若涉及 IR，请把 P3~P5 一并纳入设计，而不是只按现有三文件扩展。

### R-5 015 的 5 项 NOT_COVERED（中）
真实图像语料 0 张 → 真实 OCR 命中率、真实 PaddleOCR 推理链、真实 VLM、CLI 级单条 FAILED、GIF 首帧均未覆盖。

---

## 二、环境与工具

### R-6 `--help` 全红（低，但影响 CI 观感）
`click 8.4.1 × typer 0.15.1` 不兼容：`Parameter.make_metavar() missing 'ctx'`。修法：钉 `click<8.2` 或升 typer。

### R-7 共享测试库导致 flaky（中）[已确认]
两类瞬时失败（`test_chinese_path_integration` 的 `StaleDataError`、`test_mineru_pdf_parser` 6 个）都源于跨文件共用同一个 MySQL 库、互相删改行。
**这不是被测代码的缺陷**，但会让验收信号不可靠。建议：每 worker 独立 schema / 事务回滚 / 或用 `pytest-xdist` + 库后缀。

### R-8 shallow clone（低）
仓库是 shallow（`git rev-parse --is-shallow-repository` = true），久远历史不可用；跨分支操作前需 `git fetch --unshallow`。

### R-9 可选依赖矩阵不显式（中）
`cv2` / `paddleocr` / `magic-pdf` 均可缺席，行为靠运行时探测 + skip。建议改成显式能力矩阵（pytest marker + 启动期自检报告），避免"看起来全绿、实际一半没跑"。

---

## 三、设计层面（重构最该动的）

### R-10 `parse_quality_checker.py`（1,733 行）职责过载（高）
一个类里塞了 8 类结构检查 + 5 类语义检查 + 候选加载 + 报告装配。新增一类检查就要改多处（`ISSUE_CODES` / `RECOMMENDATION_BY_CODE` / 分桶 / 计数 / schema 版本）。
**建议**：抽成 `Rule` 协议 + 注册表（`code / severity / applies_to / evaluate(candidate) -> [Issue]`），报告装配只认注册表。

### R-11 `cli/main.py`（1,216 行，19 个命令）单文件（中）
每个命令内部直接 `load_config` + 造服务 + 打印结果，重复样板多，难测。
**建议**：命令薄壳化（只做参数解析 + 调 service），输出格式化抽 `cli/render.py`。

### R-12 四个 parser 服务接口不统一（中）
`mineru_pdf_parser` / `markitdown_parser` / `image_parser` 各自有 `plan_one/parse_one/parse_many` 但返回结构、跳过语义、错误码细节不完全一致。
**建议**：抽 `ParserAdapter` Protocol（`plan / parse / capability / profile`），统一 `ParseItem` / `ParseError`。

### R-13 配置无 schema 校验（中）
9 个 yaml 靠读取处各自兜底（缺字段回落默认、非法值抛 `ValueError`）。一旦拼错键名就静默用默认值。
**建议**：Pydantic（或 dataclass + 显式 `from_mapping`）统一校验，启动即 fail-fast。

### R-14 错误码分散（中）
`DECISION_*` 在多个模块重复定义，语义相近但数量在膨胀。
**建议**：收敛到单一 `errors.py` 枚举 + 分层（路由/解析/校验/IO）。

### R-15 IR 与三件套是"两套坐标系"（观察）
IR 的 `content_ref` 用 `parsed_text.md` 的字符偏移；证据链用 chunk 的 offset；DB 里是 `source_char_*`。三者靠约定对齐，无强校验。
**建议**：引入 `TextCoordinate` 值对象 + 单一映射函数，并在测试里做"三者一致性"断言。

---

## 四、请勿误判为 bug 的现象

| 现象 | 正确解读 |
|---|---|
| IR 里 `bbox` 全是 null | D-08 刻意如此（缺数据不编造），不是漏实现 |
| `stats.blocks_without_bbox == block_count` | 同上 |
| manifest 里 `assets_dir` 缺失但 `asset_files` 在 | 不同分支写入条件不同，见 `mineru_pdf_parser` 落盘段 |
| 解析结果 `EMPTY` 但 `status=SUCCESS` | 空正文是合法结果（另有 `EMPTY_BUT_NONZERO_PAGES` 检查去判定异常） |
| 图片解析出现 `UNSUPPORTED_WITH_HINT` | Mock VLM 被拒绝落盘是**设计**，不是失败 |
| `paddleocr` 相关用例 skip | 本机已装 paddleocr，缺依赖分支不可达 |
| 表格在 `parsed_text.md` 里是 HTML | 引擎原样输出 + 后续 IR 再结构化，不要"顺手美化" |
