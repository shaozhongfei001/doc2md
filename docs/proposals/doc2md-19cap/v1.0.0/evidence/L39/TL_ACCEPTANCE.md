# L39 验收记录（reviewer 身份策略：机制已建，策略=全部放行）

- 入口：`loop-entry/L39` = `eae0560`
- 代码：`6398f26`
- **状态：`CODE_MERGED`；机制 CLOSED，策略按 Owner D1 保持 `ALLOW_ALL`**

## 1. 这一轮做了什么、没做什么

| 做了 | 没做 |
|---|---|
| 建了 `ReviewerPolicy`（模式 + 白名单 + 正则） | 没有把策略改成 `RESTRICTED` |
| 决策与补丁两条路径统一走同一策略 | 没有声称"reviewer 身份已验证" |
| 未知模式**构造即失败** | 没有用 reviewer 字符串推导权限 |

Owner 的原话是"目前全部放行"。所以 `"---"` **仍然被接受**——这是**决定**，不是遗漏。

## 2. 为什么值得先建机制

因为收紧时只需换策略，不用改调用点。若等到真要收紧才写，会同时改三处：
两个入口、一条错误路径，以及所有依赖"任意字符串都能当 reviewer"的测试。

现在 `RESTRICTED` 分支**已经被执行过**（有用例钉住），不是"写了但没人跑过"的守卫——
这正是 L17 那轮教训：声明了却没被执行过的检查等于没有。

## 3. 四条判据

| 判据 | 用例 | 实测 |
|---|---|---|
| `ALLOW_ALL` 下 `---` 仍被接受 | `test_the_default_policy_accepts_a_placeholder_reviewer` | 通过；`reviewer == "---"` |
| `RESTRICTED` 下非白名单被拒 | `test_a_restricted_policy_refuses_an_unlisted_reviewer` | `E_INPUT_INVALID`，消息含 `RESTRICTED` |
| 白名单/正则命中被接受且 `strip()` | `test_a_restricted_policy_accepts_a_listed_reviewer_and_strips_it` | `"  E12345  "` → `"E12345"` |
| 未知模式构造即失败 | `test_an_unknown_policy_mode_fails_at_construction` | `E_INPUT_INVALID` |

补两条边界：

| 用例 | 断言 |
|---|---|
| 空规则的 `RESTRICTED` | 拒绝所有人（fail-closed，**不是**"无限制"） |
| 任何策略下空白 reviewer | 一律拒绝 |

## 4. 补丁路径同样接上了

`preview_patch` / `apply_patch` 也接受 `policy`，默认 `ALLOW_ALL`：

- 默认：`audit["reviewer"] == "---"`；
- `RESTRICTED`：同一条补丁被拒。

两条路径用**同一个** `ReviewerPolicy`，避免"决策拒了、补丁放了"这种一半执行。

## 5. 变异验证

| 变异 | 结果 |
|---|---|
| 策略恒放行（`if True: return value`） | 1 failed |
| 未知模式不报错（`if False:`） | 1 failed |

还原后 md5 回 `17a9d8ee…`，`test_review_decisions.py` 18 passed。

## 6. 交付说明必须同时写的两句话

1. **机制**：reviewer 可配置白名单/正则，未知策略失败即拒；
2. **现状**：策略为 `ALLOW_ALL`，**审计主体不可信**，`"---"` 之类的占位串仍会写进 `audit.reviewer`。

只写第 1 句会让人以为身份已校验；只写第 2 句会抹掉已经建好的机制。

## 7. 门禁

| 门 | 结果 |
|---|---|
| A1 | `loop-entry/L39..HEAD` **5 个文件**，无未跟踪 |
| B | **1441 passed / 131 skipped**（1433 + 8） |
| C | `passed=true` |

## 8. 剩余

| 编号 | 内容 |
|---|---|
| `L31-FIND-02` | 机制已备，但**策略未收紧**；真正关闭要等 Owner 给出白名单格式并改默认 |
| `L39-LIM-01` | 未接认证上下文（现在拿到的仍是自报字符串） |
| `L39-LIM-02` | 历史记录不回改：已写入的 `"---"` 仍在库里 |
