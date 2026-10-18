# L16 验收记录（T1601–T1604，纠错补丁链）

- 入口：`loop-entry/L16` = `76c5135`
- 代码：`7b82422`
- 合并：`--ff-only` → `main` = `7b82422`
- **状态：`CODE_MERGED` + `L12-LIM-02 补丁部分 CLOSED` + `INDEPENDENT_QA_PENDING`**

## 1. 关闭的是什么

`L12-LIM-02`（补丁部分）：纠错不改补丁链。结果是一次订正**发生了**、新版本**产出了**，
但没有任何东西能把"这次订正"表达成一个 review 可绑定摘要的补丁。

## 2. 门禁

| 门 | 结果 |
|---|---|
| A1 | 等价自检：区间**恰好 2 个文件**，无未跟踪 |
| B | **1365 passed / 133 skipped**（L15 后 1358，+7） |
| C | `passed=true` |
| 关闭判据 | 旧文档 + 补丁 **能重建**出新文档；有 `preview_patch` 实跑为证 |

## 3. 关键设计：补丁从"数据差异"导出，不是从 correction 对象拼

一次订正只改一个字段，但重建会连带改动派生字段。若补丁只写那一个字段，
它会**干净地应用到旧产物上、且一个派生变化都不复现**——这类补丁最危险：它能过 `test`，也能过 `replace`，
唯独不能证明自己等于新文档。

所以流程是：

```
旧文档 ─┐
        ├─ 逐叶子 diff ─ 仅保留 PATCHABLE_PATHS 内的 ─ test+replace ─ preview_patch(CAS)
新文档 ─┘                                    └─ 其余逐条记入 unpatchable
                                                 │
                          等价性自检：旧文档+补丁 ==? 新文档（除 unpatchable）
                                                 │
                                        不等则报错，不返回"半张补丁"
```

## 4. 实测行为

| 场景 | ops | fully_patchable | unpatchable |
|---|---|---|---|
| 订正 cell `text` | 2（test+replace） | ✅ | 空 |
| 订正 cell `row`（白名单外） | 0 | ❌ | `/tables/0/cells/2/row` |
| 订正 `evidence_ids`（禁止片段） | 0 | ❌ | 含 evidence 的路径 |
| 同值订正（无变化） | 0 | ✅ | 空（digest `None`） |
| 订正 + 脚注重绑 | 2 | ✅ | 空（重绑结果确定，无数据差异） |

**同值订正返回 `fully_patchable=True` 而不是 `False`** 是有意的：没有变化就没有不可表达的变化，
把它标成"不可补丁"会让统计里出现假的缺口。

## 5. 仍未关闭

| 编号 | 内容 |
|---|---|
| `L12-LIM-02`（候选证据部分） | 纠错后**候选证据登记表**未随之更新。它走的是 `evidence/candidates.register_candidate`（带副作用的登记），不是纯函数重建，接线需要决定"重绑证据"的归属，超出本 Loop |
| `L16-LIM-01` | 补丁只描述数据差异，**不描述 why**：审计理由在 `review_decision_ref` + `reason`，不在补丁里（这是 patches 层的既有设计，未改） |
| `L16-LIM-02` | 补丁**未落盘**、未接入 release 的 CAS 切换；`apply_patch`→切换仍是两步 |

## 6. 结论

`L12-LIM-02` 的**补丁部分关闭**：一次订正现在能产出一张可校验、可回放、可绑定决策摘要的 RFC6902 补丁，
且它"表达不了什么"是**逐条写出来的**，不是省略掉的。

不能说"纠错链路已闭环"——候选证据未更新（`L12-LIM-02` 余项）、补丁未接入切换（`L16-LIM-02`）。

## 7. 勘误（2026-10-18，由独立 QA 抽检触发）

本文件第 3 节画了这张图：

```
等价性自检：旧文档+补丁 ==? 新文档（除 unpatchable） → 不等则报错，不返回"半张补丁"
```

**这条守卫当时从未被执行过。**

独立 QA（`qa-l17`）把 `preview_patch` 换成"原样返回未变更文档"（模拟补丁丢掉替换），
原实现应抛 `E_INPUT_INVALID`。实测：守卫的 `residual` 在全部用例里恒为空，
raise 分支不可达，因此把 `if residual:` 改成 `if False:` 后**全量 1368→1365 用例全绿**。

已在 **L17** 补 `test_a_patch_that_fails_to_reconstruct_the_document_is_refused`
（monkeypatch 模块级 `preview_patch`，断言 `E_INPUT_INVALID` + "does not reconstruct"），
并按"注入变异必须变红"验证。

### 由此修正的验收判据（本文件的有效性依赖它）

| 旧判据（不充分） | 新判据 |
|---|---|
| 代码里有这个检查 | **有变异能让这个检查变红** |
| 文档里写了"不等则报错" | 有用例证明"不等时确实报错" |

> **声明了却没有被执行过的守卫，等于没有守卫；它只是让代码读起来更安全。**
> 这类缺陷在评审里最危险，因为它同时骗过了"看代码的人"和"跑测试的人"。

本文件其余章节未受影响：主路径（MB11 一改 19 红）的等价性断言是真实有效的。

### 证据三坐标（任何人可重跑复核）

| 坐标 | 内容 |
|---|---|
| 变异 | `correction.py` 的 `_build_patch` 内：`    if residual:` → `    if False:` |
| 变红的用例 | `tests/extensions/test_correction.py::test_a_patch_that_fails_to_reconstruct_the_document_is_refused` |
| 失败位置 | `test_correction.py:842` → `Failed: DID NOT RAISE <class 'app.extensions.ExtensionError'>` |
| 还原校验 | `correction.py` md5 回到 `882e11b3eab69fa506f393983f0ed602`，该用例 1 passed |

重跑方式同 L13。注意：这条用例靠 monkeypatch 制造 `residual`，证明的是**守卫可达且生效**；
真实补丁链路的正确性由 `test_the_patch_reconstructs_the_corrected_document` 外部回放兜底。两句缺一不可。
