# L04 验收记录（T401–T405）

- 工作流：`DOC2MD-19CAP` / 契约 `DOC2MD-19CAP-V1.0`（`DESIGN_CANDIDATE`）
- Loop：`L04 Paddle/MinerU适配、坐标和OCR`
- 覆盖需求：`PI-F04-02`、`PI-F04-03`、`PI-F04-05`
- 入口 commit（`loop-entry/L04`，重基线后）：`10a220afa22822dc0651306bf860f0662fd95215`
- 代码：`c8cf054`（T403/T404）、`c13b474`（T402，DEV 交付）、`58587d9`（T401，TL 自写）
- 合并：`--ff-only` → `main` = `58587d9`
- **状态：`CODE_MERGED` + `T405 NOT_RUN` + `INDEPENDENT_QA_PENDING`**

## 1. 任务与执行者

| 任务 | 执行者 | 文件 | 状态 |
|---|---|---|---|
| T401 Paddle schema + worker 边界 | TL 自写 | `adapters/paddle_structure.py`、`worker/**`、`requirements-ocr-worker.{in,lock}`、`test_worker_limits.py` | 已合入，25 例 |
| T402 MinerU 只读映射 | **派发的 DEV（dev-mineru）** | `adapters/mineru_reader.py`、`test_layout_adapter.py` | 已合入，40 例 + DEV 自做 3 组变异 |
| T403 坐标变换 | TL 自写 | `geometry/transform.py` | 已合入，22 例 |
| T404 OCR 对齐 | TL 自写 | `tables/ocr_alignment.py` | 已合入，23 例 |
| T405 真实扫描语料分层评测 | — | — | **`NOT_RUN`（无语料、无引擎）** |

> 这是本工作流**第一次**成功由派发角色交付实现（此前两轮派单产出为零）。

## 2. 门禁实测

| 门 | 命令 | 结果 |
|---|---|---|
| A1 | `check_loop_diff --repo /tmp/d19/L04 --loop L04 --base-commit 10a220af…` | `passed=true`，`errors=[]`，13 个文件全在白名单 |
| B | `pytest -q` | **1071 passed / 133 skipped**（L05 后基线 961，新增 110） |
| C | `check_baseline --allow-index-append` | `passed=true`，`errors=[]` |
| DB | 新增文件 grep DB 调用 | 无 |
| IO | `paddle_structure.py`/`mineru_reader.py`/`worker/**` 无生产写盘；`review_packet` 不在本 Loop | 通过 |

## 3. TL 独立探针（非 DEV 测试）

对 T402 的 6 组探针，全部实测：

| 探针 | 预期 | 实测 |
|---|---|---|
| 两表数组顺序颠倒（asset+caption 唯一） | 按证据配对，不按下标 | entry0→`[0,100,100,200]`(表B)，entry1→表A ✓ trace 显示 `page→type→asset_hash→text_fingerprint→engine_id:unavailable→bound` |
| asset 与 caption 自相矛盾（img=b, caption=表A） | 拒绝不选边 | `NO_CANDIDATE` ✓ |
| 同一块被两条 entry 抢 | 一块一绑 | 第二条 `BLOCK_ALREADY_BOUND` ✓ |
| 指纹碰撞（`"12 480"` vs `"12480"`） | 不猜 | `AMBIGUOUS_MATCH` ✓ |
| 几何页号不符（page_idx+1 ≠ geometry.page_number） | 降级 | `PAGE_ONLY` + `PAGE_GEOMETRY_PAGE_MISMATCH` ✓ |
| 单位未验证 | 降级 | `PAGE_ONLY` + `E_GEOMETRY_UNVERIFIED: …` ✓ |

对 T403 的探针发现一个真实漏洞并已修：原实现只校验"回映后是否在页内"，若引擎返回的坐标其实属于**另一个坐标系**（缩放/裁剪漂移），逆变换仍可能落回页内，产出"看起来合理但错误"的区域。已补"输入多边形必须落在 `processed_canvas` 内"，不满足报 `E_INPUT_INVALID`，并加回归测试。

## 4. 登记的技术决定（需在真实语料上复核）

| 编号 | 决定 | 依据与代价 |
|---|---|---|
| `L04-DEC-01` | asset 型（table/image）绑定**必须**命中 asset hash；缺 `img_path` 时即使 caption 指纹唯一也不绑（报 `MISSING_ASSET_HASH`） | DEV 的判断：表格身份不应只靠 caption 文本，续表/同页同型表容易撞。代价是召回下降。**TL 探针确认这一路径可达**：两表 caption 唯一但无 img_path 时零绑定。真实语料若显示引擎不产 `img_path`，此规则需改判 |
| `L04-DEC-02` | 文本指纹 = NFKC + 删除全部空白 | 真实 golden 里 same-sentence 的 span/换行/全半角不同，逐字节比较会全灭。代价：`"12 480"` 与 `"12480"` 会碰撞——实测碰撞走 `AMBIGUOUS_MATCH`，不产生错误绑定 |
| `L04-DEC-03` | content_list 的 `index` 永不作为引擎 ID，只认显式 `middle_index` | `index` 就是数组下标，读它等于把位置匹配偷渡回来。DEV 用变异验证：改成读 `index` 会让专门用例变红 |

## 5. 缺口与未验证（不写"应当通过"）

| 编号 | 缺口 | 影响 |
|---|---|---|
| `L04-GAP-01` | `requirements-ocr-worker.lock` 为 **UNRESOLVED** 占位（无引擎环境，未解析传递依赖与哈希） | 真实 worker 部署前必须解析并 `--require-hashes` |
| `L04-GAP-02` | Paddle 字段名未用 golden raw 确认（`PADDLE_LOCK_NOTE` 写进每个输出） | 版本一旦漂移，只能靠 `E_ENGINE_SCHEMA_DRIFT` 兜底 |
| `L04-GAP-03` | 阈值 0.8 / 0.15 未校准（SDD 明示需 golden 校准） | 召回/精度未知 |
| `L04-GAP-04` | `BBOX_DISAGREEMENT` 路径只有合成用例（本仓两份真实 MinerU 产物都无 bbox） | 真实触发条件未验 |
| `L04-GAP-05` | `type=equation` 永不可绑（无文本指纹） | 真实样本是否可接受未知 |
| `L04-GAP-06` | T405 真实扫描语料分层评测 | `NOT_RUN` |

## 6. 结论

T401–T404 的实现与测试已合入 `main`，A1/B/C 三门绿，TL 探针发现并修复 1 个坐标空间漏洞。
**T405 未执行**，`lock` 未解析，Paddle 字段未对真实 golden 确认——因此本 Loop 的定位是
"适配层与几何层可调用且 fail-closed"，**不是**"已用真实引擎验证"。
`INDEPENDENT_QA` 仍为 `PENDING`（无独立于写码者的角色）。
