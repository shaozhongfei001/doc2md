# L35 验收记录（纠错声明作废的证据）

- 入口：`loop-entry/L35` = `2390417`
- 代码：`a7fda18`
- **状态：`CODE_MERGED` + L12-LIM-02 余项 CLOSED**

## 1. 关闭的是什么

`L12-LIM-02` 余项：纠错改了 cell，但**候选证据登记表还是旧的**。
产物看起来完整，读到的却是订正前的内容。

## 2. 设计：只声明，不写盘

`correction.py` 保持纯函数，`apply_correction` 返回三组信息：

| 输出 | 含义 |
|---|---|
| `stale_candidate_evidence` | 本次订正**作废**的证据 id，调用方据此 `mark_stale` |
| `unattributable_evidence` | locator 无法归因的 id（**列出，不猜**） |
| `evidence_checked` | 调用方是否提供了候选集（`False` = 这一项没检查，不是"干净"） |

## 3. 归因用定位器，不用文本

```python
if locator.get("cell_id") == cell_id:           # 精确命中
if locator.get("table_id") == table_id and ...  # 表作用域
```

**不比较 quote**：文本相等只是猜测（两条相同字符串未必是同一证据），
而且会漏掉"引用同一单元格但换了写法"的候选。

## 4. 三条边界判断（都写成了用例）

| 情形 | 处理 | 理由 |
|---|---|---|
| 表作用域证据（locator 无 cell）+ cell 订正 | **一并作废** | 它可能引用该表任一 cell；这一侧宁可多作废，不可漏 |
| locator 既无 cell 也无 table | `unattributable` | 既不静默保留（会留旧值），也不静默作废（可能误伤） |
| 未提供候选集 | `evidence_checked=False` | 把"没检查"与"检查过没有受影响"分开 |

## 5. 变异验证

| 变异 | 结果 |
|---|---|
| 全部候选都当 stale | 精确性用例红 |
| 丢弃 `unattributable` | 无法归因用例红 |

## 6. 一个必须记下来的"假通过"

第一次做变异 B（丢弃 `unattributable`）时，测试**通过了**，我差点把它记成"等价变异"。
实际原因是：**变异脚本用了相对路径 `app/...`，而 cwd 是仓库根，脚本抛了 FileNotFoundError，文件根本没改**。

重做（正确路径 + 统计替换次数 `occurrences replaced: 2`）后，测试立刻变红。

教训与整轮一致，只是这次发生在我自己的验证动作上：

> **变异必须证明自己落盘了。** 之后每次变异我都加"替换计数"或 `md5 前后比对`，
> 没有这两样，"没红"和"没改"无法区分。

## 7. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 2 个文件 |
| B | **1422 passed / 131 skipped**（1417 + 5） |
| C | `passed=true` |

## 8. 仍未完成

| 编号 | 内容 |
|---|---|
| `L35-LIM-01` | 归因依赖 `locator.cell_id` / `locator.table_id` 这两个**约定字段**；目前没有 schema 强制，生产侧若不给这两个键，全部候选会落进 `unattributable`（安全但不推进） |
| `L35-LIM-02` | 只声明不执行；`mark_stale` 的调用点还没接线（属 release/纠错流程） |
| `L31-FIND-02` | `reviewer="---"` 仍被接受（授权模型，待 Owner） |

## 9. 字段名澄清与独立抽检结果（2026-10-18）

### 9.1 没有 `correction_declaration` 这个字段

本文第 2 节说"只声明，不写盘"，这里的**"声明"是设计意图的比喻，不是字段名**。
全仓 grep `correction_declaration` 零命中；实际实现是：

| 真实名字 | 含义 |
|---|---|
| `CorrectionOutcome.stale_candidate_evidence` | 本次订正作废的候选证据 id |
| `CorrectionOutcome.unattributable_evidence` | 定位信息不足以归因的候选证据 id |
| `CorrectionOutcome.evidence_checked` | 调用方是否提供了候选清单 |
| `stats["candidate_evidence"] = {checked, stale, unattributable}` | 可回读的同一事实 |

> 用比喻代替符号，会让读者去找一个不存在的名字。此后证据里引用实现，
> 必须引用能被 grep 到的标识符。

### 9.2 抽检结论：2 捕获 / 2 缺口（缺口已在 L37 关闭）

| 变异 | 结果 |
|---|---|
| M2 cell 归因精确到 locator | CAPTURED（`assert 'EV-OTHER' not in (...)`） |
| M3 跨表 table-scoped 证据不被作废 | CAPTURED |
| M4 `checked` 反映真实检查 | CAPTURED |
| M5 无法归因不得静默保留 | CAPTURED（QA 自加） |
| **M1 删 `candidate_evidence_id` 必填** | **NOT_CAPTURED → L37 已补** |
| **M6 删缺 `locator` 的归因分支** | **NOT_CAPTURED → L37 已补** |

成因：夹具 `_candidate()` 永远造出形状完整的候选，两条防线在用例层面不存在。

### 9.3 两条观察

| 编号 | 内容 |
|---|---|
| `L37-OBS-01` | 重复 id 不合并（`stale=('EV-DUP','EV-DUP')`）；**不做去重，由调用方幂等** |
| `L37-OBS-02` | `None` = 未检查、`[]` = 检查过且为空；**调用方不得用 `[]` 当默认值**，否则 `evidence_checked` 会被洗成 True |
