# L00 / T001 · BASELINE_REPORT

工作流 `DOC2MD-19CAP`；契约 `DOC2MD-19CAP-V1.0`；Loop `L00`；任务 `T001`。
执行角色（合同标注）：TL。执行人：本会话（AI 编码助手）。时间：2026-10-18（Asia/Shanghai）。

## 1. 结论

| 结论 | 内容 |
|---|---|
| 仓库基线 | 当前仓库 HEAD 与包声明基线**完全一致**，无需回退，也**未**重写基线锁 |
| 受保护文件 | 322 个受保护文件内容哈希与锁**全部一致**（0 不符） |
| mode 差异 | 包声明的 4 项 mode-only 差异属于**生成 ZIP 时的权限位丢失**，本仓库 4 个脚本均为 `100755`，与 git 索引一致 → 本仓库无此差异 |
| 工作区 | 录入前仅有 1 个未跟踪文件（原 ZIP）；未执行任何清理/重置/覆盖 |
| 已有改动 | 录入前无其他用户改动；文档导入为独立提交，未混入产品代码 |

## 2. 版本事实

| 项目 | 值 | 一致性 |
|---|---|---|
| 仓库根 | `/home/szf/dev/doc2md` | — |
| 分支 | `main` | 与 `origin/main` 一致（录入时） |
| 文档导入前 HEAD | `a20389ffd3f61baacc51502c10f8a503cac4ca3f` | = 包内 `zip_head` ✓ |
| 业务代码父提交 | `52a63ecf6ba852f65f848edfb1f835e7d360febd` | = 包内 `business_code_head`，为上述 HEAD 的父提交 ✓ |
| 文档导入提交（`DOC_IMPORT`） | `ab8f6822ced463c4602e5e32664de74169a05964` | 本批次单独提交 |
| **L00 入口 commit** | `ab8f6822ced463c4602e5e32664de74169a05964` | 文档导入之后，未含产品代码 |
| L01 入口 commit | 待 L00 证据收口后记录 | 尚未记录 |

> 说明：L00 入口取「文档导入提交」，而不是 `a20389ff`。原因见 §6：原包 L00 机器白名单不含 `contracts/`、`loop/`、`docs/architecture/`，若把契约落位混入 L00，`check_loop_diff.py` 会报 112 项越界；扩大白名单被禁止，故按用户指令 §4.2 分批。

## 3. 受保护文件哈希比对（基线锁 vs 当前仓库）

基线锁：`docs/proposals/doc2md-19cap/v1.0.0/inputs/baseline_lock.json`
（`schema_version=doc2md-baseline-lock/1.0`，`read_only=true`，`hash_scope="bytes"`，`files=322`）

比对方式：使用原包工具在 `PACK` 内运行（只读，不修改仓库）。

```bash
REPO="$(git rev-parse --show-toplevel)"
PY=/home/szf/anaconda3/bin/python
"$PY" "$REPO/docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017/tools/check_baseline.py" \
  --repo "$REPO" --allow-index-append
```

实测（2026-10-18）：

| 字段 | 值 |
|---|---|
| `check` | `PROTECTED_BASELINE_CONTENT_ONLY` |
| `passed` | **true** |
| `checked_file_count` | 322 |
| `errors` | `[]` |
| `mode_changes_not_treated_as_content_changes` | 4 项（见 §4） |
| `append_exceptions` | 1 项：`specs/SPEC_INDEX.md` → `APPEND_ONLY_NEW_SELECTION` |

受保护文件构成（322）：

| 目录/文件 | 数量 |
|---|---|
| `specs/` | 161 |
| `backend/` | 94 |
| `handoff/` | 23 |
| `frontend/` | 17 |
| `.cursor/` | 8 |
| `config/` | 8 |
| `scripts/` | 4 |
| `sql/` | 4 |
| `docs/` | 2 |
| `README.md` | 1 |

不加 `--allow-index-append` 时，检查如实失败（`specs/SPEC_INDEX.md: PROTECTED_BYTES_CHANGED`），证明该例外是**显式申请**而非默认放宽。

## 4. mode-only 差异核对

包内 `known_mode_only_changes` 列出 4 个脚本：

- `scripts/create_sample_dataset.py`
- `scripts/create_sample_dataset.sh`
- `scripts/init_db.sh`
- `scripts/run_pipeline.sh`

本仓库实测：

```
-rwxrwxr-x scripts/create_sample_dataset.py
-rwxrwxr-x scripts/create_sample_dataset.sh
-rwxrwxr-x scripts/init_db.sh
-rwxrwxr-x scripts/run_pipeline.sh
```

`git ls-files -s scripts/` 全部为 `100755`，与工作区一致。

**结论**：4 项 mode 差异是**包作者解包环境**的属性（ZIP 不保存 POSIX 可执行位），不是本仓库的差异。本仓库这 4 个文件既无内容差异也无 mode 差异，**不需要**任何修复动作，也不得把它们当作"已知差异"放行其他改动。

## 5. 工作区与已有改动

录入前 `git status --porcelain --untracked-files=all`：

```
?? "docs/architecture/doc2md_19项能力_SDD与Loop工程契约包_V1.0_20261017.zip"
```

即：唯一改动是用户放入的原 ZIP。处置：**保留原 ZIP 并入库**（作为只读来源快照的一部分），不做任何清理。

未执行：`git clean`、`git reset`、`git checkout --`、`git stash`、分支切换、worktree 创建。理由：仓库 HEAD 已等于包基线，无需隔离；文档导入与 L01 代码改动通过**分批提交**隔离即可。

## 6. 批次隔离证据（为什么必须分两次提交）

对文档导入批次运行 L00 范围检查（基线 = `a20389ff…`）：

```bash
"$PY" "$PACK/tools/check_loop_diff.py" --repo "$REPO" --loop L00 --base-commit a20389ffd3f61baacc51502c10f8a503cac4ca3f
```

实测：

| 字段 | 值 |
|---|---|
| `passed` | **false** |
| 白名单内（允许） | 27 项（`docs/proposals/doc2md-19cap/**`、`specs/proposals/doc2md-19cap-v1.0.0/**`、`specs/SPEC_INDEX.md`） |
| `errors` | **112** 项 `OUTSIDE_LOOP_WHITELIST` |

112 项构成：

| 前缀 | 数量 | 原因 |
|---|---|---|
| `docs/architecture/…` | 72 | 原 ZIP + `PACK` 解压目录（只读来源快照），不在 L00 白名单 |
| `contracts/doc2md-19cap/v1.0.0/…` | 34 | 用户指令 §4 指定的工作位置，不在原包 L00 白名单 |
| `loop/proposals/doc2md-19cap/v1.0.0/…` | 6 | 同上 |

处置：**不扩大代码白名单**，改为把契约落位作为独立的 `DOC_IMPORT` 批次提交（`ab8f682`），L00 入口 commit 取该提交之后。这样：

- `DOC_IMPORT` 批次的越界是**已知且已登记**的目录落位事实（见 `adoption/ADOPT-PATH-01.md`）；
- L00 批次的差异只落在其白名单内；
- L01 及以后的代码差异不会与文档导入混在一起。

## 7. 与历史基线的差异（不沿用数量容忍）

| 来源 | 记录 | 本轮实测 | 判定 |
|---|---|---|---|
| `handoff/00-READ-ME-FIRST.md` | `613 passed / 4 failed（环境基线）/ 2 skipped` | 项目默认解释器：`482 passed / 4 failed / 133 skipped` | 失败**ID 相同**（4 个 `--help`），通过/skip 数不同（环境与数据集状态不同），旧数字不作本轮结果 |
| `PACK/09_DECISION_ALIGNMENT.md` | "现有 requirements 已写 click<8.2，必须先复测，不得预设本机仍允许 4 红" | 钉住 `click==8.1.8` 的隔离环境：`486 passed / 0 failed / 133 skipped` | **4 红确为环境态**；requirements 合规环境下旧套件**全绿** |

因此本轮**不采纳**"4 个 `--help` 失败是永久豁免"的说法，改为登记为环境修复项（`ENV_BASELINE.json` 的 `L00-GAP-02`）。

## 8. 未做的事

- 未修改 `backend/requirements.txt`、`config/app.yaml`、`sql/**`、`specs/001—016`、017 任何文件。
- 未向系统 Python 安装任何依赖（隔离环境在 `/tmp`）。
- 未创建/修改任何 MySQL 库或用户（无凭据亦无授权）。
- 未把 `PACK` 当工作副本编辑；`PACK` 71 个文件与 `MANIFEST.json` 全量复核一致。
- 未声称本报告构成独立 QA 或 L00 通过结论（独立 QA 见后续 Gate）。
