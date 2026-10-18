# L06 验收记录（T601–T606）

- 工作流：`DOC2MD-19CAP` / 契约 `DOC2MD-19CAP-V1.0`（`DESIGN_CANDIDATE`）
- Loop：`L06 流程图、图表与一次ROI复识别`
- 覆盖需求：`PI-F05-01` … `PI-F05-06`
- 入口 commit（`loop-entry/L06`）：`03c60ee`
- 代码：`ea1b96d`（vision 包入口）、`3658537`（T604/T605）、`912f3b6`（T602）、`58d2b70`（契约测试）、`05eef54`（T601/T603）
- 合并：`--ff-only` → `main` = `05eef54`
- **状态：`CODE_MERGED` + `T606 NOT_RUN` + `INDEPENDENT_QA_PENDING`**

## 1. 派发情况（必须记录）

| 尝试 | 角色 | 结果 |
|---|---|---|
| 第 1、2 次 | `dev-geo`/`dev-mineru`（L04 早期） | 派单丢失，零产出 |
| 第 3 次 | `dev-mineru`（L04/T402） | **成功**，T402 交付并验收 |
| 第 4、5 次 | `dev-vision-core`/`dev-vision-flow`（L06） | `Spawned` 后 `Known members: none`，磁盘零文件 |

结论：派发通道不稳定，**不能作为关键路径**。L06 的 T601/T602/T603/T604/T605 全部由 TL 接管实现。
本记录不把 TL 自写伪装成 DEV 交付。

## 2. 门禁实测

| 门 | 命令 | 结果 |
|---|---|---|
| A1 | `check_loop_diff --repo /tmp/d19/L06 --loop L06 --base 03c60ee` | `passed=true`，`errors=[]`，**14 个文件**全在白名单 |
| B | `pytest -q` | **1181 passed / 133 skipped**（L04 后 1071，新增 110） |
| C | `check_baseline --allow-index-append` | `passed=true`，`errors=[]` |
| 契约 | `contract_models.Chart / FlowGraph / Visual` 直接校验 | 三条图表提升路径 + 流程图 + 区域分类均 **accepted** |

`58d2b70` 的根契约测试不是"多写几个断言"：`to_chart_points` 的中间产物带 `label_verified`
这个契约外键，一旦有人把它直接塞进 `DocumentEnhancement` 就会重演 `L03-GAP-06`
（表级过、根级不过）。现在这件事由常驻测试守着，而不是靠文档提醒。

## 3. 任务与实现要点

| 任务 | 文件 | 关键约束落地 |
|---|---|---|
| T601 区域路由 | `vision/classify.py` | 框架 `image/figure/picture` **一律 UNKNOWN**，不证明 FLOW；VLM 只能给 CANDIDATE；DECORATIVE 保留 asset 与理由；重叠组唯一事实所有者（面积最小），容器另记 parent；等面积 → 无 owner + NEEDS_REVIEW |
| T602 流程候选 | `vision/flow.py`、`adapters/vision_http.py` | 逐行成节点、重复文字不合并；**代码中不存在按坐标/阅读序补边的路径**；双向/端点缺失/方向未声明 → UNRESOLVED；拒绝外部 URL、`tool_call`/`instruction`/`system` 字段、未知端点、缺 `id_map` |
| T603 一致性 | `vision/consistency.py` | 结构硬错阻断确认；孤立点/无出口决策/泳道变化/缺分支标签为软发现且不删；环仅 `CYCLE_PRESENT`；图文判定保留双方原文；**不改传入 graph**（测试断言） |
| T604 统计图 | `vision/chart.py`、`adapters/chart_paddle.py` | 未核验 `PRINTED_LABEL` 降级 UNKNOWN；数值只收小数字符串；表图冲突双方保留不覆盖；轴截断/对数/双轴/堆叠/缺刻度/混单位/负数专项检查 |
| T605 ROI | `vision/roi.py` | 每对象一轮（配置改不动）；PAGE_ONLY 回退不猜裁图；运输重试单独计数；新旧候选并存、不按自报分数覆盖；同模型换提示标 `independent=False` |
| T606 真实盲评 | — | **`NOT_RUN`**（无 VLM/GPU、无语料） |

## 4. 登记的技术决定

| 编号 | 决定 | 代价/依据 |
|---|---|---|
| `VIS-DEC-01` | 框架 `image` 标签 → `UNKNOWN`，即使看起来像流程图 | SDD 10.1 原文；代价是更多区域进人工 |
| `VIS-DEC-02` | 重叠组事实所有者 = 面积最小者；等面积无 owner | 避免同像素双计数；等面积不靠任意 tie-break |
| `VIS-DEC-03` | `PRINTED_LABEL` 无核验 → **降级**而非报错 | 报错会连证据一起丢掉，降级保留问题可见 |
| `VIS-DEC-04` | `to_chart_points` 中间产物含契约外键，只有 `build_chart` 输出是契约形状 | 已由根契约测试锁定 |

## 5. 缺口与未验证（如实列出）

| 编号 | 缺口 | 影响 |
|---|---|---|
| `L06-GAP-01` | T606 真实语料盲评 `NOT_RUN` | 模型能力与工程结构校验**未分离测量** |
| `L06-GAP-02` | PP-Chart2Table 字段名未对 golden raw 确认（`CHART_LOCK_NOTE` 写入每个输出） | 版本漂移只能靠 `E_ENGINE_SCHEMA_DRIFT` 兜底 |
| `L06-GAP-03` | `vision_http` 未对真实 VLM 响应验证 | 只保证"拒绝非法"，不保证"接受真实" |
| `L06-GAP-04` | 图文一致性只做了**词面**判定（是否提及 + 否定极性），**未做**动作/角色/先后的语义比对 | SDD 10.3 要求逐项比动作、角色、先后、条件；当前只是最低层筛子，不能当语义一致结论 |
| `L06-GAP-05` | ROI 预算未在真实引擎上触发过（无引擎） | 计数逻辑已测，真实超时行为未验 |

## 6. 结论

T601–T605 已合入 `main`，A1/B/C 与三份根契约校验全绿，110 例新增测试全部通过。
但本 Loop 的**能力边界必须说清**：

- 它能保证"不猜、不补、不按分数覆盖、不把模型自评当概率"；
- 它**不能**保证图里的语义被正确理解——`L06-GAP-04` 是真实的语义缺口，不是文档瑕疵。

`INDEPENDENT_QA` 仍为 `PENDING`。
