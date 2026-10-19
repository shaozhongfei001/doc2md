# doc2md

把 Word / PDF / 扫描件转成**可复核、可订正、可追溯**的结构化产物（Markdown + 表格 + 证据链）。

这不是一个"一键转换"工具。它假设产物会被人签字、被机器消费、被审计追问，
所以每个结论都要能回答：**谁说的、依据是什么、错了怎么改、改了影响谁。**

---

## 1. 这个项目解决什么问题

| 场景 | 普通转换器 | 本项目 |
|---|---|---|
| 表格结构 | 输出 Markdown 表格 | 输出 cell 网格 + 合并关系 + 每个单元格的定位证据 |
| 识别不确定 | 尽量猜一个结果 | **不猜**：不确定就标出来，交给人 |
| 复核 | 无 | 生成复核包，且 `omission ⊆ evidence` 可校验 |
| 订正 | 重跑整篇 | 只重建受影响表，其余逐字节不变；产出可回放的 RFC6902 补丁 |
| 改动归属 | 无 | 每次订正必须带 `review_decision_ref`，可追溯到人 |
| 证据作废 | 无 | 订正产生的"应作废清单"可执行，落 append-only 流水，可恢复 |

---

## 2. 当前状态（2026-10-19）

| 项 | 值 |
|---|---|
| 基线 | `main`，**1487 passed / 131 skipped** |
| 主语言 | Python 3.11 |
| 扩展层 | `backend/app/extensions/`（约 40 个模块） |
| 已验证路径 | 原生 `.docx` → 复核包 → 订正 → 补丁 → 作废 → 恢复（**第三方 E2E 8/8**） |
| 未打通路径 | PDF/发票 → 复核包（OCR 侧已能产出契约形状的表，但无复核包、无订正） |

> 数字会变。**以 `pytest` 实际输出为准**，不要引用本文里的计数作为验收依据。

### 快速开始

```bash
# 环境（首次）
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 全量测试（不需要 GPU、不需要语料）
cd backend
PYTHONPATH=. ../.venv/bin/python -m pytest -q -p no:cacheprovider

# CLI（子命令见 python -m app.extensions.cli --help）
PYTHONPATH=. ../.venv/bin/python -m app.extensions.cli enhance --help
```

OCR / 表格识别需要独立环境（Paddle 会钉住 numpy/opencv 版本，**不要装进测试 venv**）：

```bash
python3.11 -m venv /tmp/paddle-gpu-venv
/tmp/paddle-gpu-venv/bin/pip install paddlepaddle-gpu==3.3.1 \
  -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
/tmp/paddle-gpu-venv/bin/pip install "paddleocr==3.7.0" "paddlex[ocr]==3.7.2"
```

---

## 3. 架构

系统有**两条并行的处理路径**，共享复核包与证据层，但入口与判定方式不同。
每条路径都有一张可交互的设计图（自包含 HTML，离线可开）。

### 3.1 文档与表格路径

> 图：[`design.architecture.html`](docs/proposals/doc2md-19cap/v1.0.0/evidence/design.architecture.html)
> （源：[`design.architecture.json`](docs/proposals/doc2md-19cap/v1.0.0/evidence/design.architecture.json)）

```text
.docx ──> word/package ──> tables/ ──> review_packet ──> pipeline ──> artifact_store
  │                          (网格/表头/脚注)    复核包      装配        generation + CAS
  │                                                                      │
  │                                        correction（只重建受影响表） ──┤
  │                                        patches（RFC6902 回放）   ─────┤
  │                                        revocation（应作废清单）  ─────┤
  │                                                                      ▼
  └─> PDF/图片 ──> paddle_structure ──> ocr_tables ──> ocr_assembly ──> 【未接通】
                     (几何校验)        (人工声明表头)   (空值+显式 issue)
```

关键点：

| 节点 | 作用 | 状态 |
|---|---|---|
| `word/package` → `tables/` | 原生表网格、表头路径、脚注绑定 | 已验 |
| `review_packet` | 复核包，`omission ⊆ evidence` 可校验 | 已验（含负向探针） |
| `pipeline` → `artifact_store` | 装配 + 原子提交（CAS） | **第三方 E2E 8/8** |
| `correction` / `patches` | 只重建受影响表；补丁可回放 | 已验 |
| `ocr_tables` / `ocr_assembly` | OCR 表：人工声明表头、缺项显式 issue | 已验 |
| **OCR → `pipeline`** | 还缺 `packets` 与订正链路 | **未接通（图上虚线）** |

### 3.2 图像理解路径

> 图：[`design.vision.architecture.html`](docs/proposals/doc2md-19cap/v1.0.0/evidence/design.vision.architecture.html)
> （源：[`design.vision.architecture.json`](docs/proposals/doc2md-19cap/v1.0.0/evidence/design.vision.architecture.json)）

```text
版面区域 ──> classify ──┬─ 仅 FLOW 类 ──> flow ─────────┐
   ▲          │         └─ 仅 CHART 类 ─> chart ────────┤
   │          │                                          ▼
   │          └─ 低置信/UNKNOWN ─> 人工覆盖 ────────> consistency ──> 复核包
   │                                  (带 ref)        结构/控制流/文图
   └── roi（受控重识别，有预算）<── 需已验证区域
```

五个模块各有一条硬规则：

| 模块 | 职责 | 硬规则 |
|---|---|---|
| `classify` | 区域路由 TABLE/FLOW/CHART/DECORATIVE/UNKNOWN | **框架的 image/chart 标签不能证明 FLOW**；`table`/`chart` 才算直接证据 |
| `flow` | 节点 + 箭头候选 | **不按阅读顺序/距离补边**；边由输入给出，不由坐标发明 |
| `chart` | 图表数值 + 值类型 | 数字未经可见标签或原表核对，**不得标 `PRINTED_LABEL`** |
| `consistency` | 结构 / 控制流 / 文图一致 | 结构错误**阻断确认**；缺 START/END、决策无标注分支；文图不一致只产生复核项 |
| `roi` | 受控重识别 | 无已验证区域则**整页回退**，不凭猜测裁图；有预算上限 |

状态：逻辑经独立抽检（9 变异，M4/M5 缺口已补）；**真实 VLM 未接入、真实图片语料未接入**。

### 3.3 两条路径的关系

| | 文档/表格路径 | 图像理解路径 |
|---|---|---|
| 入口 | `.docx` 包 / Paddle 表格响应 | 版面区域（框） |
| 判定核心 | 网格、表头路径、脚注 | 分区证据、图结构、数值口径 |
| 共同点 | 都产出复核包，都进证据层，都遵守"不猜/不静默/单一事实源" | 同左 |

### 3.4 分层

| 层 | 模块 | 职责 |
|---|---|---|
| 输入 | `word/` | docx 包解析、原生表格、脚注、表头标记 |
| 识别 | `adapters/` | Paddle 响应归一、几何绑定、OCR 表装配 |
| 结构 | `tables/` | 网格、合并、表头路径、续表候选、脚注绑定 |
| 图像 | `vision/` | 分区路由、流程图节点、图表数值、一致性、重识别 |
| 呈现 | `render/` | 复核包（Markdown + 证据） |
| 编排 | `pipeline.py` | 端到端装配、原子写盘 |
| 变更 | `review/` | 影响清单、重算、订正、补丁、决策与 reviewer 策略 |
| 证据 | `evidence/` | 候选证据、作废执行器、标记流水 |
| 交付 | `release/` | 对账、切换、回滚、消费者 ACK |

### 3.5 打开设计图

图是自包含 HTML，双击即可在浏览器打开；也可以起一个临时服务：

```bash
cd docs/proposals/doc2md-19cap/v1.0.0/evidence
python -m http.server 8099
# 然后访问
#   http://127.0.0.1:8099/design.architecture.html
#   http://127.0.0.1:8099/design.vision.architecture.html
```

图由 `archify` 生成（`showcase` 校验通过、四个桌面视口无溢出）。
**图是设计说明，不是验收证据**——验收证据在 `evidence/L*/TL_ACCEPTANCE.md` 与 `evidence/QA/`。

---

## 4. 三条不可协商的设计原则

### 4.1 不猜

- 表头带**不按加粗/首行**推断；原生 `.docx` 读 `w:tblHeader`，OCR 表**必须人工声明**。
- 表格 box 与单元格的对应**不按下标**，按几何逐格比对；对不上就拒绝绑定。
- 作废恢复**不假设**原状态是 `UNREVIEWED`，按流水里记录的值恢复；无记录则拒绝。

> 猜出来的结果最危险的地方不是错，而是**错得看起来合理**。

### 4.2 不静默

孤儿脚注、重复 id、无法归因的证据、只描述一半的补丁、残缺的批次——
全部变成可见的 issue 或报告，**不允许"跳过并继续"**。

典型例子：`notes = []` 读作"这张表没有脚注"；只有配上
`NOTES_UNAVAILABLE_FROM_OCR` 才是"我们没读到脚注"。这两件事业务上完全不同。

### 4.3 单一事实源

状态由事件推导，不另存一份"当前值"：

```text
revocations.jsonl  （只追加，永不改写）
  seq1 REVOKE  at=10:00 recorded_at=10:00:03
  seq2 RESTORE at=11:00 recorded_at=11:00:01
        └── fold ──> 当前状态
```

同一个事实若有两个来源，迟早会对不上——这条项目已经付过学费。

---

## 5. 能力与验证状态

**"机制"与"证据"是两件事。** 下表第三列只写有实测支持的部分。

| 能力 | 入口 | 验证状态 |
|---|---|---|
| 原生表格抽取（合并/空洞/嵌套拒绝） | `word/native_tables.extract_tables` | 已验（含独立变异抽检） |
| 表头路径（cell 引用） | `tables/semantics.derive_header_paths` | 已验 |
| 脚注绑定 | `tables/notes.bind_notes` | 已验 |
| 复核包 `omission ⊆ evidence` | `render/review_packet` | 已验；有负向探针（伪造省略位被拒） |
| 端到端装配 + 原子提交 | `pipeline.assemble_package` | **第三方 E2E 8/8 PASS** |
| 订正 + 只重建受影响表 | `review/correction.apply_correction` | 已验；未受影响表字节不变 |
| RFC6902 补丁 + 不可补丁登记 | 同上 | 已验；回放后逐叶子零残留 |
| 证据作废（人工触发 + 可恢复） | `evidence/revocation*.py` | 已验；双进程 seq 无重 |
| reviewer 身份策略 | `review/authority.ReviewerPolicy` | 形式校验已收紧；**未接认证** |
| Paddle 表格几何绑定 | `adapters/paddle_structure` | 真实票据 **2/17 通过**（安全性成立，可用性不足） |
| OCR 表头人工声明 | `adapters/ocr_tables` | 已验（建议来自 `<th>`，人签署才生效） |
| OCR 装配入口 | `adapters/ocr_assembly` | 已验；缺项显式 issue，**无复核包/无订正** |

### 验收判据（这段比结论更可复用）

| 版本 | 判据 | 防的错 |
|---|---|---|
| v1 | 代码里有这个检查 | 无 |
| v2 | 有变异能让它变红 | 守卫声明了却从未执行 |
| v3 | v2 + 三坐标（变异原文 / 用例 / 失败行） | 判据自身变成声明 |
| **v4** | v3 + 重放必须保留原变异全部语义变化 | 用弱变异体证明强结论 |

---

## 6. 明确"不声称"

以下说法在本仓库**不成立**，交付材料中不得出现：

1. 不声称端到端生产可用（无真实 OCR 环境与真实语料）；
2. 不声称表格解析在真实票据上可用（真实 17 表仅 2 表通过几何校验）；
3. 不声称识别准确率（无人工标注，全部是相对量）；
4. 不声称阈值已校准（`0.6` 偏低，`0.9` 只是候选值）；
5. 不声称 reviewer 身份可信（只校验形式，未接认证上下文）；
6. 不声称证据作废是全自动的（需人工触发）；
7. 不声称跨进程并发安全（单机 `flock`，未并入 `artifact_store` 锁体系）；
8. 不声称 MySQL 相关 131 例已跑（`NOT_EXECUTED`）；
9. 不声称发票/PDF 能进复核包（OCR 侧只到"契约形状的表 + 缺失声明"）。

---

## 7. 仓库结构

```text
backend/
  app/extensions/          # 本项目的核心（下述为其中重点）
    word/                  #   docx 解析、原生表格、脚注、表头标记
    adapters/              #   Paddle 响应归一、几何绑定、OCR 表装配
    tables/                #   网格、表头路径、续表、脚注
    render/                #   复核包
    review/                #   影响清单、重算、订正、补丁、策略
    evidence/              #   候选证据、作废执行器、标记流水
    release/               #   对账、切换、回滚、ACK
    pipeline.py            #   装配与原子写盘
    cli.py                 #   命令行入口
  tests/                   #   扩展层测试（1487 条中占大多数）
docs/proposals/doc2md-19cap/
  v1.0.0/evidence/         #   逐 Loop 验收证据（含勘误）
  v1.0.0/evidence/QA/      #   独立 QA 报告
  v1.0.0/evidence/DATASET_INVOICES/  # 样本清单（不含原始凭证）
loop/proposals/            # Loop 契约（追加式，不改历史）
.ua/                       # 知识图谱（见第 9 节说明）
```

### 证据在哪

| 想知道 | 看 |
|---|---|
| 某个 Loop 验了什么、边界是什么 | `docs/proposals/doc2md-19cap/v1.0.0/evidence/L*/TL_ACCEPTANCE.md` |
| 独立 QA 抓到了什么 | `evidence/QA/` |
| 为什么这么设计（被否掉的方案） | `evidence/TL_DESIGN_SUMMARY_20261019.md` |
| 谁决定了什么（政策层） | `evidence/OWNER_DECISIONS_20261018.md` |
| 未验证清单 | `evidence/DELIVERY_19CAP.md` 第 3/4 节 |
| 样本从哪来、能证明什么 | `evidence/DATASET_INVOICES/README.md` |

---

## 8. 已知缺口（按"出错后多久会被发现"排序）

| 风险 | 为什么危险 | 现状 |
|---|---|---|
| 表格几何绑定真实通过率 2/17 | 结构复杂时会整批失败，且不报错 | 已登记，待真实样本 |
| 阈值 `0.6` 偏低 | 该复核的被放过；不会报错 | 有相对依据，未改常数 |
| OCR 链路断在复核包之前 | "能识别"被误读成"能交付" | 已写进"不声称"第 9 条 |
| 作废流水无权限约束 | 有进程权限即可 append | `L47-LIM-02` |
| 日志只增不减 | 长期膨胀 | `L47-LIM-01` |
| 报销发票 ≠ 银行票据 | 结论域差 | `DS-INV-05` |

---

## 9. 知识图谱（`.ua/`）

`.ua/knowledge-graph.json` 由 `/understand` 流程生成，**当前快照对应 `28fa3d5`**。

| 项 | 值 |
|---|---|
| 规模 | **3511 节点 / 3874 边 / 23 层 / 5 步导览 |
| 范围 | 代码为主（`backend/`、`.cursor/`、`config/`、`sql/` 等）；`docs/` 等 489 个散文文件已排除 |
| 生成方式 | **确定性 AST 抽取**（Python `ast`），`summary` 取 docstring 首行 |
| 未使用 | 未跑 LLM 逐文件语义分析，因此**没有人工撰写级别的摘要** |

为什么是这个形态：仓库 747 个文件里有 341 个是文档，全量 LLM 分析会产出大量"解读散文"的节点，
而图谱的用途是**导航代码结构**。收窄到代码后 258 个文件仍属"large"，故采用确定性抽取保证可复现。

> **图谱用于导航，不作为事实来源。** 事实以代码与测试为准。
> `.ua/` 已被 `.gitignore` 排除，不会进入提交。

重新生成（全 LLM 版，耗时长）：

```bash
/understand --full --language zh
```

## 10. 开发约定

- **改动必须自带证据**：`evidence/L*/TL_ACCEPTANCE.md` 写明判据、命令、实测数字、边界。
- **Loop 契约只追加**：`loop/proposals/` 下不改历史契约。
- **测试基线不可漂移**：新增依赖若会改 numpy/opencv 版本，必须装进独立 venv。
- **不猜、不静默、单一事实源**：见第 4 节；违反其一的设计需要单独记录理由。
- **声称必须能指出反例**：写"两条路径都被钉住"时，必须能说出每条路径的用例名。

---

## 11. 最近完成（摘要）

| Loop | 内容 | 结果 |
|---|---|---|
| L38 | Paddle 表格几何绑定 | 真实响应暴露 `cell_box_list` 形状漂移，已双形状兼容 |
| L39 | reviewer 身份策略 | 机制建成，当时策略为"全部放行" |
| L40 | 证据作废执行器 | 人工触发、幂等、整批原子 |
| L43 | reviewer 合法形式 | 关闭"全部放行"，占位串被拒 |
| L44–L45 | 阈值校准 | 伪 Gold 失败；退化实验显示 `0.6` 偏低，候选 `0.9` |
| L46–L48 | 作废接线 / 可恢复 / 双时间戳 | 清单直达执行器，落 append-only 流水 |
| L49–L50 | OCR 表头人工声明 / 装配入口 | 引擎建议 + 人裁决；缺项显式 issue |

完整历史见 `docs/proposals/doc2md-19cap/v1.0.0/evidence/`。
