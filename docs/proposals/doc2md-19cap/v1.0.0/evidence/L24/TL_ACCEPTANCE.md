# L24 验收记录（severity 不变式下沉到构造点）

- 入口：`loop-entry/L24` = `0fc7382`
- 代码：`4a62d45`
- **状态：`CODE_MERGED` + 不变式结构化**

## 1. 来源：`qa-l20` 第 5 节的建议

L22 用一条**测试**钉住"非阻断理由必须 INFO"：

```python
assert all(r.severity == "INFO" for r in result.reasons if not r.blocking)
```

QA 指出它只覆盖**这一次 fixture 产出的 reasons**：将来在别的分支（例如 `page_index is None`）新增一个非阻断理由，这条用例不会红。

它给的结构化解法我采纳了：

```python
def __post_init__(self) -> None:
    if not self.blocking and self.severity != "INFO":
        object.__setattr__(self, "severity", "INFO")
```

## 2. 用例（不依赖任何 fixture 分支）

```python
fresh = ComparisonReason(dimension="x", code="A_CODE_THAT_DOES_NOT_EXIST", message="m")
assert fresh.severity == "INFO"      # 只能由构造器规则满足
hard = ComparisonReason(..., blocking=True, severity="HARD")
assert hard.severity == "HARD"       # 不得压平显式硬阻断
```

`code` 取一个模块里根本不存在的值，是为了让这条断言**无法经由任何现有路径通过**——它证明的是构造器，不是某条分支。

## 3. 变异验证

| 变异 | 结果 |
|---|---|
| 删掉 `__post_init__` | 新用例红（`severity` 落回 `HARD`）；还原后 1 passed |

## 4. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间 2 个文件 |
| B | **1388 passed / 131 skipped** |
| C | `passed=true` |

## 5. 环境变更（必须记录，否则后续数字对不上）

机器重启后 `/tmp/doc2md-l00-venv` 消失，已按 `backend/requirements.txt` + `click==8.1.8` 重建，并补装 `opencv-python-headless`。

同一提交 `81710d7` 在新环境下：

| 环境 | 结果 |
|---|---|
| 旧（已毁） | 1385 passed / 133 skipped |
| 新（当前） | **1387 passed / 131 skipped** |

差 2 例来自 `tests/test_image_parser.py`：三条 cv2 用例此前有 2 条被 skip，重建后实际执行并通过。
**代码零改动**（`git status` 为空、HEAD 未变），差异纯属环境能力差异。

此后基线以 **1387 / 131**（L24 后 **1388 / 131**）为准。

## 6. 结论

`severity` 三档从"测试记得检查"变成"调用方无法绕过"。

这条改动很小，但它修的是一个**结构性弱点**：所有"靠测试维持的不变式"，都只覆盖测试走到的分支。
