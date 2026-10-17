# Plan: 独立图片解析管线（015 实现计划）

> **项目**：`doc2md`（个人历史项目文档知识库）
> **项目路径**：`/home/szf/dev/doc2md`
> **版本基线**：V1.1-SDD / Loop S1
> **编写日期**：2026-10-16
> **前置条件**：001–013、902 已完成；Owner 已批准 spec.md §12 O-1 ~ O-5
> **编写角色**：Tech Lead（spec-lead）— Loop S1 G0
> **建议分支**：`feature/015-image-parse-pipeline`

---

## 1. 背景

见 `spec.md` §1。IMAGE 路由 hint 指向 MINERU_FAMILY，但 `mineru_pdf_parser.py:56-61` 的 `IN_SCOPE_ROUTE_TYPES` 不含 IMAGE → 独立图片是死胡同。015 新建「预处理 → 质量评分 → 引擎路由」独立管线。

---

## 2. 输入数据

### 2.1 MySQL（只读；`--register` 时经 006 写 registry）

与 007 §7.1 同构。候选条件：`sha256 IS NOT NULL AND status='CONTENT_REGISTERED' AND vault_status='COPIED'`，再按 `route_type=IMAGE` 过滤（复用 `match_route_type`，只读 import）。

### 2.2 Vault 输入

`build_vault_dir()` + `build_vault_artifact_paths()` 定位 `original.bin`；只读 open。

### 2.3 配置

`config/image_parse.yaml`（新文件，缺失用代码默认值）：

```yaml
image_parse:
  adapter_version: "015_mvp_v1"
  quality:
    blur_threshold: 100.0        # Laplacian 方差；>= 视为清晰
    scorer: "auto"               # auto: cv2 优先，Pillow 近似兜底
  preprocess:
    enabled: true                # 总开关；单步失败不中断
    ops: ["deskew", "denoise", "binarize"]
  engines:
    default: "auto"              # auto | paddleocr | vlm
    paddleocr:
      lang: "ch"
      use_gpu: false
    vlm:
      adapter: "mock"            # MVP 仅 mock；真实 adapter 另开 Spec
      endpoint: null
      timeout_seconds: 120
  timeout_seconds: 300
  max_limit: 100
```

`config/app.yaml` 复用既有 `storage.*` 与 `mysql.*`，无新增键。

---

## 3. 输出数据

### 3.1 parsed 三件套 + assets（契约见 spec.md §6）

- `parsed_text.md`：引擎文本原样写入（Mock 引擎永远到不了这一步）。
- `parsed_metadata.json`：含 `image_quality`、`ocr_confidence`、`engine` 三块（016 消费 `ocr_confidence`）。
- `parse_manifest.json`：字段与 007 manifest 对齐（`content_uid/sha256/route_type/parser_name/parser_profile/parser_adapter_version/source_vault_path/status/content_size_bytes/output_size_bytes/output_hash/generated_at`），另含 `assets_dir`（若存在）。
- `assets/preprocess/`：deskew/denoise/binarize 中间图，文件名 `{op}.png`；`parsed_metadata.json.extra.asset_files[]` 索引（对齐 007 §8.2）。

### 3.2 报告

`parse_image_report_{UTC}.json`，结构族对齐 `parse_mineru_pdf_report`：`report_type="parse_image_report"`、`parser_name="image_ocr"`、`summary`（total/in_scope/parsed/skipped/failed/unsupported）、`items[]`、`errors[]`、`plans[]`。

### 3.3 关键 status / decision 词表

```text
status:   SUCCESS | EMPTY | SKIPPED | FAILED
decision: PARSED | ALREADY_SUCCESS | ROUTE_MISMATCH | UNSUPPORTED_WITH_HINT
        | DEPENDENCY_MISSING | ENGINE_ERROR | TIMEOUT | OUTPUT_CONTRACT_VIOLATION
skip_reason: idempotent_success_manifest | parse_limit_reached | route_type=...
```

---

## 4. 关键数据对象

```text
ImageQualityResult(dataclass)
  score: float | None        # None = 评分失败
  method: str                # laplacian | pillow_gradient | unavailable
  detail: str

OcrResult(dataclass)
  text: str
  confidence_mean: float | None
  confidence_min: float | None
  sample_count: int
  engine: str                # paddleocr | vlm
  mock: bool                 # MockVlmClient 恒 true
  engine_meta: dict

class VlmClient(Protocol):
    def recognize(self, image_path: Path, *, timeout_seconds: int) -> OcrResult: ...

ImageParsePlan / ImageParseItem / ImageParseManyResult   # 对齐 007 的 Plan/Item/ManyResult 三件套
```

---

## 5. 模块设计

### 5.1 `backend/app/adapters/image_preprocess.py`

```text
def preprocess(image_path: Path, staging_dir: Path, ops: list[str]) -> PreprocessResult
```

- cv2 import guard：不可用 → 全部 op 记 `skipped: opencv_missing`，原图直通。
- 每步独立 try：`deskew`（minAreaRect 角度纠正）、`denoise`（fastNlMeansDenoising）、`binarize`（adaptiveThreshold）；失败 → WARNING + 跳过该步继续。
- 中间图写 `staging_dir/preprocess/{op}.png`；最终图路径返回给评分与引擎。
- **永不抛异常**中断批处理（最坏情况 = 用原图）。

### 5.2 `backend/app/adapters/image_quality.py`

```text
def score_image(image_path: Path, scorer: str = "auto") -> ImageQualityResult
```

- `laplacian`：cv2.Laplacian 方差。
- `pillow_gradient`：Pillow 灰度图相邻像素梯度均值近似（无 cv2 时兜底，量纲不同 → 使用独立阈值 `pillow_blur_threshold`，yaml 可配）。
- 两者都不可用 / 解码失败 → `score=None, method="unavailable"`（fail-safe 到 VLM 路径）。

### 5.3 `backend/app/adapters/ocr_paddle.py`

```text
def check_paddleocr_available() -> None   # DEPENDENCY_MISSING
class PaddleOcrClient:
    def recognize(self, image_path: Path, *, timeout_seconds: int) -> OcrResult
```

- 惰性 import `paddleocr`；结果聚合为纯文本 + 逐行置信度统计（mean/min/sample_count）。
- subprocess 或 in-process 由 Dev 择一（建议 in-process 惰性 import，避免模型重复加载；超时由 service 层包裹）。

### 5.4 `backend/app/adapters/vlm_adapter.py`

```text
class VlmClient(Protocol): ...
class MockVlmClient:
    def recognize(...) -> OcrResult(text="", confidence_mean=None, ..., engine="vlm", mock=True,
                                    engine_meta={"note": "mock adapter; no real recognition"})
def build_vlm_client(config) -> VlmClient   # 按 yaml vlm.adapter 装配；未知值 → ValueError
```

### 5.5 `backend/app/services/image_parser.py`

`ImageParserService(config, *, ocr_client=None, vlm_client=None, scorer=None, preprocessor=None)`——全部可注入，测试默认注入 mock。

`_build_plan(content)` → `_execute_parse(plan)` 两阶段（对齐 007）：

```text
_build_plan:
  route_type != IMAGE        → ROUTE_MISMATCH / SKIPPED
  manifest 幂等命中           → ALREADY_SUCCESS / SKIPPED
  original.bin 缺失           → FAILED / missing_original_bin
  否则                        → PARSED / would_parse

_execute_parse:
  staging 目录（parsed_root/.staging/image_{run_id}，finally 清理）
  preprocess（尽力而为）
  score_image
  引擎路由（§6）
  无可用引擎                 → UNSUPPORTED_WITH_HINT / SKIPPED（清理 staging，不写 parsed）
  引擎调用                    → OcrResult
  result.mock == True         → UNSUPPORTED_WITH_HINT / SKIPPED（D-2 诚实性红线）
  归一化写三件套 + assets/preprocess 持久化
  status: text.strip() 为空 → EMPTY，否则 SUCCESS
```

---

## 6. 引擎路由真值表

| `--engine` | score | paddleocr | vlm adapter | actual | 结果 |
|------------|-------|-----------|-------------|--------|------|
| auto | ≥阈值 | 可用 | * | paddleocr | 解析 |
| auto | <阈值 或 None | * | real | vlm | 解析 |
| auto | <阈值 或 None | * | mock | — | UNSUPPORTED_WITH_HINT |
| auto | ≥阈值 | 缺失 | real | vlm | 解析（`fallback_reason=paddleocr_missing`） |
| auto | ≥阈值 | 缺失 | mock | — | UNSUPPORTED_WITH_HINT |
| paddleocr | * | 缺失 | * | — | DEPENDENDY_MISSING（CLI exit 1，非 dry-run） |
| paddleocr | * | 可用 | * | paddleocr | 解析（低质也强制执行，warning 记录） |
| vlm | * | * | mock | — | UNSUPPORTED_WITH_HINT |
| vlm | * | * | real | vlm | 解析 |

---

## 7. CLI 设计

```bash
PYTHONPATH=backend python -m app.cli.main parse-images \
  --config config/app.yaml \
  [--sha256 HEX | --content-uid UID] [--limit N] \
  [--engine auto|paddleocr|vlm] [--timeout SECONDS] \
  [--dry-run] [--force] [--register/--no-register]
```

Rich 汇总：Candidates / In-scope / Parsed / Skipped / Unsupported / Failed / Errors + 报告路径。dry-run 不写报告。

---

## 8. 关键设计决策

### 8.1 D-1 独立于 MinerU，不扩展 007

MinerU 是 PDF 文档管线；独立图片走「预处理+评分+OCR/VLM」更符合引擎能力边界，且避免改动已封版的 007。

### 8.2 D-2 MockVlmClient 诚实性红线（反假绿核心）

`mock=true` 的 `OcrResult` 在 service 层被**显式拒绝落盘**：条目记 `UNSUPPORTED_WITH_HINT / SKIPPED`，parsed 目录零写入。理由：若允许 Mock 文本进 `parsed_text.md`，下游 010 证据链 / 012 搜索会消费伪造内容，且测试会呈现「管线通的」假象。验收 A015-06 以变异测试强制该路径。

### 8.3 D-3 评分失败 fail-safe 到低质路径

`score=None` 视同低质 → VLM。理由：无法证明清晰时，选择对低质更鲁棒的引擎；代价仅是 VLM 调用更贵。

### 8.4 D-4 依赖可选化

paddleocr / cv2 一律惰性 import + `check_*_available()` 守卫；CI 环境不装重依赖也必须全绿（引擎用例注入 mock client）。

### 8.5 D-5 产物契约复用

三件套路径经 `backend/app/core/parsed_paths.py` 的 `build_parsed_artifact_paths()`；**复用既有键、不新增键**（`parsed_dir` / `parsed_text` / `parsed_metadata` / `parse_manifest`）——新增的 `assets/preprocess/` 是 `parsed_dir` 的子目录，由既有 `parsed_dir` 键派生，manifest 仅加 `assets_dir` JSON 字段，**不触及 `ParsedArtifactPaths`**（故 015 白名单不含 `parsed_paths.py`）。manifest 字段对齐 007 → 006 registry 可直接 ingest（`--register` 复用 `ParseRegistryService.register_parse_report()`，零 006 改动）。

### 8.6 D-6 GIF 策略

`route_type=IMAGE` 含 `.gif`；MVP 用 Pillow 取首帧（`Image.seek(0)`）后按静态图处理，`engine_meta["gif_first_frame"]=true` 记录。

---

## 9. Dev 实现边界

### 9.1 白名单

| 操作 | 文件 |
|------|------|
| 新增 | `backend/app/services/image_parser.py` |
| 新增 | `backend/app/adapters/image_preprocess.py` / `image_quality.py` / `ocr_paddle.py` / `vlm_adapter.py` |
| 修改 | `backend/app/cli/main.py`（新增 `parse-images`） |
| 新增 | `config/image_parse.yaml` |
| 新增 | `backend/tests/test_image_parser.py`（+ fixtures 现场生成：Pillow 画文字图=清晰、高斯模糊图=低质） |
| 修改 | `backend/requirements.txt`（仅经 O-2 批准的可选依赖注释说明；默认不增硬性依赖） |
| 修改 | `specs/015-image-parse-pipeline/tasks.md` |

### 9.2 禁止修改

```text
backend/app/core/parser_routing.py        # 014 白名单
backend/app/services/mineru_pdf_parser.py # 007/014
backend/app/services/markitdown_parser.py
backend/app/services/parse_quality_checker.py            # 016
backend/app/services/parse_quality_report_summarizer.py  # 016
backend/app/models/**  sql/**  generated/**
raw_vault/**  data/**  specs/001..013/**  specs/902/**
config/parser_rules.yaml
```

### 9.3 测试先行

先写 `test_image_parser.py`（红）→ 实现 → 绿。图片 fixture 由测试用 Pillow 现场生成（含中文路径目录），不提交二进制。

---

## 10. 异常处理设计

| 场景 | 处理 |
|------|------|
| paddleocr 缺失（auto 且图清晰） | fallback VLM；VLM 为 mock → UNSUPPORTED_WITH_HINT |
| 预处理单步失败 | WARNING + 跳过该步 |
| 评分失败 | score=None → VLM 路径 |
| 引擎超时 | TIMEOUT / FAILED，errors[]，continue |
| 引擎返回空文本 | manifest status=EMPTY（对齐 007 语义） |
| Mock VLM 结果被试图落盘 | 不可能发生（D-2 拒绝）；变异测试验证 |
| 图片解码失败（损坏文件） | FAILED / ENGINE_ERROR，continue |
| 单条意外异常 | errors[]，continue |

---

## 11. 幂等性设计

与 007 对齐：`_should_skip_idempotent` 判定 `status=SUCCESS && parser_name="image_ocr" && parser_adapter_version="015_mvp_v1"` 且 `parser_profile` 与本次请求 profile 一致（同 sha256 换引擎 profile → 重跑，符合「同 sha256+parser_profile 不重跑」红线）。`--force` 清三件套 + assets 后重跑。报告每次新 UTC 文件。

---

## 12. Acceptance Criteria 摘要

见 `acceptance.md`（A015-01 ~ A015-12，否定式可执行）。

---

## 13. TL 决策表（Dev 必遵）

| # | 问题 | 决策 |
|---|------|------|
| Q1 | 引擎抽象 | Protocol + 构造注入；service 不直接 import paddleocr |
| Q2 | Mock VLM 输出 | 显式拒绝落盘 → UNSUPPORTED_WITH_HINT |
| Q3 | 评分缺失方向 | fail-safe 到 VLM |
| Q4 | parser_name | `image_ocr`（008/009 白名单扩展划入 016） |
| Q5 | 预处理中间图 | 持久化到 `assets/preprocess/`，metadata 索引 |
| Q6 | GIF | 首帧 + `engine_meta` 记录 |
| Q7 | 依赖安装 | 默认 requirements 不增；文档说明可选安装 |
| Q8 | 默认引擎 | `auto`；阈值 `blur_threshold=100.0`（Laplacian） |

---

## 14. 流水线与 STOP 点

```text
G0 规格 → Owner 裁定 O-1~O-5 → P2 DB Review（确认零 schema 变更、registry 复用合法）
→ P4 Dev（测试先行）→ STOP → P5 QA → P6 E2E → P7 TL Final Review
```

---

**Plan 结束** — Dev 先读 `acceptance.md` 与 §9 白名单再写代码。
