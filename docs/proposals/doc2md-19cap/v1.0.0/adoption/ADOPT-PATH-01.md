# ADOPT-PATH-01 目录落位差异记录

> **编号性质声明**：`ADOPT-PATH-01` 是**本次工程记录编号**，不是客户原始编号，不得冒充客户需求条目、PI 编号或 TC 编号。
> 本记录只登记“文件搬到哪、相对引用怎么改、谁引用它、是否兼容、如何核验”，**不改任何条款语义**。

- 工作流：`DOC2MD-19CAP`；契约版本：`DOC2MD-19CAP-V1.0`
- 批次：`DOC_IMPORT`；录入时 HEAD：`a20389ffd3f61baacc51502c10f8a503cac4ca3f`
- 来源快照：`docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017/`（只读，未修改）
- 生成依据：`adoption/PATH_MAP.json`（71 条逐文件记录，含前后哈希）

## 1. 目录级落位

| # | 来源路径（PACK 内） | 工作路径（采用后） | 原因 | 引用方 | 兼容性 | 核验结果 |
|---|---|---|---|---|---|---|
| D-01 | `00_READ_ME_FIRST.md` … `09_DECISION_ALIGNMENT.md`、`04_TRACEABILITY.csv/.md`、`VALIDATION_REPORT.md`（12 个根文件） | `docs/proposals/doc2md-19cap/v1.0.0/` | 用户指令 §4 指定的 SDD/Loop/差异/追溯/验收工作位置 | `specs/proposals/**` 五件套的 Markdown 链接；`evidence/L00` 引用；SPEC_INDEX 记录 | 兼容：文件名不变，目录层级变化 | 12/12 存在，11 个逐字节相同；1 个（见 E-01）仅链接调整 |
| D-02 | `inputs/`（3 个） | `docs/proposals/doc2md-19cap/v1.0.0/inputs/` | 来源锁与设计输入随主设计文档归档 | `loop_contract.json` 的 `baseline_lock` / `protected_existing_files_ref` | 兼容：文件名不变 | 3/3 存在，3/3 逐字节相同 |
| D-03 | `spec-proposal/`（6 个） | `specs/proposals/doc2md-19cap-v1.0.0/` | 使用项目既有 `specs/proposals/` 约定，避免占用 018 正式编号 | SPEC_INDEX 记录；五件套互相链接 | 兼容：文件名不变 | 6/6 存在，1 个逐字节相同，5 个仅链接调整（E-02…E-06） |
| D-04 | `contracts/`（34 个） | `contracts/doc2md-19cap/v1.0.0/` | 版本化合同目录，参考契约与业务代码分离 | L01 实现依据；`loop/test_cases.json` 的 `schema_ref` 语义引用 | 兼容：内部相对引用均为同目录引用（`schemas/`、`examples/`） | 34/34 存在，34/34 逐字节相同 |
| D-05 | `loop/`（6 个） | `loop/proposals/doc2md-19cap/v1.0.0/` | 机器可读 Loop 合同随工作流版本化 | `check_loop_diff.py`（指向本 JSON 的白名单）；TL 派工 | 兼容：4 个内部引用中 2 个同目录不变，2 个跨目录重写（E-07） | 6/6 存在，5 个逐字节相同，1 个仅路径键调整 |
| D-06 | `tools/`（4 个） | **不搬迁**，保留 `PACK/tools/` | 用户指令 §4：原包校验脚本按原有相对目录结构在 `PACK` 内运行 | 用户指令 §9 的三条校验命令 | 兼容：以 `PACK` 为运行根 | 4/4 存在，未修改 |
| D-07 | `validation/`（4 个） | **不搬迁**，保留 `PACK/validation/` | 作者自检回执属原包证据，不作为新实现通过证据 | `VALIDATION_REPORT.md` | 兼容 | 4/4 存在，未修改 |
| D-08 | `MANIFEST.json` | **不搬迁**，保留 `PACK/MANIFEST.json` | 用户指令 §4.1：保留原清单，另生成 `ADOPTED_MANIFEST.json` | `build_adoption_records.py` 校验来源字节 | 兼容 | 存在，未修改；71 条哈希复核 0 不符 |
| D-09 | `adoption/SPEC_INDEX_APPEND_CANDIDATE.md` | **不搬迁**，保留 `PACK/adoption/` | 属原包候选文本，实际追加落在 `specs/SPEC_INDEX.md` | 本记录与 SPEC_INDEX 追加 | 兼容 | 存在，未修改 |

合计：`PACK` 71 个文件 = 搬迁 61（55 逐字节相同 + 6 仅路径调整）+ 不搬迁 10。

## 2. 文件级路径引用调整（穷举）

所有调整均按“**相对引用文件所在目录**”解析后重写，逐一验证目标存在。共 7 处引用于 6 个文件内。

| # | 文件 | 键 / 链接 | 修改前 | 修改后 | 原因 | 引用方 | 兼容性 | 核验 |
|---|---|---|---|---|---|---|---|---|
| E-01 | `loop/proposals/doc2md-19cap/v1.0.0/loop_contract.json` | `baseline_lock` | `../inputs/baseline_lock.json` | `../../../../docs/proposals/doc2md-19cap/v1.0.0/inputs/baseline_lock.json` | JSON 从 `PACK/loop/`（1 层）搬到 `loop/proposals/doc2md-19cap/v1.0.0/`（4 层），相对深度变化 | `check_baseline.py` 语义、TL 派工、L00 T001 | 兼容：键名与语义不变，仅定位值 | 目标存在 ✓ |
| E-02 | 同上 | `protected_existing_files_ref` | `../inputs/baseline_lock.json` | `../../../../docs/proposals/doc2md-19cap/v1.0.0/inputs/baseline_lock.json` | 同 E-01 | 受保护文件哈希锁比对 | 兼容：同上 | 目标存在 ✓ |
| E-03 | 同上 | `traceability` | `requirements_trace.json` | 不变 | 同目录搬移，相对位置保持 | 追溯核验 | 兼容 | 目标存在 ✓ |
| E-04 | 同上 | `test_cases` | `test_cases.json` | 不变 | 同目录搬移，相对位置保持 | TC 状态核验 | 兼容 | 目标存在 ✓ |
| E-05 | `specs/proposals/doc2md-19cap-v1.0.0/spec.md` | Markdown 链接 | `../01_SDD.md` | `../../../docs/proposals/doc2md-19cap/v1.0.0/01_SDD.md` | 五件套从 `PACK/spec-proposal/` 搬到 `specs/proposals/doc2md-19cap-v1.0.0/`，主设计文档不在同一父目录 | `spec.md` 正文入口 | 兼容：链接可解析 | 目标存在 ✓ |
| E-06 | `.../plan.md`、`.../tasks.md` | Markdown 链接 | `../02_LOOP_ENGINEERING.md` | `../../../docs/proposals/doc2md-19cap/v1.0.0/02_LOOP_ENGINEERING.md` | 同 E-05 | 两文件正文入口 | 兼容 | 目标存在 ✓（2 处） |
| E-07 | `.../acceptance.md`、`.../test_cases.md` | Markdown 链接 | `../05_TEST_ACCEPTANCE.md` | `../../../docs/proposals/doc2md-19cap/v1.0.0/05_TEST_ACCEPTANCE.md` | 同 E-05 | 两文件正文入口 | 兼容 | 目标存在 ✓（2 处） |

> E-06、E-07 各覆盖 2 个文件、2 处链接；6 个文件合计 7 处引用调整（E-01…E-04 在同一 JSON，E-05 1 处，E-06 2 处，E-07 2 处）。

## 3. 明确**未**做的事

1. 未把 schema 标识符、字段名、接口 URI、枚举值当文件路径批量替换：`source-version/1.0`、`enhancement-job/1`、`EXTENSION` / `RAW_VAULT` / `PARSED` 等全部原样。
2. 未改任何字段、状态、任务依赖、验收标准、代码白名单：`loop_contract.json` 除上述 2 个定位键外的全部内容与来源逐字节一致。
3. 未用通配脚本批量改 Markdown：只改上表 6 个文件中的 5 处链接（其余文件链接数 0）。
4. 未把 `PACK` 当作工作副本编辑；`PACK` 内容哈希与 `MANIFEST.json` 全量一致。
5. 未删除原包 `MANIFEST.json`，未用 `ADOPTED_MANIFEST.json` 冒充原件清单。

## 4. 复现与核验命令

```bash
REPO="$(git rev-parse --show-toplevel)"
PY=/home/szf/anaconda3/bin/python

# 1) 重新生成 PATH_MAP / ADOPTED_MANIFEST（从磁盘现算，不抄原清单）
"$PY" "$REPO/docs/proposals/doc2md-19cap/v1.0.0/adoption/build_adoption_records.py"
# 期望：{"pack_files": 71, "relocated": 61, "identical": 55, "path_edited": 6,
#        "zip_sha_ok": true, "pack_manifest_mismatch": []}

# 2) 原包完整性（在 PACK 内运行）
"$PY" "$REPO/docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017/tools/check_delivery.py"
# 期望：passed=true，53 项 PASS

# 3) 旧基线内容保护（含 SPEC_INDEX 追加例外）
"$PY" "$REPO/docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017/tools/check_baseline.py" \
  --repo "$REPO" --allow-index-append
```

实际执行结果见 `evidence/L00/SELECTION_RECORD.md` 与 `evidence/L00/BASELINE_REPORT.md`。

## 5. 已知残余风险

| 风险 | 说明 | 处置 |
|---|---|---|
| R-01 双副本漂移 | `PACK` 与工作副本并存，若后续只改一侧会产生两套可编辑契约 | 唯一工作副本声明见 `ADOPTION_RECORD.md` §6；`PATH_MAP.json` 可随时比对哈希 |
| R-02 原包工具不覆盖工作副本 | `PACK/tools/*` 只校验 `PACK` 自身与旧仓库基线，不会验证搬迁后的工作副本路径 | L00 增加独立的“工作副本引用存在性”核验（本文 §2 表 + 生成器断言），并在 `SELECTION_RECORD.md` 留证 |
| R-03 白名单未覆盖契约目录 | `loop_contract.json` 的 L00 白名单不含 `contracts/`、`loop/` | 故契约落位单独提交为 `DOC_IMPORT` 批次，L00 入口 commit 取该提交之后（用户指令 §4.2） |
