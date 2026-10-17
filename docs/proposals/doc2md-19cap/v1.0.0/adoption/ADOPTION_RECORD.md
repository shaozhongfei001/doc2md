# DOC2MD-19CAP 采用登记（ADOPTION_RECORD）

批次：`DOC_IMPORT`（文档导入独立批次，不含 L01 及以后的产品代码）
工作流：`DOC2MD-19CAP`
契约版本：`DOC2MD-19CAP-V1.0`（包状态 `DESIGN_CANDIDATE`，未冻结、未签署、未独立 QA）

## 1. 采用人和角色

| 项目 | 实际值 |
|---|---|
| 采用人 | 本会话执行主体（AI 编码助手，本地会话） |
| 角色 | Tech Lead（TL）：接收设计、登记增量契约、组织开发与独立验证 |
| 真实时间 | 2026-10-18（Asia/Shanghai，本机本地时间） |
| 仓库根 | `/home/szf/dev/doc2md` |
| 分支 | `main` |
| 录入时 HEAD | `a20389ffd3f61baacc51502c10f8a503cac4ca3f`（= 包内 `zip_head`） |
| 业务代码父提交 | `52a63ecf6ba852f65f848edfb1f835e7d360febd`（= 包内 `business_code_head`，为 HEAD 的父提交） |
| 授权来源 | 用户本次施工指令（19 项能力范围 + 保留 001—017 + 新增 extensions 层） |

未做任何 git 清理、重置或覆盖；录入前工作区仅有 1 个未跟踪文件（原 ZIP），已保留。

## 2. 输入包

| 项目 | 实际值 |
|---|---|
| ZIP 路径 | `docs/architecture/doc2md_19项能力_SDD与Loop工程契约包_V1.0_20261017.zip` |
| ZIP 实际 SHA-256 | `f196a238b203e272a23a4a9c4e1485edf3d233ea329f340eb6e6ee7a30ccfc51` |
| 预期 SHA-256 | `f196a238b203e272a23a4a9c4e1485edf3d233ea329f340eb6e6ee7a30ccfc51` |
| 是否一致 | **一致**（判定为同一 V1.0 包，无换包迹象） |
| 条目数 | 71（单一顶层目录，无绝对路径、无 `..` 穿越、无符号链接） |
| 解压位置（只读快照） | `docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017/`（下称 `PACK`） |
| 原包 `MANIFEST.json` | 保留在 `PACK/MANIFEST.json`，本轮 71 个文件逐一复核内容哈希，`pack_manifest_mismatch=[]` |

`PACK` 为只读来源快照和原包校验工具的运行根：**不在其中修改契约、任务状态或校验结果**。

## 3. 允许启动的 Loop

L00 及以后 Loop 的启动授权如下（`PLANNED` 不等于已执行）：

| Loop | 名称 | 本轮是否允许启动 | 依据 |
|---|---|---|---|
| L00 | 基线、环境和实施选择 | **允许（本轮执行）** | 用户指令 §5.1 |
| L01 | 合同、输入桥接与原子产物仓库 | **允许（L00 证据齐备后启动）** | 用户指令 §5.2、§7 |
| L02 | 来源版本、许可与可恢复同步 | 允许启动，真实银行源联调 `NOT_CONNECTED` | OI02 未取得 |
| L03 | 原生 Word 与表格结构 | 允许启动 | 无外部输入阻塞 |
| L04 | Paddle/MinerU 适配、坐标和 OCR | 允许启动；真实引擎实测受 OI03/OI04 限制 | OI03/OI04 |
| L05 | 多级表头、续表与脚注 | 允许启动（依赖 L03）；扫描表语义验收另依赖 L04 | 契约 `conditional_dependencies` |
| L06 | 流程图、图表与一次 ROI 复识别 | 允许启动（依赖 L04）；真实 VLM 验收 `INPUT_BLOCKED` | OI03/OI04 |
| L07 | 人工纠错、候选证据与依赖失效 | 允许启动（依赖 L02、L05、L06） | 契约 `depends_on` |
| L08 | 最终对账、切换、恢复与交付 | 允许启动（依赖 L07）；银行联调 `NOT_CONNECTED` | OI07/OI08 |

**不在本次默认授权内**：生产部署、生产切换、覆盖受保护契约、修改 `sql/**`、回写 `raw_vault/` 或既有 `parsed/`。

## 4. 文件落位总表（来源 → 工作副本）

| 内容 | 来源（PACK 内） | 工作位置 | 文件数 |
|---|---|---|---|
| 原始交付包 | 原 ZIP + 完整解压目录 | `docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017/`（只读，不搬迁） | 71 |
| SDD / Loop 说明 / 差异决议 / 追溯 / 验收 / 验证报告 | `00_READ_ME_FIRST.md` … `09_DECISION_ALIGNMENT.md`、`VALIDATION_REPORT.md` | `docs/proposals/doc2md-19cap/v1.0.0/` | 12 |
| 来源锁与设计输入 | `inputs/` | `docs/proposals/doc2md-19cap/v1.0.0/inputs/` | 3 |
| Spec 五件套及说明 | `spec-proposal/` | `specs/proposals/doc2md-19cap-v1.0.0/` | 6 |
| 字段 schema、接口、身份规则及样例 | `contracts/` | `contracts/doc2md-19cap/v1.0.0/` | 34 |
| 机器可读 Loop / 任务 / 测试 / 追溯 | `loop/` | `loop/proposals/doc2md-19cap/v1.0.0/` | 6 |
| 原包校验脚本 | `tools/` | 保留在 `PACK/tools/`（`PACK_ONLY`，不搬迁） | 4 |
| 原包自检回执 | `validation/` | 保留在 `PACK/validation/`（`PACK_ONLY`） | 4 |
| 原包清单 | `MANIFEST.json` | 保留在 `PACK/MANIFEST.json`（`PACK_ONLY`） | 1 |
| SPEC_INDEX 追加候选 | `adoption/SPEC_INDEX_APPEND_CANDIDATE.md` | 保留在 `PACK/adoption/`（`PACK_ONLY`，实际追加见 `specs/SPEC_INDEX.md`） | 1 |
| 采用登记 / 路径映射 / 执行证据 | 本轮实际生成 | `docs/proposals/doc2md-19cap/v1.0.0/adoption/`、`docs/proposals/doc2md-19cap/v1.0.0/evidence/` | — |

合计：61 个文件搬迁为工作副本（55 个逐字节相同 + 6 个仅做已登记的路径引用调整），10 个 `PACK_ONLY`。

## 5. 来源与工作位置的逐文件登记

- `adoption/PATH_MAP.json`：71 条逐文件记录，含 `source_path` / `source_sha256` / `work_path` / `work_sha256` / `relocated` / `content_identical` / `path_edit`。
- `adoption/ADOPTED_MANIFEST.json`：采用后工作副本的独立清单（61 条），**不替代** `PACK/MANIFEST.json`；不得用它证明已改路径的文件仍与原件逐字节相同。
- 两份记录均由 `adoption/build_adoption_records.py` 从磁盘现算哈希生成，可重放：

```bash
/home/szf/anaconda3/bin/python docs/proposals/doc2md-19cap/v1.0.0/adoption/build_adoption_records.py
```

本轮实际输出：

```json
{"pack_files": 71, "relocated": 61, "identical": 55, "path_edited": 6,
 "zip_sha_ok": true, "pack_manifest_mismatch": []}
```

## 6. 唯一工作副本声明

`PACK` 只保存原貌；上表“工作位置”是**采用后唯一可编辑工作副本**。项目入口（`specs/SPEC_INDEX.md` 的 `DOC2MD-19CAP` 记录）与全部后续任务单统一指向工作副本，并记录版本、哈希和来源。

业务程序代码不在契约参考目录内开发：L01 及以后只写 `backend/app/extensions/**` 与 `backend/tests/extensions/**`，`contracts/doc2md-19cap/v1.0.0/` 保持参考契约只读。

## 7. 本批次的边界与未声明事项

本批次（`DOC_IMPORT`）允许的变更仅限：

1. 原包归档（ZIP + `PACK` 解压）；
2. 上表“工作位置”新增目录与文件落位；
3. 目录落位差异登记（`ADOPT-PATH-01`）与引用核验；
4. `specs/SPEC_INDEX.md` 末尾受限追加一条 `DOC2MD-19CAP` 记录。

本批次**未**做、也不得据此声称：

- 未修改任何产品代码、`sql/**`、既有契约语义、原三件套、IR 1.0、旧 CLI；
- 未取得客户签署、需求冻结、独立 QA 通过或银行验收；
- 未把 017 改为 DONE，未替 017 的剩余任务/裁定/验收改状态；
- 未把本包 `DESIGN_CANDIDATE` 升级为 `APPROVED` 或 `FROZEN`；
- 未占用“018”正式编号（`DOC2MD-19CAP` 为工作流标识，正式编号由项目规则另行确定）。

## 8. 待决项（不阻塞本批次，仅阻塞对应实测）

沿用 `07_OPEN_INPUTS.md` 的 OI01—OI09，责任人/影响/补验条件见该文件与 `docs/proposals/doc2md-19cap/v1.0.0/evidence/L00/` 记录。本轮实际阻塞统计见 `evidence/L00/ENV_BASELINE.json`。
