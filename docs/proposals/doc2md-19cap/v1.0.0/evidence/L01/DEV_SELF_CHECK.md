# L01 / DEV_SELF_CHECK

> 状态口径：本文件是 **DEV 自测回执**，不是独立 QA，也不是 DB_REVIEW 结论。
> `DEV_SELF_TEST=DONE`、`DB_REVIEW=PENDING`、`INDEPENDENT_QA=PENDING`、`E2E=NOT_RUN`。

## 1. 身份和输入

| 项目 | 值 |
|---|---|
| 工作流 / 契约版本 | `DOC2MD-19CAP` / `DOC2MD-19CAP-V1.0`（`DESIGN_CANDIDATE`） |
| Loop / 任务 | `L01` / T101–T106 |
| 分支 | `main`（无独立 worktree；通过**分批提交**隔离范围） |
| L01 入口 commit | `39922a7069bde1de8a80f7eeed01ab14928de8ec` |
| L01 代码 commit | `dfd59fd673eb0b3e162dff87b4ecaee0b7e1d88e` |
| 采用契约位置 | `contracts/doc2md-19cap/v1.0.0/`（34 文件，只读参考） |
| 采用契约哈希 | 见 `adoption/ADOPTED_MANIFEST.json`（逐文件 sha256） |
| 基线锚点 | `a20389ffd3f61baacc51502c10f8a503cac4ca3f`（= 包 `zip_head`） |

## 2. 需求依据

| 来源 | 条款 |
|---|---|
| PI 编号 | L01 在机器合同中映射全部 19 项（`PI-F03-01…04`、`PI-F04-01…09`、`PI-F05-01…06`），因其提供公共契约、桥接与产物仓库底座 |
| SDD | §3 基线与兼容、§4 新产物/标识/版本/状态、§5 输入与调用合同（IF01–IF11）、§13.1 原子提交、§17 read-set 快照 |
| 对象 / schema | `contracts/doc2md-19cap/v1.0.0/schemas/*.schema.json`（12 个根 payload） |
| 接口 | IF03 `resolve_input`（T105）、IF09/IF10 的执行边界（L01 只留薄入口）、退出码契约 `cli_contract.json` |
| TC | L01 无专属 TC；其守卫对应 `TC-PI-F04-04-02`（span 拒绝）、`TC-PI-F03-02-02`（崩溃恢复）、`TC-PI-F04-03-02`（缺坐标不补造）的**前置控制**。逐 TC 结果仍为 `NOT_RUN`（属 L03–L08 执行面） |

## 3. 修改范围

### 3.1 新增（全部在白名单内，18 文件）

| 文件 | 行 | md5(12) | 职责 |
|---|---|---|---|
| `backend/app/extensions/__init__.py` | 117 | `0e7c4658d544` | 命名空间、错误码→退出码映射 |
| `backend/app/extensions/identity.py` | 214 | `1966a9950f3b` | canonical JSON + 6 类稳定 ID |
| `backend/app/extensions/contracts.py` | 951 | `533a95836338` | 12 个封闭字段根契约 |
| `backend/app/extensions/io/__init__.py` | 13 | `33338d41caa3` | io 子包 |
| `backend/app/extensions/io/config.py` | 215 | `69f09cf95abc` | 扩展配置加载（fail-closed） |
| `backend/app/extensions/io/paths.py` | 224 | `701aadf4046b` | 受限 root / 穿越 / 符号链接守卫 |
| `backend/app/extensions/io/read_bridge.py` | 373 | `3572376a66c4` | 三件套只读桥接 + read-set 快照 |
| `backend/app/extensions/io/artifact_store.py` | 477 | `e5f158359f53` | 锁 / staging / 原子提交 / CAS / 恢复 |
| `backend/app/extensions/runner.py` | 306 | `97e9d7fcb2eb` | 薄编排与 dry-run 计划 |
| `backend/app/extensions/cli.py` | 259 | `c5837979269a` | 独立 CLI（11 命令 + 4 旗标） |
| `backend/tests/extensions/conftest.py` | 273 | `8e12b24cf49a` | 合成夹具 |
| `backend/tests/extensions/test_contracts.py` | 674 | `1b71a64c5902` | 契约/身份/边界守卫（62 例） |
| `backend/tests/extensions/test_read_bridge.py` | 326 | `4d6e4f4812b3` | 桥接/路径（38 例） |
| `backend/tests/extensions/test_artifact_store.py` | 412 | `73e2d910e675` | 仓库/故障（28 例） |
| `backend/tests/extensions/test_extension_cli.py` | 377 | `007efcaeedad` | CLI（27 例） |
| `config/doc2md_extensions.example.yaml` | 80 | `1948a9350225` | 扩展配置示例（工程候选值） |
| `backend/requirements-extensions.in` | 21 | `a3008018385c` | 扩展层顶层依赖 |
| `backend/requirements-extensions.lock` | 286 | `3deb32c01ed7` | pip-compile 生成，含 sha256 |

### 3.2 只读依赖（未修改）

`backend/app/**`（旧 46 文件）、`backend/tests/**`（旧 22 文件）、`sql/**`、`specs/**`、`config/app.yaml`、`backend/requirements.txt`、`raw_vault/`、`parsed/`、`curated/`。

### 3.3 禁止改动（已核验未触碰）

`backend/requirements.txt`、`config/app.yaml`、`sql/**`（4 文件哈希一致）、`specs/001—016`、`specs/017`、`.cursor/rules/**`、旧 19 个 CLI 命令。

### 3.4 单文件负责人

L01 由同一执行体完成全部文件（无并发写者），故不存在多人同文件冲突；`read_bridge.py` 的 T102/T105 两部分由同一写者顺序完成，避免双重负责人。

## 4. 开发说明（实现要点与拒绝行为）

### T101 身份与封闭契约

- `canonical_json`：UTF-8、`sort_keys`、紧凑分隔符、显式 `null`、`allow_nan=False`；显式拒绝 `bytes`、非字符串键、`Decimal`（数字事实须以字符串承载）、`NaN/Infinity`。
- 6 类 ID：`source_version_id` / `job_key` / `generation_id` / `object_id` / `candidate_evidence_id` / `source_access_key`，域后缀强制带版本号，防止跨语义复用同一域。
- **逐字节复现包内 `identity_vectors.json` 的 4 个固定向量**，并用子进程（`PYTHONHASHSEED=12345`）证明跨进程稳定。
- 旧 `evidence_uid` v1 用 `legacy_uid_vector.json` 固定向量验证未变（**L-2 红线**）。
- 12 个根契约 `extra="forbid"`；JSON 模式接受 ISO-8601，Python 对象模式保持 strict（`datetime` 必须是真 `datetime`）。**理由**：payload 来自磁盘 JSON，若用对象模式验证会让 `strict` 把合法 ISO 串判错，而放松 `strict` 又会掩盖类型错误。

### T102 路径与配置

- `extension_root` 拒绝：`sql/`、`raw_vault/`、`parsed/`、`curated/`、`.cursor/rules/` 及其**父/子路径**；拒绝仓库根本身；拒绝**任何**符号链接组件（含指向根内的链接——避免校验与使用之间的 TOCTOU 窗口）。
- 相对路径拒绝绝对形式、反斜杠、盘符、`..`、`.`/空段、尾随空格。
- `safe_join` 做**双重**检查：先按词法路径找符号链接组件，再用 `resolve` 后的 containment 判越界。
- 配置 fail-closed：未知顶层键拒绝（拼写错误不能静默关策略）；`policy.numeric_chart_estimates_enabled` 与 `policy.allow_bank_approved_label_with_local_identity`、`release.real_bank_cutover_enabled` 强制为 `false`（OI05/OI06/OI08 未决）；引擎 `enabled: true` 必须同时给出环境锁与权重清单引用。

### T103 原子产物仓库

- 单写者：`flock(LOCK_EX|LOCK_NB)` 跨 fd/进程互斥，锁文件固定在 `<extension_root>/.locks/`。
- 提交：`staging` 内逐文件 `fsync` → 目录 `fsync` → `os.rename` 到不可变 `objects/by_hash/ab/cd/<sha>/<generation>/` → 父目录 `fsync`。
- 幂等：同 `generation_id` + 同 manifest 摘要 → `ALREADY_COMMITTED`；同 ID 异内容 → `E_VERSION_CONFLICT`（不自动 rebase、不覆盖）。
- 指针 CAS：`expected_current` 不符即 `E_VERSION_CONFLICT`；`epoch` 自增；temp+`fsync`+`rename`+父目录 `fsync`。
- 故障注入 5 点；`recover()` 清孤儿 staging、验证已提交世代，**损坏只报告不修复不删除**。
- `load_generation()` 要求 `execution_state == "VALIDATED"` 并逐文件复核，reader 不可能读到半成品。

### T104 CLI 与 runner

- 独立模块 `app.extensions.cli`，`python -m app.extensions.cli` 可用；旧 `app.cli.main` 未改。
- 4 个公共旗标在命令**前后**均可（子解析器用 `SUPPRESS` 默认值，避免覆盖根值）。
- 退出码：0 完成 / 2 输入错误 / 3 依赖缺失 / 4 待复核 / 5 版本冲突 / 6 批次失败 / 7 权限拒绝。
- `--dry-run` 做**真实**只读工作：`enhance` 解析基线与 read-set、算 `job_key`；`source-register` 校验契约并复算身份。**不建目录、不建锁、不写报告、不调模型与网络**；`ArtifactStore` 接 `WriteGuard`，即使未来代码忘记判 dry-run 也会在第一次写入时抛 `E_DRY_RUN_WRITE_BLOCKED`。
- 未实现引擎的命令（9 条）在 dry-run 也返回 3 + `E_NOT_IMPLEMENTED`，并列出所属 Loop；**不打印成功**。
- `job_key` 需要 profile/engine/patchset 摘要；缺锁时如实报 `PROFILE_NOT_CONFIGURED` / `ENGINE_LOCK_NOT_CONFIGURED` 并退出 3，**不伪造摘要**。

### T105 只读桥接

- 复用旧派生规则 `parsed/by_hash/ab/cd/<sha>/`；只以 `rb` 打开。
- 校验链：`status == SUCCESS` → `content_uid/sha256 == 请求 sha` → `output_hash == sha256(parsed_text.md)` → 每个声明 asset / ir_source 复算 sha。
- 缺失/不可解析 → `NEEDS_BASE_PARSE`（exit 3），绝不返回空解析；摘要漂移 → `E_EVIDENCE_MISMATCH`，绝不降级到旧缓存。
- `document_ir.json` 可选：缺失记 `ir_available=false`，不当作错误（017 交付物，本层不依赖）。
- read-set 排序键 `(root, relative_path)`，保证 `job_key` 可复现；manifest 读两次，中途变化 → `E_BASE_MISSING`。
- 快照只写入 `extension_root`，复制后复核每份字节，用于应对旧 `--force` 重建。

## 5. 验证和交付

### 5.1 命令与退出码

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `cd backend && PYTHONPATH=. /tmp/doc2md-l00-venv/bin/python -m pytest -q` | 0 | **641 passed / 0 failed / 133 skipped** |
| 2 | `... -m pytest -q tests/extensions` | 0 | **155 passed** |
| 3 | `<PACK>/tools/check_baseline.py --repo <repo> --allow-index-append` | 0 | `passed=true`，322 文件，`errors=[]` |
| 4 | `<PACK>/tools/check_loop_diff.py --loop L01 --base-commit 39922a7` | 0 | `passed=true`，18 文件在白名单，`errors=[]` |

### 5.2 与基线的对照（同环境、同解释器）

| 指标 | L00 基线（ENV_B） | L01 后 | 变化 |
|---|---|---|---|
| passed | 486 | 641 | +155（全部为新增扩展测试） |
| failed | 0 | 0 | 0 |
| skipped | 133 | 133 | 0（同一批 MySQL/分支态 skip） |

**没有旧用例改变状态**，因此 L01 未引入回归。133 个 skip 仍记为 `NOT_EXECUTED`，不计通过。

### 5.3 变异复验

6/6 `CAPTURED`（变异→FAIL、还原→PASS、md5 复原）。详见 `MUTATION_REPORT.md`。
未做变异的守卫已在该报告 §"未被本轮变异覆盖"逐条诚实登记。

### 5.4 原件与保护文件

| 对象 | 结论 |
|---|---|
| 322 个受保护文件 | 内容哈希全一致（含 `sql/` 4 文件、`backend/requirements.txt`、`config/app.yaml`） |
| `parsed/`（3 文件）与 `raw_vault/`（4 文件） | 未读写（本 Loop 测试全部使用 `tmp_path` 合成夹具） |
| 旧三件套产物 | 未生成、未回写 |
| 旧 `evidence_uid` | 固定向量一致 |
| 旧 CLI 19 命令 | 源码未改 |

### 5.5 样本类别

| 类别 | 本 Loop 是否使用 |
|---|---|
| 合成夹具 | **是**（唯一来源：`tmp_path` 下构造的三件套目录与配置） |
| 真实本行数据 | 否 |
| 真实引擎 | 否 |
| Mock | 否（未使用任何模型/网络替身；桥接测试用真实文件字节） |

### 5.6 缺口与影响

| ID | 缺口 | 影响 | 补验条件 |
|---|---|---|---|
| L01-GAP-01 | 无 MySQL 凭据 | 131 旧 DB 用例 `NOT_EXECUTED`；G1 的 DB 分支不可判定 | 提供 `PKB_MYSQL_*` 或授权建隔离库 |
| L01-GAP-02 | 无真实 OCR/VLM/Chart 引擎与权重 | `enhance` 只能出 plan，无法产出 generation；L03+ 真实引擎验收 `INPUT_BLOCKED` | OI03/OI04 |
| L01-GAP-03 | 无银行源接口 | `source-sync` 及真实来源能力探测不可执行 | OI02 |
| L01-GAP-04 | 5 条 `NOT_IMPLEMENTED` 面属 L02–L08 | 当前 CLI 只能完成 2 条命令的只读计划 | 后续 Loop |
| L01-GAP-05 | `requirements-extensions.lock` 由 pip-compile 在本机 PyPI 镜像生成 | 银行内网需以离线 wheel/私服重放同一命令 | 信创环境验证 |
| L01-GAP-06 | 6 组变异外的守卫未变异 | 不能宣称这些断言已被证明有效 | 独立 QA 补变异（见 MUTATION_REPORT §"未被覆盖"） |

## 6. 交接和停止点

| 项目 | 内容 |
|---|---|
| 接收角色 | **DB**（DB_REVIEW）→ 之后 **独立 QA** |
| 需要证据 | 本文件 + `TEST_RESULTS.json` + `MUTATION_REPORT.md` + 基线/范围检查输出 |
| DB_REVIEW 关注点 | ①`backend/app/extensions/**` 无 DB 写（`test_db01…db03` 已可执行）②`sql/` 树零变化（`test_db04`）③桥接不 import 旧写服务（`test_db06`）④`extension_root` 不重叠受保护根（`test_read_bridge.py` 路径用例） |
| 独立 QA 关注点 | ①独立复跑四条命令并比对数字 ②对 MUTATION_REPORT §"未被覆盖"的分支补变异 ③核对 `requirements-extensions.lock` 可重放性 ④确认"未实现即退出 3"不是空转（可用 M6 类似手法变异 CLI 分支） |
| 允许进入下一阶段的条件 | DB_REVIEW=PASS 且独立 QA 给出结论后，方可派发 L02/L03/L04（彼此不重叠文件，可并行） |
| 当前任务结束边界 | **DEV 侧在 L01 停止**。不进入 L02，不修改任何 L02+ 文件；不改契约语义；不代签 QA |

## 7. 角色诚实性

本回执由与 TL 相同的本地会话按 DEV 自测口径产出，**不是独立 DEV 岗位执行，也不构成独立 QA**。
`DB_REVIEW` 与 `INDEPENDENT_QA` 均保持 `PENDING`；若后续无法满足独立性，应记 `DB_REVIEW_PENDING` / `INDEPENDENT_QA_PENDING`，而不是通过改角色名称制造通过。
