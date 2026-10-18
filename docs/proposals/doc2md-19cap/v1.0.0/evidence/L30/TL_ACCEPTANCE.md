# L30 验收记录（补丁守卫可见性：op 白名单与禁词防线）

- 入口：`loop-entry/L30` = `c3a2939`
- 代码：`e566b92`（只改测试）
- **状态：`CODE_MERGED` + 两条防线可见**

## 1. 来源：第六轮抽检的两条 NOT_CAPTURED

`qa-l22` 在 L07 上注入两个变异，**全量 1396 仍绿**：

| 变异 | 实际后果 |
|---|---|
| M4 `ALLOWED_OPS` 加 `remove` | `remove` 被接受，**`text` 键真的被删除**，且 `changed_paths` 只统计 `replace`——预览里连"改了什么"都看不见 |
| M5 跳过 `FORBIDDEN_FRAGMENTS` | 可把 `locator` 从 `AUDIT-ONLY` 改成 `LEAKED` |

## 2. 两条防线为什么没被钉住

**M4**：旧用例只投递 `add` 一个禁止 op，就假定整张白名单闭合；`remove/move/copy` 从未出现。

**M5**：旧用例的 4 个参数（`/source_sha256`、`/schema_version`、`/tables/0/cells/0/locator`、`/evidence/0/…`）
**全都不匹配白名单正则**——即使禁词检查消失，也会被 "outside the reviewer field whitelist" 拦下。
而断言写的是 `"never patchable" in msg or "outside the reviewer field whitelist" in msg`，
那个 `or` 把"禁词防线"整条洗成恒真。

> `or` 连接两条防线的错误消息 = 两条防线一条都没验。

## 3. 补的断言

| 用例 | 关键设计 |
|---|---|
| `test_only_test_and_replace_are_allowed`（4 参数：add/remove/move/copy） | **每个 op 都带 `value`**，这样唯一可能拒绝它的是 op 白名单，而不是后面的 `'value' not in raw` |
| `test_forbidden_fragment_wins_over_a_whitelisted_path_shape` | path 取 `/tables/0/cells/locator/text`：**命中白名单正则**却含禁词，只有 `FORBIDDEN_FRAGMENTS` 能拒绝；断言只接受 `never patchable` |

## 4. 变异验证（复现 qa-l22 的两个注入）

| 变异 | 结果 |
|---|---|
| `ALLOWED_OPS` 加 `remove` | `test_only_test_and_replace_are_allowed[remove]` @ `:108` **`DID NOT RAISE`** 红 |
| 跳过 `FORBIDDEN_FRAGMENTS` | `:145` 红 —— 实际消息变成 "patch could not be applied: 'locator' is not a valid sequence index" |

第二条的红法值得记：禁用禁词检查后，path 通过白名单，**patch 在应用阶段才失败**。
也就是说：**没有禁词检查，定位器字段会一路走到 jsonpatch 才被偶然挡住**（且若文档里 `cells` 是对象而非数组，就会真的写进去）。
这条防线不是冗余，是唯一一条。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 1 个**测试**文件 |
| B | **1405 passed / 131 skipped**（1401 + 4） |
| C | `passed=true` |

产品代码零改动。

## 6. 结论

两条防线现在各有一条变异能让它变红。

这轮的教训可以压成一句：

> **"测了一个取值"不等于"测了一张表"。**
> 白名单类逻辑必须逐个取值投递；多防线并存时，断言不得用 `or` 合并消息来源。
