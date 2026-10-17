# L00 / T003 · SELECTION_RECORD

工作流 `DOC2MD-19CAP`；契约 `DOC2MD-19CAP-V1.0`；Loop `L00`；任务 `T003`。
执行角色（合同标注）：TL。执行人：本会话。时间：2026-10-18。

本记录锁定：**新目录 / 写入根 / 来源范围 / 017 只读的 work-flow 选择**，并核对 SPEC_INDEX 追加结果。

## 1. 选定工作流与本批范围

| 项目 | 值 |
|---|---|
| 工作流标识 | `DOC2MD-19CAP` |
| 契约版本 | `DOC2MD-19CAP-V1.0`（包状态 `DESIGN_CANDIDATE`） |
| 正式 Spec 编号 | **未分配**（不占用 018；`specs/SPEC_INDEX.md` 以工作流标识登记） |
| 需求范围 | 19 项：`PI-F03-01…04`、`PI-F04-01…09`、`PI-F05-01…06` |
| 不进入本批范围 | 四智能体上层应用、GraphRAG/LLM Wiki 运行平台、银行 IAM、授信审批、自动对客承诺、WeKnora 服务/DB/队列、其他项目技术基线 |
| Loop 计划 | L00—L08，共 45 任务，初始全部 `PLANNED`/`TODO` |

范围来源：用户本次施工指令 + `inputs/workbook_selected_19_rows.json`（仅选中的 19 行，非整本工作簿）。
逐项追溯：`04_TRACEABILITY.csv` / `loop/proposals/doc2md-19cap/v1.0.0/requirements_trace.json`（63 条 TC，全部 `NOT_RUN`）。

## 2. 目录落位（唯一工作副本）

| 内容 | 工作位置 | 数量 | 性质 |
|---|---|---|---|
| 主设计 / Loop / 差异 / 追溯 / 验收 / 验证 | `docs/proposals/doc2md-19cap/v1.0.0/` | 12 | 可编辑工作副本 |
| 来源锁与设计输入 | `docs/proposals/doc2md-19cap/v1.0.0/inputs/` | 3 | 可编辑工作副本（内容冻结语义） |
| Spec 五件套 | `specs/proposals/doc2md-19cap-v1.0.0/` | 6 | 可编辑工作副本 |
| 字段 schema / 接口 / 身份规则 / 样例 | `contracts/doc2md-19cap/v1.0.0/` | 34 | **参考契约，只读**（不作业务代码目录） |
| 机器可读 Loop / 任务 / 测试 / 追溯 | `loop/proposals/doc2md-19cap/v1.0.0/` | 6 | 可编辑工作副本（语义变更需差异决议 + 升版） |
| 采用登记 / 路径映射 / 执行证据 | `docs/proposals/doc2md-19cap/v1.0.0/adoption/`、`.../evidence/` | 本轮生成 | 记录 |
| 原包只读快照 | `docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017/` | 71 | **只读，不编辑** |

来源与工作副本的逐文件前后哈希：`adoption/PATH_MAP.json`（71 条）；采用后清单：`adoption/ADOPTED_MANIFEST.json`（61 条）。
路径调整共 7 处（6 文件），逐项登记于 `adoption/ADOPT-PATH-01.md`，并已逐一验证目标存在（`BROKEN=0`）。

## 3. 写入根限制（新层）

`extension_root` 为**新增层唯一写入根**，本轮选定默认值：

| 项目 | 值 |
|---|---|
| 默认配置 | `config/doc2md_extensions.example.yaml` → `extension_root: ./extension_data` |
| 实际写入根 | 由该配置解析（L01 实现 `io/paths.py` 时校验），仓库内**当前不存在** `extension_data/` |
| 禁止写入根 | `sql/`、`raw_vault/`、`parsed/`、`curated/`、`.cursor/rules/`（机器合同 `forbidden_write_roots`） |
| 禁止改动的既有文件 | `backend/requirements.txt`、`config/app.yaml`（机器合同 `forbidden_existing_paths`） |
| 额外禁止 | `extension_root` 不得配置为任何源目录、`raw_vault`、`parsed`、`curated` 本身或其父/子路径；拒绝符号链接逃逸、绝对路径与 `..` |

核验（本轮实际执行）：

| 检查 | 结果 |
|---|---|
| 仓库内是否存在 `extension_data/` | 否（`git status` 干净，无该路径） |
| `forbidden_existing_paths` 是否被改动 | 否（在 322 受保护文件清单内，`check_baseline --allow-index-append` = `passed:true`） |
| `forbidden_write_roots` 是否有新增改动 | 否（`sql/` 4 文件、`raw_vault/` 4 文件、`parsed/` 3 文件均未改） |

## 4. 017 与既有封版契约：只读

| 对象 | 处置 | 证据 |
|---|---|---|
| `specs/001`—`specs/016` | 只读，不改 | 受保护清单内，哈希一致 |
| `specs/017-unified-document-ir` | **状态保持 `DESIGN REVIEWED`**，不标 DONE，不改其剩余任务/裁定/验收 | SPEC_INDEX 追加段 §7.2 明示 |
| `docs/architecture/DOCUMENT_IR_DESIGN_V1.md` | 只读 | 受保护清单内 |
| IR 1.0 契约 | 不新增 `cell bbox` / 格级 `confidence` / `header_path` / 流程节点 / 脚注等键；新内容走独立 sidecar | `contracts/doc2md-19cap/v1.0.0/schemas/document-enhancement.schema.json` 独立命名空间 |
| 三件套（`parsed_text.md` / `parsed_metadata.json` / `parse_manifest.json`） | 零改动、零回写；只读桥接 | `read_bridge` 设计为 `rb` 只读 + 哈希校验 |
| `evidence_uid` v1 公式 | 不变；新证据叫 `candidate_evidence_id`，不写 `kb_evidence` | `contracts/doc2md-19cap/v1.0.0/legacy_uid_vector.json` 固定向量 |
| 旧 19 个 CLI 命令 | 原样保留；新入口 `python -m app.extensions.cli` 独立 | `contracts/doc2md-19cap/v1.0.0/cli_contract.json`（`old_cli_modified:false`） |
| `sql/**` | 零迁移、零新增表/列 | `sql/` 仍为 4 文件（`001_init_schema_v1_1.sql` + `migrations/006_*_up/down`） |

## 5. SPEC_INDEX 追加核对

追加方式：**纯字节追加**（`after[:len(before)] == before` 已验证为 `True`），未改动任何既有行。

| 项目 | 值 |
|---|---|
| 追加前 SHA-256 | `2f4afc23b0b479935b8a4eefd64e5268aadf5349ffc976c67a7dedd970c8aaea` |
| 追加后 SHA-256 | `e60f106dd321ac3b587485833cad60bea6a7618ec20ab499779ac77a7174a69f` |
| 追加前 md5 | `8bef1aec0a188cd9e0dedbb6db69ffb1` |
| 行数 | 374 → 437 |
| 追加内容位置 | 文件末尾新增 `## 7. Workflow Selection Record — DOC2MD-19CAP (Append Only)` 起 63 行 |
| 必需标记 | 含 `DOC2MD-19CAP`（机器合同 `scoped_existing_edits[0].required_marker`）✓ |
| 基线工具判定 | 不加 `--allow-index-append` → `passed:false`（`PROTECTED_BYTES_CHANGED`）；加上 → `passed:true`，`append_exceptions: [{specs/SPEC_INDEX.md, APPEND_ONLY_NEW_SELECTION}]` |

追加段明确写出的**不声明**事项：未占用 018、未取得客户签署/需求冻结/独立 QA/银行验收、017 状态不变。

## 6. 允许启动的 Loop 与输入缺口

| Loop | 允许启动 | 条件 / 缺口 |
|---|---|---|
| L00 | 是（本轮） | — |
| L01 | 是 | L00 证据齐备 |
| L02 | 是 | OI02 真实银行源缺失 → 真实联调 `NOT_CONNECTED`；本地 manifest connector 可做 |
| L03 | 是 | 无外部阻塞 |
| L04 | 是 | OI03/OI04 缺真实引擎/权重/脱敏语料 → 真实识别 `INPUT_BLOCKED` |
| L05 | 是 | 依赖 L03；扫描表语义验收另依赖 L04 |
| L06 | 是 | 依赖 L04；真实 VLM 验收 `INPUT_BLOCKED` |
| L07 | 是 | 依赖 L02、L05、L06 |
| L08 | 是 | 依赖 L07；OI07/OI08 → 真实切换 `NOT_CONNECTED` |

统一交接路线：`TL_PLAN → DEV_SELF_TEST → DB_REVIEW → INDEPENDENT_QA → E2E → HO_HANDOFF → TL_FINAL`。

## 7. 本批次的角色诚实性声明

本轮由同一本地会话承担 TL 规划与 L00 证据采集，同时以 DEV/DB 口径执行 T002/T004。
因此：

- T002/T004 的结果是**测量与审查记录**，不构成独立 DEV/DB 岗位执行；
- 任何实现批次（L01+）的独立 QA 必须由**独立于代码作者**的执行体完成；
- 若无法满足独立性，状态记为 `INDEPENDENT_QA_PENDING`，**不得**通过切换角色名称制造独立结论。

## 8. 未做的事

- 未扩大 `allowed_new_or_scoped_paths`；112 项越界改为独立 `DOC_IMPORT` 批次处理（见 `BASELINE_REPORT.md` §6）。
- 未修改任务状态（`loop_contract.json` 全部 Loop 仍 `PLANNED`，任务仍 `TODO`）；执行进度只登记在 `evidence/`，与源计划严格分离。
- 未把 `PACK` 里的 `validation/*.json` 当成本轮通过证据。
- 未把 `DESIGN_CANDIDATE` 升级为 `APPROVED`/`FROZEN`。
