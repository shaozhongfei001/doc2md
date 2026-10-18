# L17 验收记录（T1701–T1704，守卫分支可见性）

- 入口：`loop-entry/L17` = `34037e6`
- 代码：`bf95ed6`（只改测试）
- 证据勘误：`9957d54`（L13）、`2c10359`（L16）
- **状态：`CODE_MERGED` + `3/3 缺口 CLOSED` + **独立 QA 已复验**

## 1. 这个 Loop 的来源

不是功能需求，是**独立 QA 抽检**（`qa-l17`）在 L11/L13/L16 上做的 12 个变异里，
**3 个 NOT_CAPTURED**，且**全量 1365 用例均无感**：

| 变异 | 它破坏的保证 | 为什么全绿 |
|---|---|---|
| MB3 `deepcopy` → `dict` | purity（输入不被改写） | 旧用例的 producer **只读不写**，深浅拷贝不可区分 |
| MB5 `if table_index == 0` → `if True` | 孤儿脚注只归首表（L13-FIND-01 修复本体） | 所有用例只修**第一张表**，`False` 分支从未执行 |
| MB10 删 `if residual: raise` | 补丁等价性 fail-closed | `residual` 恒为空，raise 分支不可达 |

## 2. 修法：只加断言，不加功能

| 缺口 | 新增用例 | 断言的落点 |
|---|---|---|
| MB3 | `test_a_producer_that_writes_to_its_parent_cannot_reach_caller_state` | producer 就地写 parent，断言调用方 `state == snapshot` |
| MB5 | `test_an_orphan_note_stays_out_of_a_non_first_table` | **表级** `[1, 0]` + 不出现 `DUPLICATE_NOTE_ID_DEDUPED` |
| MB10 | `test_a_patch_that_fails_to_reconstruct_the_document_is_refused` | monkeypatch `correction.preview_patch`，断言 `E_INPUT_INVALID` + "does not reconstruct" |

两个容易踩错的点，都是 QA 提前指出的：

- **MB5 必须落在表级**。文档级 `len(ids)==1` 在变异后仍然成立——因为 `flat_notes` 之后还有一层按
  `note_id` 去重，会把重复压回 1 条。**断言层级选错，去重会把证据擦掉。**
- **MB10 必须打在 `correction` 模块的全局名上**。`correction.py` 是 `from .patches import preview_patch`，
  打在 `patches.preview_patch` 上不会生效，测试会**假绿**。

## 3. 独立复验（以 QA 结论为准，不采信我的自述）

`qa-l17` 在新克隆（`/tmp/qa-l17/verify`，checkout `bf95ed6`）上复验：

| 项 | 结果 |
|---|---|
| 产品代码是否被改 | `git diff --name-status d87b397 bf95ed6` 只有 2 个测试文件 + 1 个 JSON；两个产品文件 md5 与基线逐字节相同 → **未动产品代码** |
| 未变异基线 | 全量 1368/133；三条新用例**逐条单跑全绿** |
| MB3 注入 | 1 failed，红在 `assert state == snapshot` @ `test_recompute.py:347` |
| MB5 注入 | 1 failed，红在**表级**断言 `assert [1,1] == [1,0]` @ `test_correction.py:818` |
| MB10 注入 | 1 failed，`DID NOT RAISE` @ `test_correction.py:842` |
| 还原 | 每次 `md5` 回基线，末态 diff/untracked 均为 0，全量回 1368/133 |

判定：**三处真钉住**（未变异绿 + 变异红 + 红在它要断言的那一行），不是"跑一遍绿所以钉住了"。

## 4. 一条必须成对表述的判据（QA 要求）

MB10 的新用例是**用 monkeypatch 制造 residual** 的。它证明的是：

> **守卫可达且生效**——一旦 `residual` 非空，函数确实拒绝返回。

它**不**证明"真实补丁链路不会产生 residual"。后者由
`test_the_patch_reconstructs_the_corrected_document`（从外部回放真实补丁）兜底，
且 QA 实测 MB11 一改就有 19 条变红，说明那条兜底很结实。

两句话合起来才是完整判据：**可达性** + **真实路径的外部回放**。只写前一句，等于把"守卫能响"说成"不会出错"。

## 5. 判据本身的修正（本 Loop 真正的产出）

| 旧判据（不充分） | 新判据 |
|---|---|
| 代码里有这个检查 | **有变异能让这个检查变红** |
| 文档里写了"不等则报错" | 有用例证明"不等时确实报错" |

> **声明了却没有被执行过的守卫，等于没有守卫；它只是让代码读起来更安全。**

这类缺陷在评审里最难发现：它同时骗过"看代码的人"（代码里有）和"跑测试的人"（全绿）。

## 6. 覆盖边界（不能因为这次通过就当成全局结论）

| 范围 | 独立抽检状态 |
|---|---|
| L11 重算 / L13 脚注重绑 / L16 补丁链 | ✅ 12 变异，9→12 CAPTURED |
| L05 / L06 / L07 / L08 / L09 / L10 / L12 / L14 / L15 | ❌ **未做独立抽检**（只有作者探针） |

所以本文件支持的说法只有一句：
**"L11/L13/L16 的关键守卫经独立变异抽检确认可被观测到破坏。"**
不能外推成"全部 Loop 已验证"。

## 7. 结论

三处缺口 **CLOSED**，判据升级为"可被变异观测"。

仍然待办：其余 9 个 Loop 的独立抽检；以及此前登记的外部输入类缺口（Paddle 环境、真实语料、MySQL、路由/ACK 接口）——它们不因这次 QA 而改变。
