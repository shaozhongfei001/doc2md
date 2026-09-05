# 902 Test Dataset — Handoff

> Spec: `specs/902-test-dataset/`
> Branch: `feature/902-test-dataset` (**not merged**)
> Stage: P8 Handoff
> Status: **DEV DONE → P5 QA PASS — P6/P7 未进行，merge 待 Owner 显式授权**
> Handoff date: 2026-09-05

---

## 1. Completion Summary / 完成摘要

902 在本仓库提供一套**可提交、可幂等再生、非敏感**的小样本集与生成脚本，用于后续对 001–013 做**只读**冒烟验证。

已交付：

- 样本树 `backend/tests/fixtures/sample_dataset/`：8 个样本 + `manifest.yaml`
- 幂等生成脚本：`scripts/create_sample_dataset.sh`（6 行委派）→ `scripts/create_sample_dataset.py`
- pytest：`backend/tests/test_sample_dataset.py` — **33 passed**
- 操作文档：`docs/sample_dataset.md`
- `.gitignore`：仅追加 1 行 `data/sample_dataset/`（第 29 行）
- 门禁报告：`p2_db_review.md` / `p3_implementation_gate.md` / `p4_kickoff.md` / `p5_qa_report.md`

**902 明确没有做的事**（scope boundary，不得越界）：

- 不连接 MySQL、不 `create_engine`、不 `session.commit`、零 DML
- 不调用 MarkItDown / MinerU / `magic-pdf`（样本只供**文件级**验证，未做真实解析冒烟）
- 不改 `sql/**`、不改 schema、不改 ORM
- 不改 `backend/app/**`
- 不写 `raw_vault/` `parsed/` `curated/` `reports/` `quarantine/`
- 不移动 / 删除 / 重命名任何用户原始文件
- 不调用 001–013 的任何 CLI（`scan` 等），不把样本自动 scan 进库
- 不建 `backend/.venv`、不写 `config/app.yaml`、不装 `magic-pdf`（属操作员 P0 任务）
- 不重开 001–013，不触碰 `specs/008-review-workflow/`、`specs/901-docker-compose-deployment/`

**P5 QA: PASS**（A001–A016 全 PASS，A016 为 PASS WITH NOTES；零阻断缺陷）。

**P8 本轮动作：仅新增本交接文档。** 未执行 merge、未把 902 标为 DONE、未改 `SPEC_INDEX.md` / `docs/feature_index.md` / `README.md` / `tasks.md`。

**关于敏感信息**：本 Spec 的 8 个样本**全部为虚构内容**（如「这是一份虚构的银行项目方案摘要，仅供测试使用。」），不含任何真实公文、客户资料、业务数据、账号、口令、私钥、token、证件号、银行卡号。本文档亦不包含任何密钥或凭据。

---

## 2. P1–P7 Stage Chain / 门禁时间线

| 阶段 | Owner | 结论 | 报告 / 依据 |
|---|---|---|---|
| P1 Tech Lead Plan | TL | **PASS**（2026-09-05 Owner 确认） | `spec.md` / `plan.md` / `tasks.md` / `acceptance.md` / `test_cases.md`；`specs/SPEC_INDEX.md` 902 → ACTIVE；`docs/tl-review-2026-09-05-env-and-active-spec.md` |
| P2 DB & Data Review | DB（TL 代行审查，未改 SQL） | **PASS WITH CONSTRAINTS（C1–C8）** | `specs/902-test-dataset/p2_db_review.md` |
| P3 Implementation Gate | TL | **GATE PASS**（P4 待 Owner 确认后才开始） | `specs/902-test-dataset/p3_implementation_gate.md` |
| P4 Kickoff | Owner（2026-09-05 确认）→ 裁决 **P4-GATE = OPEN** | 存量产物经 TL 现场核查后**采纳**，DEV 补做修复 | `specs/902-test-dataset/p4_kickoff.md` |
| P4 Dev Implementation | DEV | **DONE**：T006 修正 + T007 + T008 + T009 + T011 + T012 | `specs/902-test-dataset/tasks.md` §5 |
| P5 QA | QA | **P5-GATE: PASS** — 33 passed；A001–A016 全 PASS；观察项 2（N1/N2） | `specs/902-test-dataset/p5_qa_report.md` |
| P6 E2E | QA / E2E | **未开始** — `p6_e2e_report.md` 不存在（`tasks.md` §6 该 checkbox 未勾选） | 归档脚本幂等 + 不连库证据 |
| P7 TL Final Review | TL | **未开始** | — |
| P8 Handoff | HO | **本文件** | `docs/handoff-902-test-dataset.md` |

### 2.1 P4 关键事件：D-01 阻断缺陷已闭环

`p4_kickoff.md` 记录 D-01（阻断级）：`minimal_docx_bytes()` 使用 `zf.writestr(name, data)`，zip 成员头写入**当前系统时间**，导致每次生成的 `最小文档.docx` 字节不同，违反 A009 幂等。

修复（T012）：改用显式 `zipfile.ZipInfo`，固定 `date_time = (1980, 1, 1, 0, 0, 0)`、`create_system = 0`、`external_attr = 0o600 << 16`，三个成员写入顺序固定（`[Content_Types].xml` → `_rels/.rels` → `word/document.xml`）。

修复后 QA 复核：同脚本相隔 1.2 秒两次生成 docx 字节一致（993 B，`b4fbe3cf…`）；fixture 树 + runtime 树 `diff -r` 全等；原地重跑对仓库 fixture 无字节变更。**缺陷已闭环**。docx 字节较修复前变化，`manifest.yaml` 对应 sha256 已同步（属白名单内）。

次级缺陷 D-02（`data/sample_dataset/` 未 gitignore）由 T007 消除，`.gitignore` 仅追加一行。

---

## 3. Deliverables / 交付物清单

| 路径 | 类型 | 说明 |
|---|---|---|
| `backend/tests/fixtures/sample_dataset/` | 新增目录（提交） | 8 个样本 + `manifest.yaml` |
| `backend/tests/fixtures/sample_dataset/manifest.yaml` | 新增 | `schema_version` / `spec` / `files[]`；每条含 `rel_path` `sha256` `purpose` `duplicate_group` `intended_route` |
| `scripts/create_sample_dataset.sh` | **修改**（旧脚本重写） | 原脚本向 `backend/tests/fixtures/中文路径/` 写入（违反 C3）；现为 6 行委派 + 2 行注释，无 `rm -rf` |
| `scripts/create_sample_dataset.py` | 新增 | 生成器主体；不进 `backend/app/**`，仅标准库 |
| `backend/tests/test_sample_dataset.py` | 新增 | 33 个用例，覆盖 TC001–TC015 |
| `docs/sample_dataset.md` | 新增 | 生成命令、样本清单、禁止事项、校验命令 |
| `.gitignore` | **修改**（仅追加 1 行） | 第 29 行 `data/sample_dataset/` |
| `specs/902-test-dataset/p2_db_review.md` | 新增 | P2 DB Review |
| `specs/902-test-dataset/p3_implementation_gate.md` | 新增 | P3 Gate |
| `specs/902-test-dataset/p4_kickoff.md` | 新增 | P4 裁决 + 缺陷单 D-01/D-02 |
| `specs/902-test-dataset/p5_qa_report.md` | 新增 | P5 QA PASS |
| `specs/902-test-dataset/p6_e2e_report.md` | **不存在** | P6 未开始 |

**黑名单路径零扩散（P5 §7 复核确认）**：`backend/app/**`、`sql/**`、`backend/tests/fixtures/中文路径/**`、`backend/tests/fixtures/parse_quality_report_*.json`、`fixtures/evidence_chain_*`、`fixtures/curated_project/**`、`fixtures/search/**`、`backend/requirements.txt`、`config/**`、`frontend/**`、specs 001–013 五件套 —— 均未触碰。

C3 闭环确认：旧脚本的 `mkdir -p backend/tests/fixtures/中文路径/银行项目` + `echo`/`cp` 写入已完全移除；`main()` 对封存路径返回**退出码 2**。

---

## 4. Usage / 使用方法

### 4.1 生成命令

在仓库根执行：

```bash
cd /home/szf/dev/doc2md

# 默认：生成 fixture 树 + 同步运行时副本到 data/sample_dataset/
bash /home/szf/dev/doc2md/scripts/create_sample_dataset.sh

# 只生成 / 校验 fixture 树，不同步运行时副本
bash /home/szf/dev/doc2md/scripts/create_sample_dataset.sh --skip-sync

# 自定义根目录（调试 / 隔离验证用）
python3 /home/szf/dev/doc2md/scripts/create_sample_dataset.py \
  --fixture-root /tmp/fx --runtime-root /tmp/rt
```

### 4.2 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--repo-root PATH` | 脚本所在目录的上一级 | 仓库根；影响默认 runtime 根的位置 |
| `--fixture-root PATH` | `<repo>/backend/tests/fixtures/sample_dataset` | 样本树输出位置（应提交） |
| `--runtime-root PATH` | `<repo>/data/sample_dataset` | 运行时副本位置（**已被 gitignore**） |
| `--skip-sync` | 关闭 | 只处理 fixture 树，不写 runtime 副本 |
| `--help` | — | 由 argparse 提供，说明输出路径与「不连 MySQL / 不调 parser」 |

退出码：`0` 成功；`2` 拒绝写入封存目录 `backend/tests/fixtures/中文路径/`。

### 4.3 幂等行为

- 目标文件已存在且**字节相同** → `skipped`，**不改写 mtime**
- 目标文件不存在或字节不同 → 覆盖写入（仅限 902 允许的 fixture 树与 runtime 根）
- 不删除样本树以外的任何文件；脚本中不存在 `rm` / `rmtree` / `unlink`
- 连续两次运行，8 个样本字节完全不变（QA 取证：隔离目录间隔 2 秒两次运行 `exit=0 files=8`，`MTIME IDENTICAL`，`diff -r` 全等）
- `manifest.yaml` 由脚本按磁盘字节重新渲染，与磁盘强一致

### 4.4 路径逃逸与封存目录保护

`resolve_under_root()` 三重防护：

1. 拒绝空相对路径与以 `/` 或 `\` 开头的绝对路径 → `PathEscapeError`
2. 拒绝任何路径片段为 `..` 的父目录穿越 → `PathEscapeError`
3. `resolve()` 后校验 `is_relative_to(root_resolved)`，拒绝符号链接逃逸 → `PathEscapeError`

封存资产保护：`main()` 检查若 `fixture_root` 等于或位于 `<repo>/backend/tests/fixtures/中文路径` 之下，向 stderr 打印 `refusing to write sealed fixtures/中文路径` 并**返回退出码 2**（001–010 依赖该目录下两个 txt）。

对应测试：`test_resolve_under_root_rejects_escape[4]`、`test_ensure_fixtures_refuses_escaping_manifest`、`test_script_refuses_to_write_sealed_fixtures`。

---

## 5. Sample Inventory / 样本清单

以下 sha256 取自 `backend/tests/fixtures/sample_dataset/manifest.yaml`，并已在交接时与磁盘字节逐一复核一致。

| # | 相对路径（`sample_dataset/` 下） | 用途 | intended_route | 大小 | sha256 |
|---|---|---|---|---|---|
| 1 | `中文路径/银行项目/方案.txt` | 中文路径；精确重复对 A | `text` | 70 B | `f70c9fd72023780761d7203b1257ca1141d30a02549d10c5bfae39225af3bc9c` |
| 2 | `中文路径/银行项目/方案副本.txt` | 与 方案.txt **字节级相同** | `text` | 70 B | `f70c9fd72023780761d7203b1257ca1141d30a02549d10c5bfae39225af3bc9c` |
| 3 | `中文路径/银行项目/会议纪要.md` | Markdown 样本 | `markitdown` | 65 B | `5b20428aee53e47483d644632f55154c5a038e19cbcaf63459398f14fd62e49e` |
| 4 | `中文路径/银行项目/指标口径.csv` | CSV 样本 | `text` | 52 B | `a073424c2edff9d8bb4c48e90c5a054d5998ad5d54658aceef796be5f0a5a287` |
| 5 | `中文路径/银行项目/说明.html` | HTML 样本 | `markitdown` | 49 B | `ce1bd539613e9ccc0a02b1ef580bab0bdd8b7464e0f18b82e2f7f961aebdff2d` |
| 6 | `中文路径/银行项目/元数据.json` | JSON 样本 | `markitdown` | 54 B | `8990fe005a7d3191dfe95b6abb69c6ccec639879b53742614fa9c930ab562f13` |
| 7 | `office_like/最小文档.docx` | 最小 OOXML（`[Content_Types].xml` + `_rels/.rels` + `word/document.xml`） | `markitdown` | 993 B | `b4fbe3cf92797699e508b655e25b48d0b1c8bd2acb39b57790a16ef68f612921` |
| 8 | `pdf/hello.pdf` | 最小 PDF（`%PDF-1.1`，1 页，文本 `hello`） | `mineru` | 440 B | `6e1d00daac00131253bd3185d02c5a0d0905a883470df14181b64194a23d633d` |

外加 `manifest.yaml`（2,004 B，由脚本生成）。

**精确重复对**：#1 `方案.txt` 与 #2 `方案副本.txt` sha256 **完全相同**（`f70c9fd7…`，均 70 B），`duplicate_group = "dup-plan-a"`，供 003 重复治理验证使用。其余 6 个样本两两互异，无碰撞。

**中文路径**：`中文路径/银行项目/` 为两层中文目录，6 个文件名为中文，UTF-8 可读（A003）。

**最小 PDF / docx**：均由 `create_sample_dataset.py` 在内存中构造字节（无网络、无下载、无 MS Office、无真实公文）。docx 为 zip（`PK` 头）且含 `word/document.xml`；PDF 以 `%PDF-` 开头。

---

## 6. Tests / 测试

```bash
cd /home/szf/dev/doc2md
PYTHONPATH=backend python3 -m pytest backend/tests/test_sample_dataset.py -q
```

结果：**33 passed in 0.17s**（Python 3.10.12 / pytest 9.1.1，rootdir 仓库根）。交接时已复跑确认。

测试仅依赖标准库 + pytest，不 import `app/**`，因此可在 venv 就绪前运行。

### 6.1 TC 覆盖映射（`test_cases.md` TC001–TC015，全部 PASS）

| 用例 | 测试 | 结果 |
|---|---|---|
| TC001 清单 | `test_fixture_exists_and_non_empty[8]` | PASS |
| TC002 中文路径 | `test_chinese_path_readable` | PASS |
| TC003 精确重复 | `test_exact_duplicate_pair_same_sha256` | PASS |
| TC004 互异 | `test_non_duplicate_samples_are_distinct` | PASS |
| TC005 PDF 头 | `test_pdf_has_pdf_header` | PASS |
| TC006 docx zip | `test_docx_is_zip_with_document_part` | PASS |
| TC007 manifest | `test_manifest_covers_expected_files` / `..._sha256_matches_disk` / `..._marks_duplicate_group` | PASS |
| TC008 敏感扫描 | `test_samples_and_manifest_have_no_secrets` | PASS |
| TC009 既有 fixture | `test_sealed_fixtures_untouched` / `test_script_refuses_to_write_sealed_fixtures` | PASS |
| TC010 脚本首次 | `test_script_generates_fixture_tree` | PASS |
| TC011 脚本重跑 | `test_script_is_idempotent` / `test_repo_fixtures_match_regeneration` | PASS |
| TC012 无 DB | `test_script_has_no_database_usage` | PASS |
| TC013 无 parser | `test_script_has_no_parser_usage` | PASS |
| TC014 路径逃逸 | `test_resolve_under_root_rejects_escape[4]` / `test_ensure_fixtures_refuses_escaping_manifest` | PASS |
| TC015 中文内容 | `test_text_samples_contain_chinese[3]` | PASS |

### 6.2 A011 / A012 运行时探针（QA 取证）

QA 采用**运行时陷阱**而非仅静态扫描：`PYTHONPATH` 注入假 `pymysql` / `sqlalchemy` / `sqlalchemy.engine`（import 即抛错），`PATH` 前置屏蔽 `mysql` / `mysqladmin` / `magic-pdf` / `markitdown` / `mineru`（调用即以 97 退出）。结果脚本两次运行 `exit=0`、pytest `33 passed`，**探针零触发**。

### 6.3 A007 独立敏感扫描（QA 自执行，不复用 DEV 断言）

覆盖 docx 内部 XML 与 PDF 原始字节。身份证 18 位、银行卡 13–19 位、手机号、邮箱、私钥 `BEGIN … PRIVATE KEY`、口令赋值、`IP:PORT`、`AKIA…` —— **全部无命中，结果 CLEAN**。

---

## 7. P2 Constraints (Locked C1–C8)

**P2-GATE: PASS WITH CONSTRAINTS**

| ID | 约束 | P4/P5 落地情况 |
|---|---|---|
| C1 | 运行时与测试**不连 MySQL**（不 create_engine、不 session、不 pymysql） | 满足（运行时探针零触发） |
| C2 | **不改** `sql/001_init_schema_v1_1.sql`、`sql/migrations/**`、任何 ORM | 满足（`git status sql` 为空） |
| C3 | **禁止**再写 `backend/tests/fixtures/中文路径/**` | 满足（旧脚本写入逻辑已移除；`main()` 退 2；与 git HEAD 逐字节比对未变） |
| C4 | 只写 `sample_dataset/` fixture 与 `data/sample_dataset/` | 满足（全仓 mtime+size 快照对照，唯一差异为 pytest 自身缓存） |
| C5 | 幂等 = 内容 sha256 相同则跳过；禁止广域 `rm -rf` | 满足（同内容跳过且不改 mtime；脚本无删除调用） |
| C6 | `manifest.yaml` 的 `sha256` / `duplicate_group` 是文件元数据，**不是** `kb_*` 列 | 满足（未引入任何表字段） |
| C7 | 不调用 001–013 CLI，不写 vault / parsed / curated / reports | 满足 |
| C8 | 不发明 DB 字段；不把 review / embedding 表当 902 输出 | 满足 |

**Migration: NOT required. NOT authorized.**

---

## 8. Acceptance Summary / 验收结论

对照 `specs/902-test-dataset/acceptance.md` A001–A016：

| ID | 标准 | 结论 |
|---|---|---|
| A001 | 活跃 Spec 对齐 | PASS |
| A002 | 样本清单完整 | PASS |
| A003 | 中文路径 | PASS |
| A004 | 精确重复 | PASS |
| A005 | 非重复互异 | PASS |
| A006 | 路由覆盖文件（docx + pdf 各 ≥1） | PASS |
| A007 | 无敏感内容 | PASS |
| A008 | 既有 fixture 只读 | PASS |
| A009 | 脚本幂等 | PASS |
| A010 | 写路径收敛 | PASS |
| A011 | 零数据库 | PASS |
| A012 | 零 parser | PASS |
| A013 | 无业务代码（`backend/app/**` 无 902 diff） | PASS |
| A014 | 原始文件安全 | PASS |
| A015 | 文档（`docs/sample_dataset.md`，无密钥） | PASS |
| A016 | 测试 | **PASS WITH NOTES**（33 passed；既有测试未被修改，收集错误数与改动前一致） |

**总体：PASS**（P5 判定）。notes 未触及 A001/A011/A012/A013/A014 任一禁止否定项。

---

## 9. Known Limitations & Follow-ups / 已知限制与后续待办

| # | 限制 / 待办 | 影响 | 建议动作 |
|---|---|---|---|
| L1 | **全量 `backend/tests` 回归受 P0 阻塞**：缺 `backend/.venv` 与 `config/app.yaml`，13 个既有模块报 `ModuleNotFoundError`（`sqlalchemy`、`typer`） | 902 专项测试不受影响（仅标准库 + pytest）。QA 已用 `git stash -u` 建立基线对照：改动前后收集错误数**均为 13**，用例数 1 → 34，增量全部来自新增文件，非 902 引入 | venv 就绪后由 QA 补跑全量回归并更新 `p5_qa_report.md` |
| L2 | **`magic-pdf` 未安装**，样本只供**文件级**验证，未做真实解析冒烟 | 004 路由 `pdf_to_mineru` 的真实解析能力仍待验证 | 后续 spec 可用本样本集做 parser 只读冒烟；真实 GPU/模型能力需另立项 |
| L3 | 若 conda 的 `bin` 被前置到 `PATH`，`mysql` 会命中 conda 路径而非系统客户端 | 影响操作员 P0 环境（与 902 无直接关系） | 须显式使用 `/usr/bin/mysql` |
| L4 | `test_sample_dataset.py` 依赖 `git` 命令做「封存 fixture 与 HEAD 比对」 | 若被比对文件不在 HEAD 中，相关断言会自动 skip | QA 观察项 N2：当前两文件确在 HEAD；skip 行为已显式化，不会静默漏测 |
| L5 | 样本为最小构造（PDF 一页纯文本、docx 一段中文），非真实复杂文档 | 不足以覆盖复杂版式 / 表格 / 图片解析 | 后续如需，可另立项扩充样本（不得引入敏感资料） |
| L6 | P6 E2E 报告尚未编写 | 门禁链不完整 | 由 QA 归档脚本幂等 + 不连库证据，写入 `p6_e2e_report.md` |

---

## 10. Risks & Rollback / 风险与回滚

### 10.1 改动性质

902 的仓库改动全部属于以下三类，**无 schema 变更、无数据迁移、无业务代码改动**：

1. **新增文件**：`backend/tests/fixtures/sample_dataset/**`、`scripts/create_sample_dataset.py`、`backend/tests/test_sample_dataset.py`、`docs/sample_dataset.md`、`docs/handoff-902-test-dataset.md`、`specs/902-test-dataset/p2…p5 报告`
2. **`.gitignore` 追加一行**：第 29 行 `data/sample_dataset/`（纯运行时产物，不入库）
3. **旧脚本改为 6 行委派**：`scripts/create_sample_dataset.sh` 由「写入 `fixtures/中文路径/`」改为「委派 Python 生成器」——这本身消除了 P2 识别的 C3 污染风险

### 10.2 回滚步骤（Owner 如需撤销）

```bash
cd /home/szf/dev/doc2md

# 1) 恢复被修改的两个既有文件
git checkout scripts/create_sample_dataset.sh .gitignore

# 2) 删除 902 新增文件与目录
rm -rf backend/tests/fixtures/sample_dataset
rm -f  backend/tests/test_sample_dataset.py
rm -f  scripts/create_sample_dataset.py
rm -f  docs/sample_dataset.md docs/handoff-902-test-dataset.md
rm -f  specs/902-test-dataset/p2_db_review.md \
       specs/902-test-dataset/p3_implementation_gate.md \
       specs/902-test-dataset/p4_kickoff.md \
       specs/902-test-dataset/p5_qa_report.md

# 3) 运行时副本（已被 gitignore，不影响仓库）
rm -rf data/sample_dataset
```

**回滚副作用的评估：无。** 902 不写 MySQL、不改 schema、不改 `backend/app/**`、不触碰 001–013 的封存 fixture 与既有测试，因此回滚不需要任何数据面补偿动作。

**未回滚部分（属 P1 决策，非 902 实现产物，需 Owner 单独决定是否撤销）**：`specs/SPEC_INDEX.md`（902 → ACTIVE）、`README.md` §9.7、`docs/feature_index.md`、`specs/902-test-dataset/spec.md` / `plan.md` / `tasks.md` / `acceptance.md` / `test_cases.md`、`docs/tl-review-2026-09-05-env-and-active-spec.md`。P8 未修改这些文件。

### 10.3 残余风险

| ID | 风险 | 等级 | 说明 |
|---|---|---|---|
| R1 | 样本覆盖面窄（最小构造，非真实复杂文档） | 低 | 见 L5；不影响 902 门禁 |
| R2 | 全量回归未完成（L1） | 低 | 902 专项 33 passed；增量证据已由基线对照固化 |
| R3 | 样本可能被后续 spec 误当作真实数据 scan 入库 | 低 | 样本位于测试 fixture 与 gitignore 的 `data/sample_dataset/`；`docs/sample_dataset.md` 已标注「虚构、非真实业务数据」 |
| R4 | P6 / P7 尚未进行 | 中 | 门禁链不完整；merge 前应完成 |

---

## 11. Repository State at Handoff / 交接时仓库状态

| 项 | 值 |
|---|---|
| Branch | `feature/902-test-dataset` |
| HEAD | `9fa6777` |
| Merged to `main` | **否** |
| `SPEC_INDEX.md` 902 标记 | **ACTIVE / NOT IMPLEMENTED**（未改为 DONE） |
| 工作区 | **脏**（902 产物尚未提交） |

`git status --short`（交接时快照）：

```text
 M .gitignore
 M README.md
 M docs/feature_index.md
 M scripts/create_sample_dataset.sh
 M specs/902-test-dataset/{acceptance,plan,spec,tasks,test_cases}.md
 M specs/SPEC_INDEX.md
?? backend/tests/fixtures/sample_dataset/
?? backend/tests/test_sample_dataset.py
?? docs/sample_dataset.md
?? docs/tl-review-2026-09-05-env-and-active-spec.md
?? scripts/create_sample_dataset.py
?? specs/902-test-dataset/p2_db_review.md
?? specs/902-test-dataset/p3_implementation_gate.md
?? specs/902-test-dataset/p4_kickoff.md
?? specs/902-test-dataset/p5_qa_report.md
?? .codebuddy/            # IDE 会话数据，非 902 交付物
```

其中 `README.md` / `docs/feature_index.md` / `specs/SPEC_INDEX.md` 的改动属 **P1 阶段**（TL 完成，早于本轮 P8），P8 未触碰。

`data/sample_dataset/` 经 `git check-ignore` 确认被忽略（`.gitignore:29`），`git status data/` 为空。

---

## 12. Spec / Review Artifacts / 文档与报告清单

```text
specs/902-test-dataset/spec.md
specs/902-test-dataset/plan.md
specs/902-test-dataset/tasks.md
specs/902-test-dataset/acceptance.md
specs/902-test-dataset/test_cases.md
specs/902-test-dataset/p2_db_review.md
specs/902-test-dataset/p3_implementation_gate.md
specs/902-test-dataset/p4_kickoff.md
specs/902-test-dataset/p5_qa_report.md
specs/902-test-dataset/p6_e2e_report.md        # 尚未创建（P6 未开始）
docs/tl-review-2026-09-05-env-and-active-spec.md
docs/sample_dataset.md
docs/handoff-902-test-dataset.md               # 本文件
```

---

## 13. Next Stage / 下一阶段

**不要自动启动下一个 Spec，也不要自动 merge。**

按 `specs/SPEC_INDEX.md`，当前 ACTIVE spec 仍为 `902-test-dataset`。在启动任何新实现前必须：

1. 阅读 `specs/SPEC_INDEX.md`
2. 执行显式的 **Active Spec Selection Review**
3. **不得**仅凭目录编号推断活跃 Spec

未激活的目录（不得自行启动）：`specs/008-review-workflow/`、`specs/901-docker-compose-deployment/`。

### 13.1 Merge Entry Conditions / merge 准入条件（**需 Owner 显式授权后才可执行**）

- [ ] P6 E2E 报告编写完成（`specs/902-test-dataset/p6_e2e_report.md`）
- [ ] P7 TL Final Review 完成并记录结论
- [ ] 复跑 `PYTHONPATH=backend python3 -m pytest backend/tests/test_sample_dataset.py -q` → 33 passed
- [ ] （可选，依赖 P0）`backend/.venv` 就绪后补跑全量 `backend/tests` 回归并更新 `p5_qa_report.md`
- [ ] 工作区清理并提交 902 产物（含本交接文档）
- [ ] 更新 `specs/SPEC_INDEX.md`：902 → **DONE**，并显式指定下一个 ACTIVE spec
- [ ] 视索引策略同步 `README.md` / `docs/feature_index.md`
- [ ] merge `feature/902-test-dataset` → `main`（**禁止 force push**）

**P8 明确未执行**：merge、`SPEC_INDEX` DONE 更新、分支删除、`tasks.md` 修改。

---

## 14. Quick Commands (new session) / 新会话快速命令

```bash
cd /home/szf/dev/doc2md
git rev-parse --abbrev-ref HEAD
git log --oneline -5
git status --short

# 生成 / 校验样本
bash /home/szf/dev/doc2md/scripts/create_sample_dataset.sh
bash /home/szf/dev/doc2md/scripts/create_sample_dataset.sh --skip-sync

# 测试
PYTHONPATH=backend python3 -m pytest backend/tests/test_sample_dataset.py -q

# 复核 sha256
find backend/tests/fixtures/sample_dataset -type f | sort | xargs sha256sum

# 确认运行时副本未入库
git check-ignore -v data/sample_dataset
git status data/
```

建议的 P8 提交（**仅当 Owner 要求时**执行）：

```bash
cd /home/szf/dev/doc2md
git add docs/handoff-902-test-dataset.md
git commit -m "$(cat <<'EOF'
docs(902): add test-dataset handoff

Handoff only. Not merged; SPEC_INDEX left ACTIVE.
EOF
)"
```

---

## 15. Handoff Confirmation Checklist / 交接确认清单

- [x] 分支与 HEAD 已记录（`feature/902-test-dataset` @ `9fa6777`）
- [x] P1–P5 门禁链与 Owner 结论已记录，P6/P7 标注为未开始
- [x] 交付物清单完整（含 `p6_e2e_report.md` 不存在的说明）
- [x] 生成命令、参数、幂等行为、路径逃逸与封存保护（退出码 2）已记录
- [x] 8 样本清单含真实 sha256（源自 `manifest.yaml`，已与磁盘复核）
- [x] 测试命令与结果（33 passed）已记录，TC001–TC015 映射完整
- [x] A001–A016 与 C1–C8 结论已汇总
- [x] 已知限制与后续待办（L1–L6）已记录
- [x] 回滚步骤与残余风险已记录
- [x] 仓库状态与「未 merge」已明确声明
- [x] merge 准入条件列为待 Owner 授权，未自动执行
- [x] **全文无口令 / 密钥 / token / 真实证件号**；已声明 8 个样本全为虚构内容
- [x] 未修改 `SPEC_INDEX.md` / `feature_index.md` / `README.md` / `tasks.md`；未把 902 标为 DONE

---

## 16. Final Status / 最终状态

**902 Test Dataset：实现 DONE 于分支 `feature/902-test-dataset`；P5 QA PASS；P8 交接完成。**

**状态：未 merge。** 是否 merge 到 `main`、是否把 902 标记为 DONE，**均需 Owner 显式授权**。

P6 E2E 与 P7 TL Final Review 尚未进行（`p6_e2e_report.md` 不存在），建议在上述两项完成后再评估 merge。

**STOP** — Handoff Agent P8 complete. 本轮仅新建 `docs/handoff-902-test-dataset.md`，未创建或修改任何其他文件。
