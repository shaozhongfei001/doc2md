# L04 验收附记 01：`L04-DEC-01` 触发条件核实 + 桥接交接项

本附记在 L06 收口时一并提交（遵守提交时序：docs 只能在 L04 代码提交之后落，
否则会污染 L06 的 `base..HEAD` 区间）。

## 1. `L04-DEC-01` 的触发条件经实测未成立

`L04-DEC-01` 记的是：asset 型（table/image）绑定必须命中 asset hash，缺 `img_path` 时
即使 caption 指纹唯一也不绑（报 `MISSING_ASSET_HASH`），复核条件为
"锁定版本 `content_list` 对 table 不产 `img_path`"。

DEV 回报该条件在本仓真实产物上不成立，TL 独立核实如下：

```text
docs/demo/pdf-rich-to-text/ir_source/content_list.json
entries: 4 | tables: 1
  img_path: images/8c00400739e1dc839d6597bf004799a59bfd0760fb1b71c2908c2440fedbc664.jpg
  caption:  ['表 1 主要经营指标 （单位： 万元）']
```

**结论：条件未满足，`L04-DEC-01` 维持现状，不需要改代码。**

**适用范围必须同时记录**：这是 `n=1` 张表的样本。它证明

- 该路径可达（真实 table 条目确实带 `img_path`）；
- 当前语料未触发零绑定；

它**不能**证明规模化后不会误伤。因此不把 `L04-DEC-01` 降级为"已验证"，仍按
"当前语料下代价为 0、条件变化时复核"保管。

TL 早前那条"caption 唯一但无 `img_path` → 零绑定"的探针是**构造场景**，真实语料未出现；
该探针保留在 `TL_ACCEPTANCE.md` 第 3 节，作为"规则边界确实存在"的证据，而不是缺陷证据。

## 2. 桥接交接项（不属于 T402 回归）

DEV 报告：`mineru_reader.read_mineru` 不做目录发现，`read_mineru_source(parsed_dir)` /
`NEEDS_BASE_PARSE` 完全未实现。

| 项 | 内容 |
|---|---|
| 事实 | `read_mineru` 只接受已加载的 `content_list` + `middle`，不读 `parsed/<sha>/ir_source/` |
| 归属 | 接目录的入口属桥接层（`io/read_bridge.py` 范围）或后续 Loop，**不在 L04 白名单** |
| 出口码 | 缺失成品用已登记码 `NEEDS_BASE_PARSE` / `E_BASE_MISSING`，**不新造码** |
| 影响 | L06 及后续若需自动发现 MinerU 产物，必须新增该入口；当前不做，不视为 L04 缺陷 |
| 责任 | TL 在 L07/L08 前决定是否补 `read_mineru_source`；若不补，须在交付材料中写明"需外部提供已加载 JSON" |

## 3. 其他确认

- `adapters/__init__.py` 不新建：与 `tables/` 一致使用命名空间包，导入已由 40 例 + 全量验证。
- `L04-GAP-04/05/06` 与"`middle_index` 在真实产物上不可用"维持原登记，DEV 与 TL 结论一致。
- `equation` 永不可绑属已知取舍：若真实样本要求其有坐标，需要引擎侧提供可核文本或显式 ID，
  而不是在本层放开判定。
