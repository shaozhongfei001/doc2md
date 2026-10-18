# L05 验收记录（T501–T504）

- 工作流：`DOC2MD-19CAP`
- 契约版本：`DOC2MD-19CAP-V1.0`（`DESIGN_CANDIDATE`）
- Loop：`L05 多级表头、续表与脚注`
- 覆盖需求：`PI-F04-06`、`PI-F04-07`、`PI-F04-08`
- 入口 commit（`loop-entry/L05`）：`cb0da0cccc2d1d785f4a7f02f747e273ef109853`
- 代码交付：`loop/L05` → `09ba804`（T501）、`835b477`（T502）、`0aadd4d`（T503/T504）、`ced28f1`（探针修复）
- 合并方式：`--ff-only` 快进到 `main`，合并后 `main` = `ced28f191ac88774a8b02836e59026ae9402264d`
- **状态：`CODE_MERGED` + `INDEPENDENT_QA_PENDING`**

> 本记录**不**声明独立 QA 通过。执行者与写码者为同一会话，`INDEPENDENT_QA` 无可用角色，
> 按规则如实标注为 `PENDING`，不以改角色名称的方式制造独立结论。

## 1. 交付物

| 文件 | 任务 | 行数 |
|---|---|---|
| `backend/app/extensions/tables/semantics.py` | T501 表头路径 | 564 |
| `backend/app/extensions/tables/continuation.py` | T502 续表候选 | 402 |
| `backend/app/extensions/tables/notes.py` | T503 脚注绑定 | 367 |
| `backend/app/extensions/render/__init__.py` | T504 子包入口 | 10 |
| `backend/app/extensions/render/review_packet.py` | T504 复核包 | 367 |
| `backend/tests/extensions/test_header_paths.py` | T501 测试 | 341 |
| `backend/tests/extensions/test_continuation.py` | T502 测试 | 279 |
| `backend/tests/extensions/test_notes.py` | T503 测试 | 223 |
| `backend/tests/extensions/test_review_packet.py` | T504 测试 | 223 |

## 2. 门禁实测

| 门 | 命令 | 结果 |
|---|---|---|
| A1 范围 | `check_loop_diff.py --repo /tmp/d19/L05 --loop L05 --base-commit cb0da0cc…` | `passed=true`，`errors=[]`，区间恰好 9 个白名单文件 |
| B 全量 | `pytest -q`（`click==8.1.8` 隔离环境） | **961 passed / 133 skipped**（L03 后基线 869，新增 92） |
| C 受保护 | `check_baseline.py --repo /tmp/d19/L05 --allow-index-append` | `passed=true`，`errors=[]` |
| DB 边界 | 新增 4 个模块 grep `sqlalchemy/pymysql/session\./INSERT/UPDATE/DELETE` | 无命中；无 migration |
| IO 边界 | 新增 4 个模块 grep `open(/write_text/write_bytes/mkdir/shutil` | 无命中；复核包只返回文本，无路径参数可误用 |

## 3. TL 自出负例探针（非 DEV 测试）

| 探针 | 预期 | 实测 |
|---|---|---|
| P1 首行"像表头"但未声明 | 必须不猜 | `NEEDS_REVIEW` + `HEADER_BAND_UNKNOWN`，路径为空 ✓ |
| P2 数据格横跨两个表头分支 | 报 partial | `HEADER_COVERAGE_PARTIAL`，路径为空 ✓ |
| P3 `unit_raw` 与某表头文本相同 | 单位不得进路径 | 路径仍为 `["h0"]`，`unit_raw` 独立保留 ✓ |
| P4 两页单位都未知 | 不误判冲突 | `JOIN_CANDIDATE`（候选，不合并）✓ |
| P5 `scope=UNRESOLVED` 但 `state=CONFIRMED` 的脚注 | 应被拒绝 | **放行并渲染为 CONFIRMED ✗ → 缺陷** |

**P5 是真实缺陷，已修**：复核包是发布载体，放行即等于把"范围不明"变成"已确认条件"，
违反 SDD 9.3"缺少例外范围的相关字段不得发布"。修复见 `ced28f1`，同处补齐
`state` 枚举与 `CONFIRMED` 必须有 `review_decision_ref`（脚注与 cell semantics 两侧），
新增 4 例回归测试。**该缺陷不是自测发现的，是 TL 现场出题发现的**——说明 DEV 自测覆盖仍不完整。

## 4. 已登记缺口（不阻断本 Loop 的契约实现，阻断端到端使用）

| 编号 | 缺口 | 影响 | 处置 |
|---|---|---|---|
| `L05-GAP-01` | **表头带无生产者**：`word/**` 未提取 `w:trPr/w:tblHeader`，`extract_tables` 也不输出该标记 | T501 可调用但无上游输入，端到端不可达 | 需在 L03 范围补 `tblHeader` 输出（属 L03 追加，需 ADR 记录），或在 L05 增适配器（需扩白名单） |
| `L05-GAP-02` | **`page_index` / `column_widths` 无生产者** | T502 只能被测试驱动，真实续页判断不可达 | 依赖 L04 版面/坐标输出，L04 落地后接线 |
| `L05-GAP-03` | **脚注/尾注文本未提取**：`final_view.py` 只把 `footnotes.xml` 当作修订上下文，未产出脚注条目 | T503 只能接受外部传入的候选脚注 | 需在 L03 或 L04 增加脚注抽取 |
| `L05-GAP-04` | 真实 Word 语料 gold（横纵合并/多级表头/脚注样例）未跑 | 结构正确性已测，真实文档语义未验 | 语料到位后补跑，记 `NOT_RUN` |

## 5. 明确未做

- `INDEPENDENT_QA`：无独立角色可用，`PENDING`。
- 变异抽检：未执行（应由独立 QA 在副本上做）。
- 真实语料盲评：`NOT_RUN`（无语料）。
- `graphify/search` 派生文本：SDD 9.1.6 要求附完整路径与已确认限制，属 L08 交付，本 Loop 未做。

## 6. 结论

四个任务（T501–T504）的**契约实现与测试**已完成并合入 `main`，A1/C/全量三门绿，
一个由 TL 探针发现的发布级缺陷已修并回归。**端到端可用性受 3 个上游生产者缺口限制**，
在 `L05-GAP-01/02/03` 关闭前，本 Loop 只能算"可调用"，不能算"已接入流水线"。
