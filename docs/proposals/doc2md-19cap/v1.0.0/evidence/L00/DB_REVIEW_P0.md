# P0 · DB_REVIEW（独立复核）

| 项目 | 值 |
|---|---|
| Loop | L00（覆盖 L01 + L02 的数据库/存储边界） |
| 审查角色 | `db-reviewer`（DOC2MD-19CAP 团队成员，**未参与** `backend/app/extensions/**` 任何代码编写） |
| 审核基线 | `2d257e10b14c6e033fe6e798b03355465160021f`（审查时实际 HEAD `597f6ea`，差异仅 2 个 docs 文件） |
| 结论 | **PASS**（含 3 项发现，其中 2 项已立缺陷并修复） |
| 审查方式 | 只读；`git status --porcelain` 审核前后均为空；未修改、未提交 |

## 1. 七条判据结果

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| 1 | `extensions/**` 无 DB 写 API | PASS | `session.add/commit/delete/flush(`、`create_engine(`、`from sqlalchemy` 全无命中；大小写不敏感复核 `insert/update/delete/select/commit/rollback/transaction/cursor` 命中项均为领域词（pointer CAS 的 commit、同步 cursor、`access.py:138` 的 REVOKE/DELETE 事件类型），无 SQL 语句 |
| 2 | 无 ORM 模型导入 | PASS | `app.models` 零出现（含测试） |
| 3 | 无第二套运行时 | PASS | sqlite3/redis/celery/pika/kafka 全无；import 清单仅 stdlib + pydantic + PyYAML |
| 4 | `sql/` 零迁移 | PASS | 4 文件哈希与 `baseline_lock.json` 逐条相等（`001_init_schema_v1_1.sql` `e4f4380e…`、`006_parse_registry_v1.sql` `15c1b249…`、`006_..._down.sql` `64de8252…`、`migrations/.gitkeep` `e3b0c442…`）；成员集合双向一致 |
| 5 | 桥接不 import 旧写服务 | PASS | `read_bridge.py` import 区仅 stdlib + `..`/`.paths`；类名仅出现在 docstring:7-8 |
| 6 | `extension_root` 不与受保护根重叠 | PASS（本条审查时；缺陷见 §2-1） | 仓内配置路径下 `sql`/`sql/migrations`/`raw_vault/x`/`parsed`/`curated`/`"."`/仓根绝对路径全被 `E_CONFIG_ROOT_FORBIDDEN` 拒绝；`extension_out`、`data/ext` 正常接受 |
| 7 | 无网络客户端 | PASS | requests/httpx/urllib.request/socket/aiohttp/http.client 全无；`allow_external_network` 在 `io/config.py:169-171` 被钉死为 false |

必跑命令（审查方实际执行）：`pytest -q tests/extensions` → exit 0，**229 passed / 0 failed / 0 skipped**（分文件 28+62+27+38+21+13+20+20）。

**正面证据**：判据 1/2/3/4/5/7 已由 `tests/extensions/test_contracts.py:589-674` 落成**可执行源码级断言**，不是文档承诺；因此有回归保护，不只靠一次性 grep。

## 2. 发现与处置

### 2-1 FAIL-2026-10-18-01（MAJOR，已修复）

**现象**：`io/paths.py` 的禁用根只以 `base_dir` 为锚。`--extension-config` 可指向仓外文件，此时 `base_dir` 变为该文件所在目录；配合**绝对** `extension_root` 即可指向 `<repo>/sql`。

TL 复现（修复前）：

```
resolve_extension_root("/home/szf/dev/doc2md/sql", base_dir=Path("/tmp"))        → ACCEPTED
resolve_extension_root("/home/szf/dev/doc2md/parsed", base_dir=Path("/tmp"))     → ACCEPTED
resolve_extension_root("/home/szf/dev/doc2md/raw_vault", base_dir=Path("/tmp"))  → ACCEPTED
resolve_extension_root("/home/szf/dev/doc2md", base_dir=Path("/tmp"))            → ACCEPTED
```

**性质**：属"防误配"缺口（能传该 flag 者已具备仓库写权限，非提权），但后果明确——`artifact_store` 的 `.locks/`、`.staging/` 会落进 `sql/`。

**修复**（commit `35d21fb`，只改 L01 自有文件）：`resolve_extension_root` 新增 `project_root` 锚点，禁用根在 `base_dir` 与 `project_root` **双锚定**下双向判定；`extension_root` 不得等于任一锚点；`ExtensionRunner.PROJECT_ROOT` 由 `__file__` 推导（不依赖 cwd）并透传。
**回归**：`test_repo_forbidden_root_is_refused_even_with_an_outside_base_dir`（4 个根参数化）、`test_repo_root_is_refused_even_with_an_outside_base_dir`、`test_repo_anchoring_still_allows_a_legitimate_sibling`、`test_runner_passes_the_repo_anchor_so_the_outside_config_cannot_escape`。

### 2-2 FAIL-2026-10-18-02（MINOR-MAJOR，已修复）

**现象**：`runner.py` 经 `app.core.config.load_config` 读旧配置，该函数无条件解析 `mysql` 段（`app/core/config.py:90-97`），明文口令进入扩展层进程内存（不建连、不发 SQL、无 ORM）；且 `app.yaml` 缺 `mysql` 段会 `KeyError` 而非受控错误。

**修复**（同 commit）：新增 `io/config.load_storage_roots`，只读 `storage.*`，路径解析规则与旧加载器一致，缺 `storage` 段报 `E_CONFIG_INVALID`，**永不读取 mysql 段**。
**副产品**：扩展层不再 import `app.core.config` —— 与旧代码耦合降为零，DB 边界比审查时更干净。
**回归**：`test_storage_loader_never_parses_the_mysql_section`（口令标记串不出现在内存对象中）、`test_storage_loader_works_without_a_mysql_section`、`test_storage_loader_rejects_a_config_without_storage`、`test_extension_layer_no_longer_imports_legacy_config`。

### 2-3 发现 2（留存策略，登记不修）

`read_bridge.snapshot_read_set` 存在写路径（`mkdir` + `shutil.copyfile`），目标经 `assert_writable` 限定在 `extension_root` 内，`parsed/` 源树全程只读。
**含义**：会把三件产物及 asset/ir_source 复制为 `root="EXTENSION"` 的第二份字节树，`extension_root` 会持续增长。
**处置**：DB 边界无损；留存与回收策略在 L07/L08 的清理设计里声明（`01_SDD.md` §4.1 "自动清理只允许本根内超过保留期且未被 manifest 引用的 staging/orphan"），当前**不清理已提交版本**。

### 2-4 发现 4（既有配置卫生，不计入 L01/L02）

`config/app.yaml` 的 `mysql` 段含明文口令（本文不复述、不入库），且该文件**不在** `baseline_lock.json`（lock 内只有 `app.example.yaml`，且 `config/app.yaml` 本就在 `.gitignore` 中）。
**性质**：存量问题，非本工作流引入。**处置**：登记为既有风险；修复需 Owner 决定（改配置形态/接入密钥管理），不在本轮授权内。`FAIL-2026-10-18-02` 的窄加载已消除"扩展层读取该口令"这一条暴露路径。

## 3. 未执行项（审查方声明）

- **未做变异测试**：会修改 `extensions/**` 与 `tests/**`，违反只读约束。建议交 `qa-verifier` 补做，重点变异候选：`test_contracts.py` 的 `DB_WRITE_TOKENS`、`SECOND_STORE_TOKENS`、网络 token 三组断言。
- **未连接 MySQL、未做运行期零写库验证**：无凭据；判据前置为"零 DB 访问"，静态+符号证据已足够。若提供只读 DSN 可另开一次运行期审核。
- **未审 `extensions/**` 之外的旧 `app/**`**：不在 WP-3 输入范围。

## 4. 结论与后续

P0 的 `DB_REVIEW` 由 `PENDING` → **PASS**（以 §2 的两项缺陷修复为前提）。
两项缺陷修复的**复验**由 `db-reviewer` 在 `35d21fb` 上做只读复跑，结果追加于 `DB_REVIEW_P0_RECHECK.md`（若复验 RETURN，本结论不成立）。
变异补验与运行期审核转 `qa-verifier`（P1 独立验收）与后续 DSN 到位的单独批次。
