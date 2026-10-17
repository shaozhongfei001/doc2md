# STAGE P2 · L04 派工单（TL → dev-geo / dev-mineru）

工作流 `DOC2MD-19CAP`；Loop `L04`（Paddle/MinerU 适配、坐标与 OCR）；契约 `DOC2MD-19CAP-V1.0`。
前置：L03 已 `QA_PASS`（`qa-pass/L03` = `ae08da6`，DEV 代码域 `qa-target/L03` = `07da949`）。

## 0. 共同约束

| 项目 | 值 |
|---|---|
| 仓库 / 分支 | `/home/szf/dev/doc2md`，`main` |
| **L04 入口 commit** | tag `loop-entry/L04`（= 本派工单提交）。开工前执行 `git rev-parse loop-entry/L04` 取实际 sha；代码区间为 `loop-entry/L04..<你的代码提交>`，doc 提交不得进入该区间 |
| 解释器 | `/tmp/doc2md-l00-venv/bin/python`（3.11.7）；`cd backend && PYTHONPATH=. <PY> -m pytest -q` |
| 白名单（只允许这些） | `backend/app/extensions/geometry/**`、`backend/app/extensions/tables/ocr_alignment.py`、`backend/app/extensions/adapters/mineru_reader.py`、`backend/app/extensions/adapters/paddle_structure.py`、`backend/app/extensions/worker/**`、`backend/tests/extensions/test_geometry.py`、`test_ocr_alignment.py`、`test_layout_adapter.py`、`test_worker_limits.py`、`backend/requirements-ocr-worker.in`、`requirements-ocr-worker.lock` |
| 命名空间包 | `geometry/` 可建 `__init__.py`；`adapters/`、`tables/` **不要**建 `__init__.py`（白名单不含） |
| 禁改 | `extensions/{__init__,contracts,identity,runner,cli}.py`、`io/**`、`sources/**`、`word/**`、`tables/grid.py`、旧 `app/**`、`sql/**`、`specs/**`、`config/**`、`backend/requirements.txt`、现有测试 |
| 禁做 | 不 `git add/commit/checkout/reset/clean`；不联网；不调真实模型；不写 `parsed/`、`raw_vault/` |
| 错误码 | 只用已登记：`E_INPUT_INVALID`、`E_ENGINE_SCHEMA_DRIFT`、`E_ENGINE_UNAVAILABLE`、`E_ENGINE_TIMEOUT`、`E_GEOMETRY_UNVERIFIED`、`E_GEOMETRY_NONINVERTIBLE`、`E_TABLE_ALIGNMENT_AMBIGUOUS`、`E_TABLE_SPAN_INVALID`、`E_EVIDENCE_MISMATCH`。**不得新增码** |
| 回报纪律（P-1/P-4） | 回报前 `git status` 必须空；给"最终确认时刻 + 各文件 md5 + 行数（现拼）"；若还在改，先发"别验收" |

---

## WP-1 · `dev-geo`：T403 几何 + T404 OCR 落格

### 依据
`01_SDD.md` §8.2（坐标合同）、§8.3（矩阵与 OCR 文字）；TC `TC-PI-F04-03-01/02/03/04`、`TC-PI-F04-05-01/02/03`；
`contracts/.../schemas/document-enhancement.schema.json` 的 `PageGeometry` / `PageLocator`。

### 交付
```
backend/app/extensions/geometry/__init__.py
backend/app/extensions/geometry/transform.py
backend/app/extensions/tables/ocr_alignment.py
backend/tests/extensions/test_geometry.py
backend/tests/extensions/test_ocr_alignment.py
```

### 必须实现的公开接口（名字冻结）
```python
# geometry/transform.py
@dataclass(frozen=True)
class PageTransform:
    page_no: int
    original_width_px: int
    original_height_px: int
    processed_width_px: int
    processed_height_px: int
    crop_origin_px: tuple[float, float]
    matrix: tuple[float, ...] | None      # 3x3 齐次，original -> processed
    unit_verified: bool

def identity_transform(**dims) -> PageTransform
def compose(*steps: PageTransform) -> PageTransform
def invert(transform: PageTransform) -> PageTransform       # 不可逆 → E_GEOMETRY_NONINVERTIBLE
def apply_to_point(transform, x: float, y: float) -> tuple[float, float]
def apply_to_bbox(transform, bbox) -> tuple[float, float, float, float]
def to_original_bbox(transform, bbox, *, epsilon_px: float = 1.0) -> tuple[...]   # 逆映射四角
def normalized_polygon(transform, bbox) -> list[tuple[float, float]]              # 0..1，越界拒绝
```
规则：
1. **未知单位不得冒充已验证**：`unit_verified=False` 时 `normalized_polygon` 仍可算，但调用方只能产 `PAGE_ONLY`；`transform` 无法证明时不得写 `REGION_VERIFIED`。
2. **矩阵必须可逆**（`abs(det) >= 1e-12`），否则 `E_GEOMETRY_NONINVERTIBLE`。
3. bbox 逆映射必须作用**四角**，再取包围盒；只缩放宽高视为错误（写测试证明两者结果不同）。
4. 越界/NaN/Inf → `E_GEOMETRY_UNVERIFIED` 或 `E_INPUT_INVALID`（按语义选，写清理由）。
5. 往返误差用 `epsilon_px`（初值 1.0）判定；超过即拒绝，不裁剪。

```python
# tables/ocr_alignment.py
def align_tokens_to_cells(
    *, cells: Sequence[Mapping], tokens: Sequence[Mapping],
    min_coverage: float = 0.8, min_margin: float = 0.15,
) -> AlignmentResult
```
`AlignmentResult`：`assignments: dict[cell_id, list[token_id]]`、`ambiguous: list[dict]`、`unassigned_tokens: list[str]`、`stats`。
规则（对应 §8.3 与 TC-PI-F04-05-02/03）：
1. **优先引擎已给文字**：cell 已有 `text` 且 `text_alignment != "UNKNOWN"` 时不覆盖，只在报告里记 `source="ENGINE_CELL_TEXT"`。
2. 覆盖率达阈值**且**第一二名差值 ≥ `min_margin` 才唯一绑定；否则进 `ambiguous`，**不强配、不按阅读顺序分列**。
3. 跨两格、得分接近、跨行折返、文本被截断 → 一律 `ambiguous`。
4. 保留原 token 文字与框；**不清洗**负号、括号负数、小数点、百分号、单位。
5. 阈值必须**真接线**（可注入），并写一条"把 `min_coverage` 调到 0.99 后某绑定消失"的断言。

### 测试下限 ≥40 例
几何：单位恒等往返、crop+rotate+scale 组合、四角逆映射 vs 只缩放宽高（证明实现选对）、
奇异矩阵拒绝、NaN/Inf 拒绝、越界坐标、`unit_verified=False` 时不得产 REGION_VERIFIED 的证据路径。
OCR：唯一覆盖、两格跨越、得分接近、多行折返、截断、负号/百分号保留、已给文字不被覆盖、
阈值可注入、`ambiguous` 不产生绑定。

---

## WP-2 · `dev-mineru`：T402 MinerU 只读适配（+ T401 部分）

### 依据
`01_SDD.md` §8.1（复用既有 MinerU 原始 JSON）、§5 IF05；TC `TC-PI-F04-02-01/02/03`、`TC-PI-F04-03-02`。

### 交付
```
backend/app/extensions/adapters/mineru_reader.py
backend/tests/extensions/test_layout_adapter.py
# T401 部分：
backend/app/extensions/worker/__init__.py        （可建）
backend/app/extensions/worker/limits.py
backend/tests/extensions/test_worker_limits.py
backend/requirements-ocr-worker.in / .lock       （或如实标 INPUT_BLOCKED，见下）
```

### T402 必须实现的接口
```python
def load_content_list(path) -> list[dict]           # 只读；schema 漂移 → E_ENGINE_SCHEMA_DRIFT
def load_middle(path) -> dict
def pair_blocks(content_list: list[dict], middle: dict) -> list[dict]
    # 唯一匹配才绑定；歧义 → 留 mapping issue，不按数组下标硬绑
def read_mineru_source(parsed_dir) -> MineruSource
    # 读 parsed/<sha>/ir_source/{content_list.json,middle.json}；缺失 → NEEDS_BASE_PARSE
```
规则：
1. **真实 golden**：仓内已有真实 MinerU 产物 `handoff/external-refactor-2026-10-17/10-artifacts/content_list.json`（只读引用），用它做 ADAPTER_GOLDEN 用例，**不要自编 JSON 冒充真实引擎输出**。
2. 匹配按 `页 + type + asset hash + 可核文本指纹 + 引擎局部ID` 分层；同页多个同型块或相同 caption → `mapping_issue`，**不按序号硬绑**。
3. 字段缺失/类型变化 → `E_ENGINE_SCHEMA_DRIFT`，**不静默兜成空表**。
4. 坐标单位未知时装进 `PageGeometry` 但 `unit_verified=False`；不与 T403 的单位判定抢职责（T403 给工具，你只搬运原值）。
5. 只读：不写 `parsed/`，不改原件。

### T401 的诚实边界
- 本机只有 `paddleocr 2.7.3`，契约要求 **PaddleOCR v3.3.3 + 锁 Paddle/PaddleX/权重**。真实引擎侧 → **`INPUT_BLOCKED`**（OI03）。
- 允许交付 `worker/limits.py`：实现**硬超时 + 进程组回收 + 总预算**的可注入骨架（用 fake child/可注入时钟测试），**不要**假装跑过真实 Paddle。
- `requirements-ocr-worker.in/.lock`：若能解析出可安装的组合就锁；**不能**就只写 `.in`（含 `# INPUT_BLOCKED: ...` 理由）并**不要编造 .lock 哈希**。如实回报你选了哪条路。

### 测试下限 ≥22 例
真实 `content_list.json` 解析与字段抽取、schema 漂移（删字段/改类型）、缺失 `ir_source` → `NEEDS_BASE_PARSE`、
唯一匹配绑定、歧义不绑定、同页同型块、只读断言（读前读后 `parsed/` 哈希不变）、
worker 超时触发、预算耗尽、进程组回收调用、无真引擎时 `INPUT_BLOCKED` 状态如实返回。

---

## 交接与停止点

| 工作包 | 停止边界 |
|---|---|
| WP-1 | 完成 T403+T404 即停；不碰 `adapters/`、`worker/`、`word/**`、`grid.py` |
| WP-2 | 完成 T402 + T401 允许部分即停；不碰 `geometry/`、`ocr_alignment.py`、`word/**`、`grid.py` |

回报格式（两者一致）：
1) 文件清单 + 行数 + md5(12)（现拼）2) 命令 + 退出码 + passed/failed/skipped
3) 未完成/缺口（含 `INPUT_BLOCKED` 理由与你选的路）4) 是否触碰禁改（应恒为"否"）5) 是否已提交（应恒为"否"）
6) 若你做了变异，给出"变异点/目标测试/rc/还原 rc/md5"

**不需要**你写证据文档（`docs/**` 由 TL 负责）；不要在回报里引用别人给的数字。
