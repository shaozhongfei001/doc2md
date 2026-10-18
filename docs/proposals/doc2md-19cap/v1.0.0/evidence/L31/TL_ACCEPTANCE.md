# L31 验收记录（ACK 结构校验：崩溃改为显式错误）

- 入口：`loop-entry/L31` = `a0f50d5`
- 代码：`339ec2d`
- **状态：`CODE_MERGED` + fail-closed 不再依赖"抛任意异常"**

## 1. 关闭的是什么

`qa-l22` 第 4 节 B5：

```python
activate(release, required_consumers=["search-index"], acks={"search-index": "ACKED"})
→ AttributeError: 'str' object has no attribute 'get'
activate(..., acks=None)
→ AttributeError: 'NoneType' object has no attribute 'get'
```

**这不是 fail-closed，是崩溃。** 后果分两种，都不好：

- 调用方按错误码分支（只捕 `ExtensionError`）→ 异常直接冒泡，图层拿不到决定；
- 调用方 `except Exception` 兜底 → 可能把崩溃读成"通过"。

## 2. 修法：两种坏结构，两种错误码

| 输入 | 错误码 | 理由 |
|---|---|---|
| `acks` 本身非 Mapping | `E_INPUT_INVALID` | 调用方错误，根本没有 ACK 表 |
| 单个 ack 非 Mapping | `E_CONSUMER_NOT_CONNECTED` | 形状像 ACK 但不可解析 = 消费者没真正接通；`detail.malformed` 列出名称 |

## 3. 用例与变异

| 用例 | 断言 |
|---|---|
| `test_acks_that_are_not_a_mapping_are_an_input_error` | `E_INPUT_INVALID` + "must map consumer name to ACK" |
| `test_an_ack_that_is_not_a_mapping_is_a_missing_consumer` | `E_CONSUMER_NOT_CONNECTED` + `detail["malformed"] == ["search-index"]` |

变异（删掉两处结构校验）→ **两用例都红**，且红因是 `AttributeError: 'str' object has no attribute 'get'`。

这里要说清一个判据细节：一般"红因 AttributeError"要判没钉住，但**本例的断言字面就是"必须抛 `ExtensionError`"**，
而回归的病症正是抛 `AttributeError`。所以这个红是**精确命中**，不是崩溃噪音——
区别在于：断言的对象就是异常类型，而不是某个值。

## 4. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 2 个文件 |
| B | **1407 passed / 131 skipped**（1405 + 2） |
| C | `passed=true` |

## 5. 仍未处理（`qa-l22` 第 4 节其余项）

| 编号 | 内容 | 状态 |
|---|---|---|
| `L31-FIND-01` | L06 无置信度门槛：`engine_confidence=0.05` 与 `0.99` 等价，`None` 也当确定结果 → **静默放行** | 待 L32 |
| `L31-FIND-02` | L07 `reviewer="---"` 被接受并写入审计（身份只校验"是非空字符串"） | **需规格方裁定**：占位串是否算合法主体，属策略问题不是代码问题 |
| `L31-FIND-03` | `engine_label` 缺失 → `UNKNOWN/NEEDS_REVIEW`（显式降级，可接受） | 无需行动 |

## 6. 结论

`E_CONSUMER_NOT_CONNECTED` 现在由**判断**产生，而不是由**异常类型**产生。

`L31-FIND-01` 是三个模块里唯一还存在的"静默放行"，下一 Loop 处理；
`L31-FIND-02` 我不会自己定——它决定的是"谁能签这个字"，属于交付方的授权模型。
