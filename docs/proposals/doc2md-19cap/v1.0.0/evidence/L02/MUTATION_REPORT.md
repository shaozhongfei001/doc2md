# L02 / 变异复验报告

口径同 L01：**变异 → 指定测试必须 FAIL → 还原 → 必须 PASS → md5 必须复原**。
执行环境 `/tmp/doc2md-l00-venv/bin/python`（3.11.7）。

## 结果：5/5 CAPTURED

| ID | 变异点 | 变异内容 | 目标测试 | 变异后 | 还原后 | 前/后 md5 | 判定 |
|---|---|---|---|---|---|---|---|
| MB1 | `sources/sync.py` 事件去重 | `if event.event_id in committed:` → `if False:` | `test_source_sync.py::test_duplicate_events_are_counted_and_not_reapplied` | FAIL | PASS | `38b37ed788fd…` / `38b37ed788fd…` | **CAPTURED** |
| MB2 | `sources/access.py` 墓碑守卫 | `if self.is_tombstoned(source_id, object_id):` → `if False:` | `test_source_sync.py::test_stale_upsert_after_revocation_cannot_resurrect_access` | FAIL | PASS | `64aba70e92b9…` / `64aba70e92b9…` | **CAPTURED** |
| MB3 | `sources/reconcile.py` 删除候选门禁 | `if enumeration_complete:` → `if True:` | `test_source_reconcile.py::test_incomplete_enumeration_produces_no_delete_candidates` | FAIL | PASS | `3591a69ca80c…` / `3591a69ca80c…` | **CAPTURED** |
| MB4 | `sources/reconcile.py` 失败分类 | `if raw.get("failed_reason"):` → `if False:` | `test_source_reconcile.py::test_a_failed_object_is_never_dropped_from_the_denominator` | FAIL | PASS | `3591a69ca80c…` / `3591a69ca80c…` | **CAPTURED** |
| MB5 | `sources/access.py` 用途范围 | 用途越界判断 → `if False:` | `test_source_sync.py::test_purpose_outside_the_grant_is_refused` | FAIL | PASS | `64aba70e92b9…` / `64aba70e92b9…` | **CAPTURED** |

完整 md5：

```
sync.py        38b37ed788fd0265d505f87816dfe4bb
access.py      64aba70e92b9e73432cb7171be55d645
reconcile.py   3591a69ca80c491065ee4e1837bd936f
```

## 被证明有效的规则

| 变异 | 规则 | 出处 |
|---|---|---|
| MB1 | 同 event_id 不得重复应用（幂等重放的基础） | SDD §6.2、TC-PI-F03-02-01 |
| MB2 | 墓碑必须阻断后到 UPSERT，不能按到达时间复活 | SDD §6.2、TC-PI-F03-02-03、MU01 |
| MB3 | 未完整枚举不得推断删除 | SDD §6.2、TC-PI-F03-02-02、MU03 |
| MB4 | 失败对象留在分母 | SDD §6.3、TC-PI-F03-03-01 |
| MB5 | purpose 范围外不可读 | SDD §13.2 |

## 未做变异的守卫（如实登记）

- `QUARANTINED`（顺序不可比）与 `STALE_EVENT`（可比较且更新）两条分支的**互斥性**：两者均有断言，但未做"把 QUARANTINED 也当 APPLIED"的变异；
- `RESTORE` 缺少再授权引用 → 拒绝（有断言，未变异）；
- `versioning.detect_revision_conflicts` 与 `authority` 的有效期边界（`now >= valid_to` 与 `now < valid_from`）；
- `local_manifest` 末页追加撤权的位置约束；
- `reconcile.shadow_route_decision` 恒带 `BANK_CUTOVER_NOT_AUTHORISED`。

建议独立 QA 对上述 5 组各补 1 个变异点；其中 `RESTORE` 与 `QUARANTINED` 两组与撤权安全直接相关，优先级最高。
