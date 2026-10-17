# L01 / 变异复验报告（G3 口径，TL 侧抽查）

目的：证明 L01 的关键拒绝断言**不是恒真**。仅"测试全绿"不足以说明守卫有效——如果被测分支从不进入，否定式断言永远成立（本项目历史上出现过多起"假绿"）。
本节对 6 个守卫各做一次单点变异：**变异 → 指定测试必须 FAIL → 还原 → 必须 PASS → 文件 md5 必须复原**。

执行环境：`/tmp/doc2md-l00-venv/bin/python`（3.11.7，click 8.1.8）。
执行方式：备份 → `text.replace` 单点替换（断言锚点唯一）→ 跑单测 → 还原 → 复跑 → 比对 md5。
变异脚本不落库（一次性 shell），锚点与替换内容逐条记录于下表，可照表复现。

## 结果：6/6 CAPTURED

| ID | 变异点（文件） | 变异内容（还原前） | 目标测试 | 变异后 | 还原后 | 前/后 md5 | 判定 |
|---|---|---|---|---|---|---|---|
| M1 | `app/extensions/io/read_bridge.py`<br>`_check_manifest_identity` | `if status != "SUCCESS":` → `if False:` | `test_read_bridge.py::test_non_success_manifest_is_not_a_usable_base` | FAIL (rc=1) | PASS (rc=0) | `3572376a…` / `3572376a…` | **CAPTURED** |
| M2 | `app/extensions/io/read_bridge.py`<br>`_verify_declared` | `if actual != declared:` → `if False:` | `test_read_bridge.py::test_changed_asset_body_is_an_evidence_mismatch` | FAIL (rc=1) | PASS (rc=0) | `3572376a…` / `3572376a…` | **CAPTURED** |
| M3 | `app/extensions/io/paths.py`<br>`validate_relative_path` | `if segment in ("", ".", ".."):` → `if segment in ("", "."):` | `test_read_bridge.py::test_relative_path_guard_rejects_unsafe_forms` | FAIL (rc=1) | PASS (rc=0) | `701aadf4…` / `701aadf4…` | **CAPTURED** |
| M4 | `app/extensions/contracts.py`<br>`Table.grid` | `if expected[r][c] is not None:` → `if False:` | `test_contracts.py::test_overlapping_cell_span_is_rejected` | FAIL (rc=1) | PASS (rc=0) | `533a9583…` / `533a9583…` | **CAPTURED** |
| M5 | `app/extensions/identity.py`<br>`canonical_json` | `sort_keys=True,` → `sort_keys=False,` | `test_contracts.py::test_identity_vectors_reproduce_published_digests` | FAIL (rc=1) | PASS (rc=0) | `1966a995…` / `1966a995…` | **CAPTURED** |
| M6 | `app/extensions/io/artifact_store.py`<br>`commit_generation` | `os.rename(staging, target)` → `target.mkdir(parents=True, exist_ok=True)` | `test_artifact_store.py::test_commit_publishes_the_generation` | FAIL (rc=1) | PASS (rc=0) | `e5f15835…` / `e5f15835…` | **CAPTURED** |

完整 md5（32 位）：

```
artifact_store.py  e5f158359f5349077560ca413a590b40
contracts.py       533a95836338a1551867996ef930ed8f
identity.py        1966a9950f3b506302cbe6be3723a624
paths.py           701aadf4046bee56f5c3db05430f2bab
read_bridge.py     3572376a66c4fec16ee2e9727bab26af
```

## 覆盖到的真实拒绝分支

| 变异 | 被证明有效的规则 | SDD/契约出处 |
|---|---|---|
| M1 | 非 `SUCCESS` 的 manifest 不构成可用基线 | SDD §5 IF03、T105 |
| M2 | 声明摘要与磁盘字节不一致必须 fail-closed，不得降级用缓存 | D-09、§17 |
| M3 | `..` 段必须拒绝（否则可越出 extension_root） | §4.1、T102 |
| M4 | 重叠 span 必须拒绝，不得静默改成合法网格 | PI-F04-04、TC-PI-F04-04-02 |
| M5 | 身份摘要必须使用排序键，否则跨机器不可复现 | IDENTITY_AND_PATHS |
| M6 | 发布必须是一次 rename，不能"建目录再拷"暴露半成品 | §13.1、TC-PI-F03-02-02 |

## 未被本轮变异覆盖的守卫（诚实登记）

以下断言存在但**本轮未做变异**，不得据此宣称已被证明：

- `artifact_store` 的其余 3 个注入点（`after_staging` / `before_pointer` / `after_pointer`）已有断言，但只做了"行为断言"，未做变异；
- `paths.lexical_symlinks` 的"根内符号链接也拒绝"分支；
- `read_bridge` 的 `E_BASE_MISSING`（读取中途 manifest 变化）；
- `contracts` 中 `CorrectionPatch` 路径白名单、`ReleaseManifest` ACK、`ChartPoint` 估读四组；
- `cli` 的退出码 4/6/7（L01 无对应可触发路径，属 L02+ 面）。

建议独立 QA 在 L01 收口前对这些分支各补 1 个变异点（尤其 M-xtra1：`CorrectionPatch` 白名单、M-xtra2：`ReleaseManifest` ACK）。
