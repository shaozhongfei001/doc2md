# DOC2MD-19CAP 交付说明（本地可交付范围）

- 交付树：`main` = `c529521`
- 环境基线：**1396 passed / 131 skipped**
- 交付 tag：`delivery/19cap-l09` … `delivery/19cap-l28`、`delivery/19cap-local-ready`
- 本文档只陈述**已验证**的事实；未验证项在第 3、4 节逐条列出，不作推定。

## 1. 交付了什么

Word（`.docx`）**原生表格**路径的扩展层，端到端可跑：

```
.docx ──> 表格网格/合并/空洞 ──> 表头路径 ──> 脚注绑定 ──> 复核包 ──> 新 generation（原子提交）
                                     ↑                  ↑
                                续表候选（仅候选）   纠错重算（订正一格 → 只重建受影响表）
```

| 能力 | 入口 | 状态 |
|---|---|---|
| 原生表格抽取（合并/空洞/嵌套拒绝） | `word/native_tables.extract_tables` | 已验 |
| 表头路径（cell 引用，不猜表头带） | `tables/semantics.derive_header_paths` | 已验 |
| 续表候选（确认前不合并） | `tables/continuation.propose_continuation` | 已验 |
| 脚注绑定（scope 必须可证） | `tables/notes.bind_notes` | 已验 |
| 复核包（含 `omission ⊆ evidence`） | `render/review_packet.build_review_packet` | 已验 |
| 端到端装配 + 原子写盘 | `pipeline.assemble_package` / `write_assembly` | 已验 |
| 影响清单 → 真实重算 | `review/recompute.recompute` | 已验 |
| 单次可归属订正 → 新版本 | `review/correction.apply_correction` | 已验 |
| RFC6902 补丁 + 不可补丁差异登记 | 同上（`patch` / `unpatchable`） | 已验 |

## 2. 验证状态矩阵（这是本文档最重要的表）

| Loop | 内容 | 独立变异抽检 | 承担者 |
|---|---|---|---|
| L05 | 表头路径/续表/脚注/复核包 | ✅ 8 变异 | qa-l20 |
| L09 | 脚注抽取 / 表头带 | ✅ 8 变异 | qa-l18 |
| L10 | 端到端装配 | ✅ 同上 | qa-l18 |
| L11 | 重算执行器 | ✅ 4 变异 | qa-l17 |
| L12 | 纠错接线 | ✅ 8 变异 | qa-l19 |
| L13 | 脚注重绑 | ✅ 4 变异 | qa-l17 |
| L14 | 孤儿脚注归属 | ✅ 同上 | qa-l19 |
| L15 | 无表文档 fail-closed | ✅ 同上 | qa-l19 |
| L16 | 纠错补丁链 | ✅ 4 变异 | qa-l17 |
| L23–L27 | 证据已知集/结构证据 | ✅ 7 变异 + 4 轮复核 | qa-l21 |
| **L06** | 图像分类 / 流程图 / 图文一致 / 统计图 | ❌ **未抽检** | — |
| **L07** | 候选证据 / RFC6902+CAS / outbox | ❌ **未抽检** | — |
| **L08** | 对账 / 切换 / 恢复 / 交付 | ❌ **未抽检** | — |

共 **43 个变异**注入并复核，另有 4 轮"复验即发现新缺口"的记录。

**L06/L07/L08 为什么没抽检**：三者都依赖本机没有的外部输入（VLM/GPU、真实 consumer 接口、切换授权）。
对 mock 做变异抽检只能验证"对着假数据的逻辑"，会产出"已验证"的错觉——比不抽检更危险。

## 3. 不可验证项（外部输入缺口）

| 缺口 | 影响的验收项 | 处置 |
|---|---|---|
| 无 PaddleOCR / MinerU / 权重 | T401/T402/T405 实测、适配器 schema 漂移 | 适配器 fail-closed + `NOT_RUN`，**未标完成** |
| 无真实扫描语料 | 坐标精度、OCR 对齐阈值校准（0.8/0.15 为未校准起点值） | 结构正确性已验，精度 `NOT_VERIFIED` |
| 无 MySQL 凭据 | 131 例（`test_parser_router` / `test_parse_job_registry`） | 既不判 PASS 也不判 FAIL，`NOT_EXECUTED` |
| 无真实源 / consumer / 切换授权 | L08 对账与切换联调 | 分项 `NOT_CONNECTED` |

## 4. 明确"不声称"的清单

以下说法**不成立**，交付材料中不得出现：

1. 不声称"端到端生产可用"——真实 OCR 引擎与扫描语料未验证；
2. 不声称"全部 Loop 已验证"——L06/L07/L08 未做独立抽检；
3. 不声称"纠错后全链路自动一致"——候选证据登记表未随纠错更新（`L12-LIM-02` 余项）；
4. 不声称"脚注全支持"——无表格文档的脚注仍为拒绝（`L15-LIM-01`）；
5. 不声称"坐标精度达标"——无真实扫描语料；
6. 不声称"Word 脚注抽取无遗漏"——fail-closed 的前提是抽取不漏检（`L15-LIM-02`）。

## 5. 工程约束（本文档唯一带有规范性质的部分）

四轮返工（L23 → L25 → L26 → L27）的根因不是四个 bug，而是**同一事实被推导两次、且两次之间没有断言**。
由此形成的约束，采用 QA 建议的三档版本（原始"禁止第二套实现"的字面写法会误伤性能路径与多输入域）：

> **同一个事实只允许一个权威推导源。** 若必须存在第二套推导，只能是以下之一：
> 1. **显式拒绝** —— 第二套根本不必存在时（删除降级路径即此档）；
> 2. **交叉校验** —— 第二套必须存在时（覆盖另一输入域、或性能路径），必须有一条断言把两者输出绑死。
>
> 禁止的只有第三种：**静默降级** —— 无提示地让第二套产出与第一套不同的事实。

配两条判定问句（比原则本身更好用）：

1. **这套实现产出的是同一个事实，还是不同输入域各自的事实？** 前者必须收敛，后者允许并存。
2. **有没有一个夹具能区分这两套？** 一个都无法区分的第二套就是负债，必须删除。

现有正面样例：`test_recomputed_structure_matches_the_extractor_record`（`recomputed == recorded`），
两套推导被一条断言绑死，因此是资产而不是负债。

**反面样例（本仓库真实发生过）**：`L23` 把"已知证据集"实现成注释子集，与提取器口径并存且无交叉校验，
结果是合法表格被拒；`L25` 修它以 `cells` 反推，又因同样理由漏掉整行、造出幽灵 id。

## 6. 复现方法（与门禁）

```bash
# 环境（每次重建后需装 opencv 才能复现本基线）
python -m venv /tmp/doc2md-l00-venv
/tmp/doc2md-l00-venv/bin/python -m pip install -r backend/requirements.txt "click==8.1.8" opencv-python-headless

# 全量
cd backend && PYTHONPATH=. /tmp/doc2md-l00-venv/bin/python -m pytest -q -p no:cacheprovider
# 期望 1396 passed / 131 skipped

# 受保护文件哈希
/home/szf/anaconda3/bin/python <PACK>/tools/check_baseline.py --repo . --allow-index-append
# 期望 passed=true
```

**环境注意**：`/tmp` 下的 venv 掉电即失。同一提交在旧环境是 1385/133、新环境是 1387/131，
差 2 例是 `tests/test_image_parser.py` 的三条 cv2 用例此前有 2 条被 skip。**数字差异来自环境能力，不是代码回归。**

## 7. 仍未关闭的登记项

| 编号 | 内容 |
|---|---|
| `L05-GAP-01/02/03` | 表头带、`page_index`/列宽、脚注文本三个上游生产者缺失（本层可调用、未接线） |
| `L12-LIM-02`（余项） | 候选证据登记表未随纠错更新 |
| `L16-LIM-01/02` | 补丁不含 why；补丁未接入 release 的 CAS 切换 |
| `L19-LIM-01` | 严格类型比较对 `tuple`/`float` 的边界（落点均非审阅字段） |
| `L20-LIM-01/02` | 英文续页词缀未覆盖；表名降级依赖复核环节真实存在 |
| `L26-LIM-02` | 交叉校验用 `omitted_slots` 作 oracle——**仅测试**，生产不得引用 |
| `L11-LIM-01/02/03` | 重算未接入 pipeline 默认路径、producer 需显式注册、不做全量重解析 |

## 8. 结论

**可交付**的是：Word 原生表格路径的扩展层，端到端可跑、门禁可复现、
关键守卫经独立变异抽检确认"可被观测到破坏"。

**不可交付**的是：真实 OCR 引擎/扫描语料下的精度与适配、MySQL 相关 131 例、L06/L07/L08 的联调能力。

这两句必须一起出现——只写前一句是夸大，只写后一句是抹掉已完成的工作。
