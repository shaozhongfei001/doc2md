# Spec: 独立图片解析管线（015 Image Parse Pipeline）

> **项目**：个人历史项目文档知识库 `doc2md`（原 `pkb_sdd` 演进）
> **版本基线**：V1.1-SDD / Loop S1 规格门禁 G0
> **前置条件**：001–013、902 已完成；004 路由已交付（014 并行演进，不阻塞 015）
> **详细实现计划**：见同目录 `plan.md`
> **定位**：修复 `route_type=IMAGE` 的**死胡同**——当前 IMAGE 路由 `future_parser_hint=MINERU_FAMILY`，但 007 MinerU 适配器只接 PDF（`mineru_pdf_parser.py` `IN_SCOPE_ROUTE_TYPES` 仅含两个 PDF 类型），独立图片（png/jpg/tif/webp 等）无任何解析出口

---

## 1. 背景

### 1.1 缺陷现状（代码级证据）

- `parser_routing.py:111-117`：`.png/.jpg/.jpeg/.bmp/.gif/.tif/.tiff/.webp` → `RouteType.IMAGE`，hint `MINERU_FAMILY`。
- `mineru_pdf_parser.py:56-61`：`IN_SCOPE_ROUTE_TYPES = {PDF_DIGITAL, PDF_SCANNED_OR_IMAGE}`——**不含 IMAGE**。
- `config/parser_rules.yaml` 的 `image_to_mineru` 规则指向 `mineru`，但该 yaml 当前未被任何运行时代码消费。
- 结论：IMAGE 内容经 002 入 vault 后，路由标签与解析能力**断裂**，图片是死胡同。

### 1.2 设计取舍

MinerU 面向 PDF 文档版面；独立图片的合理链路是 **图像预处理 → 质量评分 → OCR / VLM**。015 新建独立图片管线，**不**强行把图片塞进 MinerU。

### 1.3 在链路中的位置

```text
001 scan → 002 copy-to-vault → 004 route（IMAGE）
  → 【015 parse-images：预处理 → 质量评分 → 引擎路由（PaddleOCR / VLM）】
  → parsed/ 三件套 + assets/（沿用 005/007 产物契约）
  → [可选 --register] 006 registry → 008+016 质检
```

---

## 2. 目标

1. 新建 `ImageParserService`：对 `route_type=IMAGE` 且 vault COPIED 的内容执行图片解析，产物沿用 parsed 三件套契约 + `assets/`。
2. **图像预处理**（可选、尽力而为）：deskew / 去噪 / 二值化；单步失败仅 WARNING 并跳过该步，永不中断。
3. **质量评分**：Laplacian 方差模糊度（cv2 可用时）或 Pillow 梯度近似（fallback）；评分失败 → 按低质处理（fail-safe 到 VLM 路径）。
4. **引擎路由**：清晰图 → PaddleOCR；低质图 → VLM 兜底；`--engine auto|paddleocr|vlm` 可覆盖。
5. **VLM 可插拔 Protocol + 默认 Mock**：`VlmClient` Protocol；MVP 仅交付 `MockVlmClient`。**Mock 永不产出伪造文本**——mock 结果被管线识别并转为 `UNSUPPORTED_WITH_HINT`（见 plan §8.2 D-2，反假绿核心决策）。
6. **依赖可选化**：PaddleOCR / OpenCV 未安装时优雅降级，最终无可 用引擎 → 条目标记 `UNSUPPORTED_WITH_HINT`（SKIPPED），**不写** parsed 产物，批处理继续。
7. 幂等（同 sha256 + parser_profile + adapter_version 已有 SUCCESS manifest → skip）；`--force` 显式重跑；dry-run 零副作用；单条失败不中断。

---

## 3. 范围（015 包含）

| 项 | 说明 |
|----|------|
| `backend/app/services/image_parser.py` | `ImageParserService`：候选筛选 → 预处理 → 评分 → 引擎路由 → 产物归一化 → 报告 → 可选 registry |
| `backend/app/adapters/image_preprocess.py` | deskew / denoise / binarize（cv2 可选 import guard） |
| `backend/app/adapters/image_quality.py` | Laplacian / 梯度近似质量评分 |
| `backend/app/adapters/ocr_paddle.py` | PaddleOCR 薄包装（可选依赖，`DEPENDENCY_MISSING` 守卫） |
| `backend/app/adapters/vlm_adapter.py` | `VlmClient` Protocol + `MockVlmClient`（默认） |
| CLI `parse-images` | `--sha256/--content-uid/--limit/--dry-run/--force/--timeout/--engine/--register` |
| 配置 | `config/image_parse.yaml`（阈值 / 引擎开关；缺失用默认值） |
| JSON 报告 | `parse_image_report_{UTC}.json` |
| pytest + CLI E2E | 引擎相关用例全部 mock；无 paddleocr/cv2 环境也必须全绿 |

## 4. 非目标（015 明确不做）

| 非目标 | 说明 | 归属 |
|--------|------|------|
| PDF 内嵌图片抽取 | MinerU assets 已覆盖 | 007 |
| PDF 扫描页渲染成图再 OCR | MinerU `-m auto` 职责 | 007 |
| 真实 VLM API 接入（HTTP/SDK/密钥） | MVP 仅 Protocol + Mock；真实适配器需 Owner 指定供应商与凭据 | 后续 Spec（O-3） |
| 修改 004 路由规则 / hint 枚举 | IMAGE 路由已存在；015 只消费 `route_type=IMAGE`，不改 `FutureParserHint` | 014 已锁 `parser_routing.py` |
| 修改 `mineru_pdf_parser.py` | 007 封闭（014 白名单之外不动） | — |
| 手写 PaddleOCR 模型训练/调参 | 只用预训练推理 API | — |
| 语义质检 / 置信度质检 | 016 消费 015 产物中的 confidence 字段 | 016 |
| 写 DB（默认） | 仅 `--register` 经 006 `ParseRegistryService` | 006 |
| 改 schema / migration | 禁止 | — |
| GIF 多帧解析 | MVP 仅取首帧 | plan §10 |

---

## 5. 数据流

```text
kb_file_content（route_type=IMAGE, vault_status=COPIED）
  → 只读 open raw_vault/.../original.bin
  → 预处理（deskew→denoise→binarize，staging 临时目录）
  → 质量评分 quality_score
  → 引擎路由：score>=阈值 → PaddleOCR；否则 → VLM；--engine 覆盖
  → 引擎结果（text + confidence + engine_meta）
  → parsed/by_hash/.../parsed_text.md + parsed_metadata.json + parse_manifest.json
     + assets/preprocess/（预处理中间图，可选追溯）
  → reports_root/parse_image_report_{UTC}.json
  → [可选 --register] ParseRegistryService.register_parse_report()
```

**015 磁盘写入**：`parsed/**`、`reports_root/parse_image_report_*.json`。
**不写入**：`raw_vault/`、`curated/`、`quarantine/`；默认不写 MySQL。

---

## 6. 产物契约（沿用 005/007 三件套）

```text
parsed/by_hash/{sha256[:2]}/{sha256[2:4]}/{sha256}/
  parsed_text.md           # 必选 — OCR/VLM 文本
  parsed_metadata.json     # 必选 — 见下
  parse_manifest.json      # 必选 — 追溯与 status
  assets/preprocess/       # 可选 — 预处理中间图
```

`parsed_metadata.json` 扩展字段（016 消费）：

```json
{
  "parser_name": "image_ocr",
  "parser_profile": "image_paddleocr_v1 | image_vlm_v1",
  "parser_adapter_version": "015_mvp_v1",
  "route_type": "IMAGE",
  "image_quality": {"score": 0.83, "method": "laplacian", "preprocess_ops": ["deskew"]},
  "ocr_confidence": {"mean": 0.91, "min": 0.62, "sample_count": 47},
  "engine": {"requested": "auto", "actual": "paddleocr", "fallback_reason": null}
}
```

**`parser_name = "image_ocr"` 是新值**：008 `ALLOWED_PARSER_NAMES = {"markitdown","mineru"}` 与 009 summarizer 同名常量必须扩展，否则 008 会误报 `MANIFEST_PARSER_NAME_INVALID`。该扩展划入 **016 白名单**（016 spec §3），015 自身不改质检代码。

---

## 7. 引擎路由规则（摘要，详见 plan §8）

| 条件 | actual 引擎 | 备注 |
|------|-------------|------|
| `score >= blur_threshold` 且 paddleocr 可用 | paddleocr | 主路径 |
| `score < blur_threshold` 且 VLM 可用（非 Mock） | vlm | 低质兜底 |
| 评分失败（`score=null`） | vlm | fail-safe 到低质路径 |
| paddleocr 缺失且 VLM 为 Mock | — | 条目 `UNSUPPORTED_WITH_HINT`，SKIPPED，不写 parsed |
| `--engine paddleocr` 但缺失 | — | `DEPENDENCY_MISSING`，CLI 非 dry-run exit 1 |

---

## 8. 业务规则

1. 原始文件与 raw_vault 只读；预处理中间产物只进 staging / `assets/preprocess/`。
2. 幂等：manifest `status=SUCCESS` 且 `parser_name`/`parser_profile`/`parser_adapter_version` 匹配 → skip；`--force` 清产物重跑。
3. 单条失败记 `errors[]` continue；`UNSUPPORTED_WITH_HINT` 计入 summary 不抛异常。
4. dry-run：零引擎调用、零 parsed 写入、零 DB、零报告文件（stdout 输出 `would_parse/would_skip`）。
5. **诚实性红线**：任何引擎（含 Mock）不得在无真实识别的情况下产出看似真实的 `parsed_text.md`；Mock 路径只能以 `UNSUPPORTED_WITH_HINT` 收场。
6. CLI 入口 `ensure_readonly()`。
7. 中文路径/中文文件名图片全链路兼容。

---

## 9. 硬约束清单（Dev / QA 必遵）

1. 015 只消费 `route_type=IMAGE`；其他 route 一律 SKIPPED（`ROUTE_MISMATCH`）。
2. 不修改 `parser_routing.py` / `mineru_pdf_parser.py` / `markitdown_parser.py`。
3. 不改 schema、无 migration；默认不写 DB；`--register` 仅经 006 service API。
4. 不写 `raw_vault/`、`curated/`、`quarantine/`。
5. 引擎调用必须经 adapter 薄包装；service 不直接 import paddleocr。
6. 测试默认全 mock；真实 paddleocr/cv2 缺失时测试套件必须全绿（skipif 或 mock）。
7. MockVlmClient 输出必须带 `mock=true` 标记且被管线转为 `UNSUPPORTED_WITH_HINT`（D-2）。
8. dry-run 零副作用。
9. 新增依赖必须可选化（try-import guard）；requirements.txt 变更需 Owner 批准（O-2）。

发现必须写 DB / 改 schema → **STOP → TL**。

---

## 10. 与 004 / 007 / 014 / 016 的边界

| Spec | 关系 |
|------|------|
| **004** | 只消费既有 `IMAGE` 路由；不改 hint 枚举（是否新增 `IMAGE_OCR_FAMILY` hint 留 O-4，015 不依赖） |
| **007** | 不改 MinerU 适配器；PDF 与 IMAGE 管线物理分离 |
| **014** | 并行演进；014 改 PDF 分支，015 不动 `parser_routing.py`，无文件冲突 |
| **016** | 016 扩展 `ALLOWED_PARSER_NAMES` 纳入 `image_ocr`，并消费 `ocr_confidence` 字段做 `OCR_LOW_CONFIDENCE` 检查；015 需在 metadata 契约中保证该字段（无真实置信度时写 `null` 而非编造） |

---

## 11. 成功标准（Spec 级）

1. mock PaddleOCR 对清晰图 fixture 产出三件套 + `ocr_confidence`。
2. 低质图（模糊 fixture）路由到 VLM；VLM 为 Mock → `UNSUPPORTED_WITH_HINT`，零 parsed 产物。
3. paddleocr 缺失 + `--engine paddleocr` → `DEPENDENCY_MISSING` 清晰失败。
4. 非 IMAGE content 全部 SKIPPED。
5. dry-run 零副作用；幂等 skip；`--force` 重跑。
6. raw_vault 与原始文件不变；中文路径通过。
7. 001–013 回归通过；无 paddleocr/cv2 环境下测试全绿。

---

## 12. Owner 裁定记录（2026-10-16，全部 RESOLVED）

| # | 问题 | 裁定 |
|---|------|------|
| **O-1** | SPEC_INDEX 激活 015 | **APPROVED**：Loop S1 整体激活，SPEC_INDEX §1 已登记 |
| **O-2** | 可选依赖策略 | **APPROVED（按 TL 建议）**：`paddleocr` / `opencv-python-headless` 不进默认 requirements；文档说明 + `DEPENDENCY_MISSING` 优雅降级；Pillow 直接复用 |
| **O-3** | 真实 VLM 供应商与凭据 | **APPROVED（按 TL 建议）**：MVP 仅 `VlmClient` Protocol + `MockVlmClient`；真实接入另开 Spec |
| **O-4** | 是否新增 `FutureParserHint.IMAGE_OCR_FAMILY` | **RESOLVED：暂不新增**；015 按 `route_type=IMAGE` 消费 |
| **O-5** | 模糊度阈值与 GIF 策略 | **FROZEN**：`blur_threshold=100.0`（Laplacian 方差）；GIF 取首帧 + `engine_meta` 记录 |

---

**Spec 结束** — 实现细节见 `plan.md`、`acceptance.md`。
