# L43 验收记录（reviewer 合法形式，关闭"全部放行"）

- 入口：`loop-entry/L43`
- 代码：`fa6f4e0`
- **状态：`CODE_MERGED` + `L31-FIND-02` 关闭（策略收紧）**

## 1. 决定与实现

Owner 2026-10-19：**采用合法形式，关闭全部放行**。

默认策略从 `ALLOW_ALL` 改为 `RESTRICTED`，并给出默认形式：

```text
[0-9A-Za-z_\u4e00-\u9fff][0-9A-Za-z_\u4e00-\u9fff.@-]{1,63}
```

| 规则 | 理由 |
|---|---|
| 首字符必须是字母/数字/下划线/汉字 | `---`、`???`、`***` 这类占位串**自然不匹配**，不需要黑名单 |
| 长度 2–64 | 排除单字符占位，挡住异常长串 |
| 允许 `. @ -` | `ops@example.com`、`reviewer.1` 这类真实标识 |
| 允许汉字 | **审阅人是人，不只是系统账号**——只写 ASCII 规则会把中文名挡在门外 |

## 2. 旧用例为什么被翻转，而不是被删

有两条用例原本明确断言"占位串被接受"，而且注释里写了理由：

> This test asserts the **current owner decision**, not a desirable end state.

现在决定变了，它们**必须变红**，然后改写为新决定。这是判据起作用的证据：
如果当初写成"任意字符串都能当 reviewer"的模糊断言，收紧时就不会有任何东西提醒你。

| 用例 | 之前 | 现在 |
|---|---|---|
| `test_the_default_policy_accepts_a_placeholder_reviewer` | 断言接受 `---` | 改名 `refuses`，断言 `E_INPUT_INVALID` |
| `test_the_patch_path_keeps_allowing_a_placeholder_reviewer_by_default` | 同上 | 改名 `refuses` |
| `test_a_restricted_policy_with_no_rules_denies_everyone` | 用默认构造 | 显式传 `pattern=None`（默认已带 pattern，否则测的是相反的事） |

新增：

| 用例 | 断言 |
|---|---|
| `test_the_default_policy_accepts_a_plausible_identity` | `E12345` / `张三` / `reviewer.1` / `ops@example.com` 全部通过 |
| `test_the_patch_path_accepts_a_plausible_reviewer_by_default` | 补丁路径同样接受并写入 `audit.reviewer` |

## 3. 变异验证

把默认 `mode` 改回 `ALLOW_ALL` → **2 failed**：

```text
test_the_default_policy_refuses_a_placeholder_reviewer
test_the_patch_path_refuses_a_placeholder_reviewer_by_default
```

还原后 md5 回 `6483b31c…`，20 passed。

## 4. 影响（必须写进交付材料）

| 影响 | 说明 |
|---|---|
| 历史记录 | 已写入的 `"---"` **不回改**，仍留在库里 |
| 新的提交 | 占位串会被拒；必须给真实标识 |
| 逃生口 | `ReviewerPolicy(mode=ALLOW_ALL)` 仍可显式使用，但**不再是默认** |
| 未解决 | 仍拿不到"认证上下文"，只校验形式；`L39-LIM-01` 继续 OPEN |

**"形式合法" ≠ "身份可信"**：`张三` 是一个合法形式，但系统无法证明提交者就是张三。
这句必须保留，否则这轮会被读成"身份验证已完成"。

## 5. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L43..HEAD` **3 个文件**（authority + 2 测试） |
| B | **1453 passed / 131 skipped**（1451 + 2） |
| C | `passed=true` |
