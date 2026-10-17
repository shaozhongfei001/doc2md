# L00 / T004 · DB_PRECHECK

工作流 `DOC2MD-19CAP`；契约 `DOC2MD-19CAP-V1.0`；Loop `L00`；任务 `T004`。
执行角色（合同标注）：DB。执行人：本会话（与 TL/DEV 为同一执行体，见 §7 诚实性声明）。
时间：2026-10-18。

## 1. 结论

| 检查项 | 结论 |
|---|---|
| 新增层是否直接写数据库 | **否**（设计约束 + 本轮无新代码；约束已可机器校验，见 §5） |
| 是否有 migration | **否**（`sql/` 仍 4 文件，无新增/修改） |
| 旧服务是否被要求写库 | **否**；新增层只准调用只读 API 白名单（§4） |
| 是否存在第二套数据库（SQLite/Redis/队列） | **否**，且合同明文禁止以它们替代文件账本 |
| 零迁移红线（L-3 / D-17）是否成立 | **成立** |
| 本轮是否存在 DB 实测 | **否**（无可用凭据，131 用例 `NOT_EXECUTED`，不计通过） |

## 2. `sql/` 树事实

```
sql/
├── 001_init_schema_v1_1.sql
└── migrations/
    ├── 006_parse_registry_v1_down.sql
    └── 006_parse_registry_v1.sql
```

- 文件数：4（与 001—017 封版状态一致）
- 本轮是否有新增/修改：**无**
- 是否处于 322 受保护文件清单：**是**（`sql/` 4 个文件全部在内）
- 保护核验：`check_baseline.py --allow-index-append` → `passed:true`，`errors:[]`

结论：**零迁移成立**。任何新增表/列都必须先取得 Owner 批准并另立差异决议，本批次未提出此类请求。

## 3. 新增层的存储模型：文件账本，不是数据库

设计口径（`01_SDD.md` §3、§4.1；`09_DECISION_ALIGNMENT.md` D-17）：

| 产物 | 位置 | 原子性手段 |
|---|---|---|
| 来源账本（版本/事件/checkpoint/访问状态/manifest） | `extension_root/sources/<source_key>/generations/<gen>/` + `HEAD.json` | 单源写锁 + staging + fsync + 原子 rename + HEAD 指针 CAS |
| 增强产物（enhancement/manifest/raw/assets/rendered/review_context/dependencies） | `extension_root/objects/by_hash/ab/cd/<sha>/<generation_id>/` | 同上 |
| 补丁 / 复核 / outbox / 发布 / 路由 | `extension_root/patches|reviews|outbox|releases|routes/` | CAS + 不可变 payload + 独立 commit receipt |
| 运行回执 | `extension_root/runs/<attempt_id>/report.json` | 不参与业务身份 |

明确禁止（合同原文）：`不新增MySQL表/列，也不引入第二套数据库…不得暗自加SQLite/Redis/队列替代`。
**如果**后续实测证明文件账本不足，必须先提存储迁移差异决议——本批次不提出。

## 4. 旧服务只读边界（白名单 / 黑名单）

新增层通过 `io/read_bridge.py` 与旧服务交互，只准走下列**只读**入口：

### 4.1 允许（只读）

| 服务 / 函数 | 位置 | 只读依据 |
|---|---|---|
| `ParseRegistryService.list_parse_runs` | `backend/app/services/parse_registry.py:631` | 纯查询 |
| `ParseRegistryService.get_parse_run` | 同上 `:646` | 纯查询 |
| `ParseRegistryService.list_parse_results` | 同上 `:650` | 纯查询 |
| `ParseRegistryService.list_parsed_artifacts` | 同上 `:671` | 纯查询 |
| `SearchService.search` | `backend/app/services/search_service.py:77` | `_assert_select_only()` 在 `:37` 硬拦非 SELECT 与 DML/DDL |
| `load_config` / `ensure_readonly` | `backend/app/core/config.py:59` / `:112` | 只读取配置、只设置只读守卫 |
| 三件套文件读取 | `parsed/by_hash/ab/cd/<sha>/` | 以 `rb` 打开，读后校验 hash 与 manifest 一致 |

### 4.2 禁止（会写库，新层不得调用）

| 方法 | 位置 | 写库证据 |
|---|---|---|
| `ParseRegistryService.register_parse_report` | `:178` | `session.commit()`（`:337` 区间内 3 处 commit） |
| `ParseRegistryService.reconcile_parsed_artifacts` | `:337` | 3 处 `session.commit()` + `session.rollback()` |
| `ParseRegistryService.create_parse_run` | `:431` | 写 `kb_parse_run` |
| `ParseRegistryService.finish_parse_run` | `:448` | 写状态 |
| `ParseRegistryService.fail_parse_run` | `:465` | 写状态 |
| `ParseRegistryService.record_parse_result` | `:480` | 写 `kb_parse_result` |
| `ParseRegistryService.record_parsed_artifact` | `:574` | 写 `kb_parsed_artifact` |
| `EvidenceChainService.build` | `evidence_chain.py:160` | `_upsert_chunk`(`:619`)/`_upsert_evidence`(`:662`) + `session.commit()`(`:212`) |
| `InventoryScannerService` / `DuplicateGovernanceService` / `FileContentVaultService` / `CuratedProjectAssetsService` 的写方法 | `services/*.py` | 均含 `session.commit()`（`grep -c session.commit` 分别为 2/1/2/1） |
| 任何 `models/**` 直接构造 + 写入 | `backend/app/models/` | ORM 写路径 |

**硬约束**：新层不得 import 上表方法；`kb_*` 表在本期**零写入**（`kb_document_chunk`、`kb_evidence`、`kb_parse_*`、`kb_parsed_artifact` 等）。
新证据一律写 `candidate_evidence_id` 到 `extension_root`，不写 `kb_evidence`，不产生 `field_value` 行（D-19）。

## 5. 可机器校验的守卫（L01 起必须落实）

以下为 L01/T102–T103 的实现验收点，本记录先登记判据，避免"设计说了但代码没做"：

| 编号 | 判据 | 校验方式 |
|---|---|---|
| DB-01 | `backend/app/extensions/**` 不出现 `session.add` / `session.commit` / `session.delete` / `INSERT` / `UPDATE` / `DELETE FROM` / `create_engine` | 源码级 grep 断言测试（fail-closed） |
| DB-02 | 不出现 `from app.models` / `import app.models` 的写用途导入 | 同上 |
| DB-03 | 不出现 `sqlite3` / `redis` / `celery` / `pika` / `kafka` 等第二套运行时 | 同上 |
| DB-04 | `sql/` 树文件数恒为 4，且 4 个文件哈希等于基线 | 哈希对照测试 |
| DB-05 | `extension_root` 解析后不在 `sql/`、`raw_vault/`、`parsed/`、`curated/` 及其父/子路径内 | `io/paths.py` 解析测试（含符号链接逃逸负例） |
| DB-06 | 旧服务调用面只含 §4.1 白名单 | 对 `read_bridge.py` 的 import 白名单断言 |

> 这些判据在 L01 之前**尚未实现**，故当前状态为 `PLANNED`，不可标记通过。

## 6. 本轮实际执行的 DB 相关命令

| 命令 | 目的 | 结果 |
|---|---|---|
| `find sql -type f \| wc -l` | 迁移树规模 | `4` |
| `find . -type d -name migrations` | 是否新增迁移目录 | 仅 `./sql/migrations`（既有） |
| `grep -rln "session.add\|session.commit\|INSERT INTO\|UPDATE \|DELETE FROM" backend/app` | 旧服务写点盘点 | 命中 7 个文件（`core/database.py`、`services/parse_registry.py`、`services/duplicate_governance.py`、`services/evidence_chain.py`、`services/inventory_scanner.py`、`services/curated_project_assets.py`、`services/file_content_vault.py`） |
| `grep -rn "def ensure_readonly" backend/app` | 只读守卫存在性 | `core/config.py:112` |
| `mysql -h 127.0.0.1 -u personal_kb -p*** -e "SELECT 1"` | DB 可用性 | `ERROR 1045` 拒绝 |
| `mysql -h 127.0.0.1 -u root -e "SELECT 1"` | 本地免密探测 | `ERROR 1045` 拒绝 |
| `check_baseline.py --allow-index-append` | `sql/` 是否被改 | `passed:true`，`sql/` 无差异 |

未执行：任何 `CREATE DATABASE` / `CREATE USER` / migration 应用 / 数据写入。理由：无凭据、且属于本批次未授权的外部服务副作用。

## 7. 已知缺口

| ID | 缺口 | 影响 | 关闭条件 |
|---|---|---|---|
| DB-GAP-01 | 无可用 MySQL 凭据；131 个旧 DB 用例 `NOT_EXECUTED` | G1 的 DB 分支不可判定 | 提供 `PKB_MYSQL_*`（隔离测试库）或授权创建；随后复跑并逐 ID 记录 |
| DB-GAP-02 | `sql/001_init_schema_v1_1.sql` 与 22 张表结构本轮未做**实际**比对（只做哈希不变校验） | 若后续有人改表结构而未改 SQL，哈希校验也发现不了（仅能发现 SQL 侧改动） | L01 增加"模型定义 ↔ SQL 表/列"静态比对（只读，不连库） |
| DB-GAP-03 | §5 的 6 条守卫尚未实现 | 当前"零 DB 写"仅由**无代码**保证，不构成机制 | L01/T102–T103 实现 + 变异复验 |

## 8. 角色诚实性声明

本记录由与 TL/DEV 相同的本地会话按 DB 审查口径执行，**不是**独立 DB 岗位执行。
因此本记录只能作为**预审材料**（`DB_PRECHECK`），不能代替 L01+ 的 `DB_REVIEW` 结论。
L01 的 `DB_REVIEW` 必须由独立于实现者的执行体完成；无法满足时记 `DB_REVIEW_PENDING`。
