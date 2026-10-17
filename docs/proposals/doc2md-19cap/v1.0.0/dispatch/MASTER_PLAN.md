# DOC2MD-19CAP 长程执行总计划（TL）

工作流 `DOC2MD-19CAP`；契约 `DOC2MD-19CAP-V1.0`（`DESIGN_CANDIDATE`）。
本计划把 L00—L08 切成 **6 个可控阶段（P1—P6）**，每阶段以 Loop Engineering 方式推进：
`TL_PLAN → DEV_SELF_TEST → DB_REVIEW → INDEPENDENT_QA → E2E → HO_HANDOFF → TL_FINAL`。
**每阶段只有一个"可验收目标"**，未过 TL 验收不进下一阶段。

## 0. 当前进度（2026-10-18）

| 阶段 | Loop | 状态 | 证据 |
|---|---|---|---|
| P0 | DOC_IMPORT + L00 + L01 + L02 | **DONE（DEV 自测）**，DB/QA 待独立复核 | `evidence/L00`、`evidence/L01`、`evidence/L02` |
| 起点 | — | HEAD `2d257e1`；全量 **715 passed / 0 failed / 133 skipped**；变异 **11/11** | 见各 Loop 证据 |

> P0 遗留：`DB_REVIEW=PENDING`、`INDEPENDENT_QA=PENDING`。P1 起把这两步**必须**交给独立智能体执行，TL 不再自证。

## 1. 多角色智能体编制

| 代号 | 角色 | 职责 | 允许写入 |
|---|---|---|---|
| `tl` | Tech Lead（本会话） | 派工、控范围、验收、提交、差异决议 | 提交权；`docs/proposals/**` 控制文档 |
| `dev-word` | DEV | 原生 Word 结构与表格抽取（T301/T302） | `app/extensions/word/**`、`tests/extensions/test_word_*.py` |
| `dev-grid` | DEV | 表格网格校验与契约投影（T303） | `app/extensions/tables/grid.py`、`tests/extensions/test_table_grid.py` |
| `dev-geo` | DEV | 几何变换与 OCR 落格（T403/T404） | `app/extensions/geometry/**`、`app/extensions/tables/ocr_alignment.py`、`tests/extensions/test_geometry.py`、`test_ocr_alignment.py` |
| `dev-mineru` | DEV | MinerU 只读适配（T402） | `app/extensions/adapters/mineru_reader.py`、`tests/extensions/test_layout_adapter.py` |
| `dev-table-sem` | DEV | 表头路径/续表/脚注（T501–T503） | `app/extensions/tables/{semantics,continuation,notes}.py` + 对应测试 |
| `dev-review` | DEV | 纠错补丁/候选证据/依赖（T701–T704） | `app/extensions/{evidence,review,release/consumers}/**` + 对应测试 |
| `db-reviewer` | DB | 数据库与存储边界审核（只读） | 无写权限；只输出结论 |
| `qa-verifier` | QA | 独立验收：复跑、补变异、判定 PASS/RETURN | 无写权限；只输出结论 |
| `ho-writer` | HO | 按证据写交接材料 | TL 授权后写 `docs/proposals/**/evidence/**` |

**独立性纪律**：`db-reviewer` / `qa-verifier` 不得由写过被审代码的智能体担任。
若某阶段找不到未参与该代码的智能体，状态记 `*_PENDING`，不得用改名制造独立结论。
同一文件同一时间**只有一个写者**；跨阶段文件不重叠，`runner.py`/`cli.py`/`contracts.py` 等共享文件只由 `tl` 修改。

## 2. 阶段划分与验收门

### P1 · L03 原生 Word 与表格结构

| 项目 | 内容 |
|---|---|
| 任务 | T301（安全只读 OOXML、FINAL 视图与审阅分轨）、T302（gridSpan/vMerge/gridBefore/After、嵌套表定位）、T303（独立 grid 严格校验与 slots）、T304（Gold 与原件零变化） |
| 派发 | `dev-word`（T301+T302）、`dev-grid`（T303）、`db-reviewer`（P0 的 L01/L02 DB 审核）并行 |
| 可验收目标 | 基础 Gold（横纵合并/空白/省略/嵌套/文本修订/结构修订拒绝）**逐格一致**；原件与 `parsed/` 哈希不变；新测试 ≥45 例全绿；`check_loop_diff --loop L03` PASS |
| 拒绝分支 | `E_DOCX_MERGE_INVALID`、`E_TABLE_SPAN_INVALID`、`E_DOCX_REVISION_UNRESOLVED` 各有正例+负例，且至少 4 组能被打红 |
| 外部阻塞 | 无。`python-docx 1.2.0` + `lxml 4.9.3` 已就绪，DOCX 由测试用 python-docx 现场构造 |

### P2 · L04 适配、坐标与 OCR

| 项目 | 内容 |
|---|---|
| 任务 | T401（Paddle worker 锁环境）、T402（MinerU 只读映射）、T403（几何矩阵与可逆）、T404（OCR 落格候选）、T405（真实语料评测） |
| 派发 | `dev-geo`（T403+T404）、`dev-mineru`（T402）并行；T401 由 TL 评估环境后决定是否 `INPUT_BLOCKED` |
| 可验收目标 | 坐标往返误差 ≤ 批准 epsilon（1px 初值，需样本校准）；不可逆/越界/NaN 一律拒绝并降级 `PAGE_ONLY`；MinerU 匹配"唯一才绑定"；真实引擎缺失时**明确 `INPUT_BLOCKED`**，不得声称识别通过 |
| 外部阻塞 | OI03（Paddle/权重/信创环境）、OI04（真实脱敏扫描语料）。缺则 T405 `INPUT_BLOCKED`，其余照做 |

### P3 · L05 多级表头、续表与脚注

| 项目 | 内容 |
|---|---|
| 任务 | T501–T504（表头路径、续表候选、脚注 scope、review packet 渲染） |
| 派发 | `dev-table-sem`；T504 单独一人避免与语义文件冲突 |
| 可验收目标 | 同名表头不合并（币种路径区分）；单位/期间冲突 → `SEPARATE`/`NEEDS_REVIEW` 不 JOIN；脚注范围不明 → `UNRESOLVED` 并阻断相关条件发布；`rendered`/`review_context` 不覆盖旧 `parsed_text.md` |
| 依赖 | L03 完成；扫描表语义分支另依赖 L04（未完成则该分支 `NOT_RUN`） |

### P4 · L06 流程图、图表与一次 ROI 复识别

| 项目 | 内容 |
|---|---|
| 任务 | T601–T606 |
| 派发 | `dev-vision`（T601–T605）；T606 由 `qa-verifier` 做盲评框架 |
| 可验收目标 | 唯一事实路由（同一区域不双计数）；箭头方向不靠阅读顺序猜，不明即 `UNRESOLVED`；环保留不删；`exact_value` 仅来自可见标签/原表；ROI 最多 1 轮且预算/超时可回收；数值估读默认禁用 |
| 外部阻塞 | OI03/OI04 → 真实模型能力 `INPUT_BLOCKED`；工程结构校验必须独立成立 |

### P5 · L07 人工纠错、候选证据与依赖失效

| 项目 | 内容 |
|---|---|
| 任务 | T701–T705 |
| 派发 | `dev-review`；T705 E2E 由 `qa-verifier` |
| 可验收目标 | 补丁基版 CAS（并发冲突不 rebase）；`test/replace` 配对与路径白名单；父表头改动 fan-out 重算清单完整；撤权后读取立即拒绝；未连 consumer 不产生假 ACK |
| 依赖 | L02 + L05 + L06 |

### P6 · L08 最终对账、切换、恢复与交付

| 项目 | 内容 |
|---|---|
| 任务 | T801–T806 |
| 派发 | `dev-review`（T801）+ `qa-verifier`（T802/T804/T805）+ `ho-writer`（T806） |
| 可验收目标 | 分母守恒；`LOCAL_READY` 与 `ACTIVE` 分离；切换 CAS + 回退不回滚撤权账本；故障注入恢复成功；19 项按真实覆盖逐项列状态（含 `NOT_RUN`/`INPUT_BLOCKED`） |
| 外部阻塞 | OI07/OI08 → 真实切换 `NOT_CONNECTED`；不宣称银行验收 |

## 3. 每阶段统一 TL 验收清单（不过不提交）

1. `check_loop_diff --loop Lxx --base <入口commit>` → `passed=true`、`errors=[]`；**不扩白名单**，越界即返工或走差异决议。
2. `check_baseline --allow-index-append` → `passed=true`；`sql/`、`specs/001—017`、`triple`、旧 CLI 零改动。
3. 全量 `pytest`：failed 必须为 0；passed 只增不减；skip 数不得增加（新增 skip 需逐条给理由）。
4. 新测试必须含**正例 + 负例**；每条否定式断言必须有对应变异点。
5. 变异复验：每阶段 ≥4 组"变异→FAIL→还原→PASS→md5 复原"，未变异项如实登记。
6. 独立 `db-reviewer` 与 `qa-verifier` 各出一份结论；TL 不代签。
7. 证据写入 `evidence/<Loop>/`，与代码分开提交；提交信息含命令、退出码、数字、缺口。
8. 任一外部输入缺失 → 只停受影响项，登记 `INPUT_BLOCKED`/`NOT_CONNECTED`，不用 Mock 冒充真实。

## 4. 阶段节奏与检查点

- 每阶段固定两次提交：**代码**（范围检查通过后）+ **证据**（控制文档）。
- 每阶段结束 TL 记录：入口 commit、代码 commit、证据 commit、四项数字（passed/failed/skipped/变异）、未决输入。
- 若某阶段返工超过 **2 轮**，TL 缩小该阶段目标（拆成两个更小 Loop 内的任务批次）并登记原因，不降低验收标准。
- 任何需要改共享文件（`contracts.py`/`runner.py`/`cli.py`/`__init__.py`）的需求，一律回 TL 处理并单独提交，不放进 DEV 批次。

## 5. 当前立即执行项

| 序 | 动作 | 负责 | 状态 |
|---|---|---|---|
| 1 | P1 派工：`dev-word`（T301/T302）、`dev-grid`（T303） | tl | **本轮派发** |
| 2 | P0 补审：`db-reviewer` 独立审核 L01+L02 的 DB/存储边界 | tl 派 `db-reviewer` | **本轮派发** |
| 3 | P1 集成：`native_tables.py`（T302 剩余）由 `dev-word` 在 package/grid 就绪后完成 | dev-word | 待前序 |
| 4 | P1 验收：范围+基线+全量+变异 | tl | 待 DEV 回报 |
| 5 | P1 独立 QA | qa-verifier | 待 TL 集成后派发 |
