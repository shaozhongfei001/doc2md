# 独立 QA 报告：变异抽检（8 组）

- 工作流：`DOC2MD-19CAP`
- QA 执行：`qa-mutation`（独立实例，**未参与任何被审代码编写**）
- 被审基线：`a921d52`（克隆 `/tmp/qa-verify-19cap`）
- 当时真仓 HEAD：`bb78f1d`（仅多一个 docs 提交；`git diff --stat a921d52..bb78f1d -- backend specs` 为空，代码完全一致）
- 方式：关掉单条防护 → 跑目标用例 → 期望变红 → 还原 → 复跑期望绿
- 结论：**8/8 CAPTURED，0 NOT_CAPTURED**

## 1. 结果

| M# | 被关掉的防护 | 变异后 | 目标用例 | 还原后 |
|---|---|---|---|---|
| M1 | `replace` 必须先有同路径 `test` | rc=1 `DID NOT RAISE` | `test_replace_without_a_preceding_test_is_refused` | rc=0，diff 空 |
| M2 | 只允许 `test`/`replace` | rc=1 `DID NOT RAISE` | `test_only_test_and_replace_are_allowed` | rc=0，diff 空 |
| M3 | 缺 ACK 不得 ACTIVE | rc=1 **`KeyError`** | `test_missing_ack_keeps_local_ready` | rc=0，diff 空 |
| M4 | 回退不得重开撤权内容 | rc=1 `DID NOT RAISE` | `test_rollback_refuses_to_reopen_revoked_content` | rc=0，diff 空 |
| M5 | FAILED 必须留在对账分母 | rc=1 `{'FAILED': 0} != {'FAILED': 1}` | `test_denominator_is_the_full_source_list_even_with_failures` | rc=0，diff 空 |
| M6 | 框架 `image` 不证明 FLOW | rc=1 `'FLOW' == 'UNKNOWN'` | `test_framework_image_never_proves_flow` | rc=0，diff 空 |
| M7 | 未核验 `PRINTED_LABEL` 必须降级 | rc=1 `'PRINTED_LABEL' == 'UNKNOWN'` | `test_unverified_printed_label_is_demoted_not_trusted` | rc=0，diff 空 |
| M8 | 同一对象只允许一轮 ROI | rc=1 `DID NOT RAISE` | `test_second_pass_for_the_same_object_is_refused` | rc=0，diff 空 |

6 个被改文件全部 `git checkout` 还原，工作区 md5 与 `HEAD:<file>` 逐一 MATCH；克隆 `status`/`diff` 为空；真仓未被触碰。

## 2. 三条影响"红得是否可信"的细节

### 2.1 M3：红得理由不对，但规则另有独立覆盖

变异后抛的是 `KeyError: 'search-index'`（`acks[name]` 在守卫被跳过时炸），
**不是**该规则本应抛的 `E_CONSUMER_NOT_CONNECTED`——所以该用例里 `code`/`detail` 两条断言在变异世界里没被执行到。
若日后有人把 `acks[name]` 改成 `acks.get(name, {})`，这条变异会变绿。

**TL 独立复验（`/tmp/m3check`，同一变异）**：同文件的两条兄弟用例在该变异下以正确形态变红：

```text
FAILED test_simulated_ack_never_activates          -> DID NOT RAISE
FAILED test_ack_for_another_generation_is_not_an_ack -> DID NOT RAISE
2 failed, 1 passed
```

结论需要按子分支分开说，不能笼统写"有独立覆盖"：

| 守卫子分支 | 覆盖形态 | 判定 |
|---|---|---|
| `simulated`（模拟 ACK） | `test_simulated_ack_never_activates` 在变异下 `DID NOT RAISE` | **干净独立覆盖** |
| `stale`（旧 generation ACK） | `test_ack_for_another_generation_is_not_an_ack` 在变异下 `DID NOT RAISE` | **干净独立覆盖** |
| `missing`（根本没有 ACK） | 仅 `test_missing_ack_keeps_local_ready` 一条，且变异下先撞 `KeyError` | **单条用例 + 失败形态不干净** |

即：整体规则不是覆盖缺口，但 `missing` 子分支的测试质量偏弱。

**登记 `QA-TEST-01`（待处理，不在本轮修改）**：将 `test_missing_ack_keeps_local_ready` 改为
先断言抛出的是 `ExtensionError` 且 `code == "E_CONSUMER_NOT_CONNECTED"`，再断言 `detail["missing"]`；
或补一条 `acks.get(name, {})` 风格的回归用例，使该子分支在变异下也以 `DID NOT RAISE` 变红。

之所以不就地改：L08 已封版，事后修改其测试会重开已通过的验收结论，须与新 Loop 或独立 ADR 一起处理
（与 `ADR-19CAP-02` 同一处置原则）。

### 2.2 M7：TL 的"冗余防护"假设被证伪

TL 预判 `build_chart` 的契约级校验会兜住，QA 实测否定：

- `build_chart` 的类型/值校验方向是**反的**——只禁止 `ESTIMATED/TREND_ONLY/UNKNOWN` 携带 `exact_value`，
  **从不要求** `PRINTED_LABEL` 必须 `label_verified`；
- `audit_chart` 只查轴/单位/负值，不碰 `value_type`；
- 全仓没有第二处读 `label_verified`。

即：**该降级守卫是单层且承载性的（load-bearing）**，关掉后未核验的 `PRINTED_LABEL` 会直接带 `exact_value` 进入 chart。
这正是它必须被测试守住的原因；测试守住了。

### 2.3 M5：冗余是正向的

FAILED 被剔出分母后，除 `counts['FAILED']` 断言变红，`DENOMINATOR_MISMATCH` 分支同时触发（denominator 3 vs classified 2），`report.clean` 也变 False。
两层独立生效，属"多一道也不会误伤"的冗余。

## 3. 工具侧事故（必须记录）

QA 报告：`replace_in_file` **有 3 次报告"成功"但磁盘未改变**（M2 首次、M4 首次、另一次），
导致 M2 首次跑出 **rc=0 假绿**；其后改用 `grep` + `git diff` 双校验落盘再跑 pytest，重新注入后正确变红。

**纪律**：本仓任何"改了但没变红"的结论，必须先排除"改动没落盘"，再谈覆盖缺口。
TL 自身也按此执行（本轮所有提交前均核对 `git diff --cached --name-only`）。

## 4. 这次 QA 覆盖了什么、没覆盖什么

**覆盖**：6 个模块的 8 条关键防护（补丁白名单与 test 前置、激活/回退安全、对账分母、
区域路由、图表值类型、ROI 轮次预算）。

**未覆盖**：

| 项 | 说明 |
|---|---|
| 功能正确性全量 | 只证明"防护失效会被发现"，不证明业务逻辑全对 |
| 9 个 Loop 的逐项验收 | 仍为 TL 自检，不是独立验收 |
| 真实引擎/银行接口 | 输入缺失，`NOT_RUN` / `NOT_CONNECTED` |
| 人工作为独立方 | QA 是独立**实例**，不是独立**组织**；同一模型/运行时 |

## 5. 状态变更

`INDEPENDENT_QA`：`PENDING` → **`PARTIAL_PASS`**（安全防护面已由非作者实例独立验证；功能面仍未独立验收）。
这一改动不改变 `TL_FINAL_REVIEW` 中 8 项 `CODE_PARTIAL` 的判定——那些是输入/接线缺口，与测试是否守得住无关。
