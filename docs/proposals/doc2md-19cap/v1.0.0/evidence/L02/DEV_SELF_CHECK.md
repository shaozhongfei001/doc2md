# L02 / DEV_SELF_CHECK

> `DEV_SELF_TEST=DONE`、`DB_REVIEW=PENDING`、`INDEPENDENT_QA=PENDING`、`E2E=NOT_RUN`。
> 本文件是自测回执，不是独立 QA。

## 1. 身份和输入

| 项目 | 值 |
|---|---|
| 工作流 / Loop | `DOC2MD-19CAP` / `L02`（T201–T205） |
| L02 入口 commit | `71e4c72c7c3ac24ad3fef16006d51c6fad7b46a9` |
| L02 代码 commit | `39eeb3699b67b83726975feb90a745319b362542` |
| 依赖前置 | L01 `dfd59fd`（契约/桥接/产物仓库），已自测通过 |
| 范围 | `PI-F03-01`、`PI-F03-02`、`PI-F03-03`、`PI-F03-04` |

## 2. 修改范围（12 文件，全部在白名单内）

| 文件 | 行 | md5(12) | 职责 |
|---|---|---|---|
| `backend/app/extensions/sources/__init__.py` | 28 | — | 子包入口与边界声明 |
| `backend/app/extensions/sources/base.py` | 216 | — | 协议、能力、页面契约 |
| `backend/app/extensions/sources/local_manifest.py` | 268 | — | 本地 manifest 连接器 |
| `backend/app/extensions/sources/versioning.py` | 202 | — | 版本登记与冲突隔离 |
| `backend/app/extensions/sources/authority.py` | 205 | — | 权威/许可/有效期 |
| `backend/app/extensions/sources/access.py` | 305 | `64aba70e92b9` | 访问账本、墓碑、租约 |
| `backend/app/extensions/sources/sync.py` | 297 | `38b37ed788fd` | 事件页提交与耐久边界 |
| `backend/app/extensions/sources/reconcile.py` | 236 | `3591a69ca80c` | 分母对账与影子路由 |
| `backend/tests/extensions/test_source_adapter.py` | 21 例 | — | 连接器与页面契约 |
| `backend/tests/extensions/test_source_versioning.py` | 20 例 | — | 版本与权威 |
| `backend/tests/extensions/test_source_sync.py` | 20 例 | — | 提交与访问 |
| `backend/tests/extensions/test_source_reconcile.py` | 13 例 | — | 对账与路由 |

未修改：`backend/app/extensions/__init__.py`（共享错误码表）、L01 任何文件、旧 `app/**`、`sql/**`、`specs/**`、`config/app.yaml`、`backend/requirements.txt`。

### 2.1 一次真实的跨 Loop 纠偏

L02 初版把 4 个新错误码加进共享的 `backend/app/extensions/__init__.py`，`check_loop_diff --loop L02` 随即报 1 项 `OUTSIDE_LOOP_WHITELIST`。
处置：**回退该共享文件**，改用 L01 已登记的等价错误码（`E_ENGINE_UNAVAILABLE` / `E_VERSION_CONFLICT` / `E_SCOPE_DENIED` / `E_INPUT_INVALID`），使 L02 的退出码语义仍正确且不跨 Loop 改共享文件、不申请白名单例外。
这与"不得为消除误报而扩大原代码白名单"的纪律一致。

## 3. 实现要点与拒绝行为

| 任务 | 关键规则 |
|---|---|
| T201 | 能力"声明 + 探测"双轨；`NOT_CONNECTED`/`PROBE_FAILED`/`CONNECTED_WITH_GAPS` 三分；能力缺口显式列出（无撤权声明 → `NO_REVOCATION_REPORTING`）；页面契约拒绝重复 event_id、未知类型、超页、UPSERT 缺版本、"既无 cursor 又未 complete" |
| T202 | 原生修订 ↔ 本地快照**双向互斥**；同源版异摘要 → `E_SOURCE_VERSION_CONFLICT` 隔离；声明 ID 与重算不符 → `E_VERSION_CONFLICT`；同字节不同来源不共享身份与 access_key |
| T203 | 外部/同业必须有 licence；外部永不可 `BANK_EFFECTIVE_RULE`；`BANK_EFFECTIVE_RULE` 需 `BANK_RULE` + 银行授权引用；有效期按注入时钟；无 purpose 不可用；`compare_in_house_and_external` 只报并存、`external_may_override_in_house` 恒 false |
| T204 | 撤权/删除立即生效 + epoch 自增 + 墓碑；旧 UPSERT 后到 → `STALE_EVENT`（可比较且更新）或 `QUARANTINED`（不可比较）；`RESTORE` 无再授权引用 → `QUARANTINED`；租约过期 → `E_ACCESS_STALE`；purpose 越界 → `E_SCOPE_DENIED`；提交失败即无持久化，重放按 event_id 幂等 |
| T205 | 分母 = 源清单，五类计数回加必须等于分母；未完整枚举**禁止**产生删除候选；未连源 → `NOT_CONNECTED` + 零删除；影子路由恒带 `BANK_CUTOVER_NOT_AUTHORISED`，`LOCAL_READY ≠ ACTIVE` |

### 3.1 一处诚实的口径修正

初版 `sync.apply_page` 注释声称"游标只在耐久提交后才前移"，但实现把**含新游标的整份状态**交给 `commit_fn`，游标赋值发生在提交之后仅属内存簿记。
实际保证应为：**提交失败则无任何持久化，调用方保留旧状态，重放按 event_id 幂等**。已按此改写模块 docstring 与行内注释，避免夸大保证（也避免写一条打不红的"假顺序"断言）。

## 4. 验证和交付

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `cd backend && PYTHONPATH=. /tmp/doc2md-l00-venv/bin/python -m pytest -q` | 0 | **715 passed / 0 failed / 133 skipped** |
| 2 | `... -m pytest -q tests/extensions` | 0 | **229 passed**（L01 155 + L02 74） |
| 3 | `check_baseline.py --allow-index-append` | 0 | `passed=true`，`errors=[]` |
| 4 | `check_loop_diff.py --loop L02 --base 71e4c72` | 0 | `passed=true`，12 文件，`errors=[]` |

基线对照：L01 后 641 → 715（+74），failed 恒 0，skipped 恒 133（同一批 MySQL/分支态）。

变异复验 **5/5 CAPTURED**：去重、墓碑防复活、删除候选枚举门禁、失败计入分母、用途范围；md5 全部复原。详见 `MUTATION_REPORT.md`。

原件与保护文件：322 受保护文件哈希一致；`sql/` 4 文件未变；未读写 `parsed/`、`raw_vault/`；测试全部使用 `tmp_path` 合成夹具。

## 5. 缺口

| ID | 缺口 | 影响 |
|---|---|---|
| L02-GAP-01 | 无真实银行源接口（OI02） | 真实增量/撤权/对账 `NOT_CONNECTED`；本地 manifest 仅证明状态机正确 |
| L02-GAP-02 | 无 MySQL | 131 旧 DB 用例 `NOT_EXECUTED` |
| L02-GAP-03 | 无租约真实来源 | `lease_expires_at` 仅由合成事件注入验证 |
| L02-GAP-04 | 删除检测仅 manifest 显式声明 | `reports_deletions` 在无 `deleted` 键时为 false，已如实上报为能力缺口 |

## 6. 交接和停止点

接收角色：DB（DB_REVIEW）→ 独立 QA。
DB 关注点：本批**零** DB 接触（无 import、无 SQL）；`extension_root` 仍为唯一写入根。
QA 关注点：①独立复跑四命令 ②对 `QUARANTINED`/`STALE_EVENT` 两分支与 `compare_in_house_and_external` 补变异 ③确认"未连源不产生删除"不是空转（可用 MB3 手法）。
**停止点**：DEV 在 L02 结束，不进入 L03/L04，不改契约语义，不代签 QA。
