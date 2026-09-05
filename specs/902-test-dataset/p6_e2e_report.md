# 902 Test Dataset — P6 E2E Report

> Role: QA Agent (P6 E2E)
> Spec: `specs/902-test-dataset/`
> Stage: P6 E2E（独立重跑，**不复用 P5 结论**）
> Branch: `feature/902-test-dataset` @ `9fa6777`
> 日期：2026-09-05
> 被测对象：DEV P4 交付（`scripts/create_sample_dataset.sh` + `scripts/create_sample_dataset.py` + fixture 树 + `test_sample_dataset.py`）

---

## 1. Gate Conclusion

**P6-GATE: PASS**

| 项 | 结果 |
|---|---|
| E2E 场景 A（全新环境生成） | **PASS** |
| E2E 场景 B（重复执行幂等） | **PASS**（字节 + mtime 双不变） |
| E2E 场景 C（封存资产保护） | **PASS**（HEAD 字节一致 + 退出码 2） |
| 不连库 / 无 parser（运行时探针） | **PASS**（探针零触发） |
| 无扩散（白名单收敛） | **PASS** |
| 回归 `test_sample_dataset.py` | **33 passed / 0 failed / 0 skipped** |
| 全量 `backend/tests` | **BLOCKED（P0 存量）** — 非 902 引入 |
| 阻断缺陷 | **0** |
| 观察项 | 3（均不影响门禁，见 §8） |

未发现需退回 DEV 的缺陷。

---

## 2. 环境与前置

| 项 | 值 |
|---|---|
| 工作目录 | `/home/szf/dev/doc2md` |
| 分支 | `feature/902-test-dataset` |
| HEAD | `9fa6777` |
| Python | 3.10.12 |
| pytest | 9.1.1 |
| git | 2.34.1 |
| venv | **无**（系统解释器直跑；902 声明不依赖 venv） |

**P5 结论引用**（`specs/902-test-dataset/p5_qa_report.md`）：P5-GATE **PASS**，`test_sample_dataset.py` 33 passed，A001–A016 全 PASS，P2 约束 C1–C8 全满足，阻断缺陷 0。P6 在此基础上**独立重跑全部场景**，未复用其取证。

**E2E 起点基线**（执行前 `git status --porcelain`）已采集，共 22 项，全部为 P1–P3 既有改动 + 902 白名单新增；黑名单路径为空。

---

## 3. E2E 场景

### 3.1 场景 A — 全新环境生成

**命令**

```bash
cd /home/szf/dev/doc2md
rm -rf data/sample_dataset
bash scripts/create_sample_dataset.sh
```

**原始输出**

```text
=== A1: 删除 data/sample_dataset ===
总计 8
drwxrwxr-x  2 szf szf 4096  9月  5 23:24 .
drwxrwxr-x 15 szf szf 4096  9月  5 23:23 ..
-rw-rw-r--  1 szf szf    0  9月  5 17:55 .gitkeep
=== A2: 运行脚本 ===
fixture_root=/home/szf/dev/doc2md/backend/tests/fixtures/sample_dataset
runtime_root=/home/szf/dev/doc2md/data/sample_dataset
files=8
EXIT=0
```

**校验 1：runtime 副本文件数 = 9（8 样本 + manifest.yaml）**

```text
9
```

**校验 2：runtime 9 文件 sha256**

```text
5006fc8135eb8f8712bbc17e36f0621692a298dabe2a25630733c541e95f6052  ./manifest.yaml
b4fbe3cf92797699e508b655e25b48d0b1c8bd2acb39b57790a16ef68f612921  ./office_like/最小文档.docx
6e1d00daac00131253bd3185d02c5a0d0905a883470df14181b64194a23d633d  ./pdf/hello.pdf
5b20428aee53e47483d644632f55154c5a038e19cbcaf63459398f14fd62e49e  ./中文路径/银行项目/会议纪要.md
8990fe005a7d3191dfe95b6abb69c6ccec639879b53742614fa9c930ab562f13  ./中文路径/银行项目/元数据.json
a073424c2edff9d8bb4c48e90c5a054d5998ad5d54658aceef796be5f0a5a287  ./中文路径/银行项目/指标口径.csv
f70c9fd72023780761d7203b1257ca1141d30a02549d10c5bfae39225af3bc9c  ./中文路径/银行项目/方案.txt
f70c9fd72023780761d7203b1257ca1141d30a02549d10c5bfae39225af3bc9c  ./中文路径/银行项目/方案副本.txt
ce1bd539613e9ccc0a02b1ef580bab0bdd8b7464e0f18b82e2f7f961aebdff2d  ./中文路径/银行项目/说明.html
```

**校验 3：与 fixture 逐字节一致**

```bash
diff -r backend/tests/fixtures/sample_dataset data/sample_dataset
```

```text
DIFF_R: IDENTICAL (exit=0)
```

**校验 4：脚本未改动已提交 fixture 字节**（A0 基线 vs 运行后）

```text
FIXTURE_UNCHANGED: PASS
```

> 附带确认：重复对 `方案.txt` / `方案副本.txt` sha256 同为 `f70c9fd720237807…`（A004）；其余 6 样本互异（A005）；docx `b4fbe3cf…`（与 T012 修复后一致，993 B）。

**判定：PASS**（exit 0；9 文件齐全；与 fixture 逐字节一致；fixture 未被改写）

---

### 3.2 场景 B — 重复执行幂等（字节 + mtime 双证据）

**命令**（第二次、第三次执行，各间隔 2 秒；记录 sha256 + 纳秒级 mtime）

```bash
sleep 2 && bash scripts/create_sample_dataset.sh
(cd data/sample_dataset && find . -type f | LC_ALL=C sort | \
   while read -r f; do printf '%s  %s  %s\n' "$(sha256sum "$f"|cut -d' ' -f1)" "$(stat -c '%.9Y' "$f")" "$f"; done)
```

**第二次执行（RUN1）**

```text
fixture_root=/home/szf/dev/doc2md/backend/tests/fixtures/sample_dataset
runtime_root=/home/szf/dev/doc2md/data/sample_dataset
files=8
EXIT=0

5006fc8135eb8f8712bbc17e36f0621692a298dabe2a25630733c541e95f6052  1788621866.067089647  ./manifest.yaml
b4fbe3cf92797699e508b655e25b48d0b1c8bd2acb39b57790a16ef68f612921  1788621866.067089647  ./office_like/最小文档.docx
6e1d00daac00131253bd3185d02c5a0d0905a883470df14181b64194a23d633d  1788621866.067089647  ./pdf/hello.pdf
5b20428aee53e47483d644632f55154c5a038e19cbcaf63459398f14fd62e49e  1788621866.066089603  ./中文路径/银行项目/会议纪要.md
8990fe005a7d3191dfe95b6abb69c6ccec639879b53742614fa9c930ab562f13  1788621866.066089603  ./中文路径/银行项目/元数据.json
a073424c2edff9d8bb4c48e90c5a054d5998ad5d54658aceef796be5f0a5a287  1788621866.066089603  ./中文路径/银行项目/指标口径.csv
f70c9fd72023780761d7203b1257ca1141d30a02549d10c5bfae39225af3bc9c  1788621866.066089603  ./中文路径/银行项目/方案.txt
f70c9fd72023780761d7203b1257ca1141d30a02549d10c5bfae39225af3bc9c  1788621866.066089603  ./中文路径/银行项目/方案副本.txt
ce1bd539613e9ccc0a02b1ef580bab0bdd8b7464e0f18b82e2f7f961aebdff2d  1788621866.066089603  ./中文路径/银行项目/说明.html
```

**第三次执行（RUN2）**：输出与 RUN1 完全相同（`files=8`，`EXIT=0`）。

**判定**

```text
=== B: 对比 RUN1 vs RUN2 (sha256 + mtime) ===
B_RESULT: SHA256_AND_MTIME_IDENTICAL — 走 skip 分支，未改写 (exit=0)
```

`diff` 为空 → 9 个文件的 **sha256 与纳秒级 mtime 全部未变**，证明 `write_bytes_if_changed()` 走 skip 分支，脚本未重写目标文件（A009 / C5 强证据，而非仅哈希相同）。

---

### 3.3 场景 C — 封存资产保护（C3 闭环）

**C1：git 逐字节比对（工作树 vs HEAD）**

```bash
git diff --stat -- "backend/tests/fixtures/中文路径/"
git hash-object <file>  vs  git rev-parse HEAD:<file>
```

```text
=== C1: git 逐字节比对（工作树 vs HEAD）===
git-diff-empty=0        # git diff --stat 无输出
方案.txt     worktree_blob=34a6a783f167992e757bc4637e2defa94aa9646d  HEAD_blob=34a6a783f167992e757bc4637e2defa94aa9646d  MATCH
方案副本.txt  worktree_blob=34a6a783f167992e757bc4637e2defa94aa9646d  HEAD_blob=34a6a783f167992e757bc4637e2defa94aa9646d  MATCH
=== C1b: git status 该目录 ===
(空=无改动)
```

**C2 / C3：脚本以封存目录为 `--fixture-root` 的退出码**

```bash
bash scripts/create_sample_dataset.sh --fixture-root "backend/tests/fixtures/中文路径"
bash scripts/create_sample_dataset.sh --fixture-root "backend/tests/fixtures/中文路径/银行项目"
```

```text
=== C2 ===
refusing to write sealed fixtures/中文路径        # stderr
EXIT=2
=== C3 ===
refusing to write sealed fixtures/中文路径        # stderr
EXIT=2
```

两条路径（封存根、封存子目录）均 **exit 2**，且错误信息写入 **stderr**（符合 plan §4「错误信息写 stderr」）。

**C4 / C5：拒绝后封存目录仍完好**

```text
方案.txt  MATCH
方案副本.txt  MATCH
封存目录文件总数 = 2
backend/tests/fixtures/中文路径/银行项目/方案.txt
backend/tests/fixtures/中文路径/银行项目/方案副本.txt
```

**判定：PASS**（A008 / C3 闭环；拒绝逻辑在写入前生效，未产生任何新文件）

---

## 4. 不连库与无 parser 证据（运行时探针）

QA 自造**运行时陷阱**（`/tmp/p6-probe`，用完已删除），而非静态扫描源码。

### 4.1 探针构造

```text
/tmp/p6-probe/stub/pymysql.py                 → raise AssertionError("PROBE-TRIGGERED: pymysql imported — DB access attempted")
/tmp/p6-probe/stub/mysql/__init__.py          → raise AssertionError("PROBE-TRIGGERED: mysql connector imported — DB access attempted")
/tmp/p6-probe/stub/sqlalchemy/__init__.py     → raise AssertionError("PROBE-TRIGGERED: sqlalchemy imported — DB access attempted")
/tmp/p6-probe/stub/sqlalchemy/engine.py       → raise AssertionError("PROBE-TRIGGERED: sqlalchemy.engine imported — DB access attempted")
/tmp/p6-probe/bin/{mysql,mysqladmin,magic-pdf,markitdown,mineru}   # 可执行，调用即 stderr 输出 PROBE-TRIGGERED 并 exit 97
```

注入方式：`PYTHONPATH=/tmp/p6-probe/stub`、`PATH=/tmp/p6-probe/bin:$PATH`。

### 4.2 探针自检（证明陷阱已武装，避免「假阴性」）

```text
PYTHONPATH=/tmp/p6-probe/stub python3 -c "import pymysql"
  AssertionError: PROBE-TRIGGERED: pymysql imported — DB access attempted   (rc=1)
PYTHONPATH=/tmp/p6-probe/stub python3 -c "import sqlalchemy"
  AssertionError: PROBE-TRIGGERED: sqlalchemy imported — DB access attempted
PYTHONPATH=/tmp/p6-probe/stub python3 -c "import sqlalchemy.engine"
  AssertionError: PROBE-TRIGGERED: sqlalchemy imported — DB access attempted
PATH=/tmp/p6-probe/bin:$PATH magic-pdf --version
  PROBE-TRIGGERED: CLI magic-pdf invoked        (exit 97)
```

### 4.3 探针下运行结果

| 命令 | 结果 |
|---|---|
| `PYTHONPATH=<stub> PATH=<bin>:$PATH bash scripts/create_sample_dataset.sh`（`data/` 已清空，全新生成） | `files=8`，**EXIT=0** |
| 同上，再跑一次 | `files=8`，**EXIT=0** |
| `PYTHONPATH=backend:<stub> PATH=<bin>:$PATH python3 -m pytest backend/tests/test_sample_dataset.py -q` | **33 passed in 0.17s**，PYTEST_EXIT=0 |

**触发检查**

```text
=== D3: PROBE 触发检查 ===
PROBE: ZERO TRIGGER (PASS) — 脚本未 import pymysql/sqlalchemy，未调用 mysql/magic-pdf/markitdown/mineru

=== D5: pytest 期间 PROBE 触发检查 ===
PROBE: ZERO TRIGGER (PASS)
```

对全部 stdout+stderr 输出 `grep PROBE-TRIGGERED` 均无命中（ShellCheck 式 grep 在脚本侧与测试侧均为 0）。

### 4.4 清理确认

```text
/tmp/p6-probe 已删除
/tmp 无 p6* 残留
```

**判定：PASS**（C1 / A011 / A012 强证据：探针已自检武装，运行期零触发）

---

## 5. 无扩散证据

### 5.1 `git status --porcelain`（全部 E2E 跑完后）

```text
 M .gitignore
 M README.md
 M docs/feature_index.md
 M scripts/create_sample_dataset.sh
 M specs/902-test-dataset/acceptance.md
 M specs/902-test-dataset/plan.md
 M specs/902-test-dataset/spec.md
 M specs/902-test-dataset/tasks.md
 M specs/902-test-dataset/test_cases.md
 M specs/SPEC_INDEX.md
?? .codebuddy/
?? backend/tests/fixtures/sample_dataset/
?? backend/tests/test_sample_dataset.py
?? docs/handoff-902-test-dataset.md
?? docs/sample_dataset.md
?? docs/tl-review-2026-09-05-env-and-active-spec.md
?? scripts/create_sample_dataset.py
?? specs/902-test-dataset/p2_db_review.md
?? specs/902-test-dataset/p3_implementation_gate.md
?? specs/902-test-dataset/p4_kickoff.md
?? specs/902-test-dataset/p5_qa_report.md
```

全部为 **P1–P3 既有改动 + 902 白名单新增**。新增项仅：`.codebuddy/`（IDE 元数据）、`backend/tests/fixtures/sample_dataset/`（白名单）、`backend/tests/test_sample_dataset.py`（白名单）、`docs/sample_dataset.md`（白名单）、`scripts/create_sample_dataset.py`（白名单）、`docs/tl-review-...md`（P1）、`specs/902-test-dataset/p2..p5`（P1–P5 报告）、`docs/handoff-902-test-dataset.md`（**非 902 脚本产物，见观察项 N1**）。

### 5.2 黑名单路径核查

```bash
git status --porcelain -- backend/app sql config frontend \
   "backend/tests/fixtures/中文路径" "backend/tests/fixtures/parse_quality_report_*.json" backend/requirements.txt
```

```text
(空=无扩散)
```

`backend/tests/` 下仅两项新增（`sample_dataset/`、`test_sample_dataset.py`），**既有 `test_*.py` 无一被修改**。

### 5.3 `.gitignore` 改动幅度

```text
 .gitignore | 1 +
 1 file changed, 1 insertion(+)
+data/sample_dataset/        (.gitignore:29)
```

`git check-ignore -v data/sample_dataset` → `.gitignore:29:data/sample_dataset/`，`git status --porcelain -- data/` 为空 → **运行时副本未入库**（C4 / plan §3 锁定项达成）。

### 5.4 全仓字节快照（脚本一次运行前后）

对全仓（排除 `.git` / `.pytest_cache` / `.codebuddy`）做 `sha256sum` 快照，脚本运行一次后再快照：

```text
=== G3: 全仓 sha256 快照 diff（脚本一次运行前后）===
REPO_STABLE: 无任何文件字节变化（含黑名单与已提交 fixture）
```

**判定：PASS**（A010 / A013 / A014；脚本写路径完全收敛，且对已提交内容零写入）

---

## 6. 回归结果

### 6.1 目标回归

```bash
PYTHONPATH=backend python3 -m pytest backend/tests/test_sample_dataset.py -q
```

```text
.................................                                        [100%]
33 passed in 0.18s
```

**33 passed / 0 failed / 0 skipped**。`-rs -rA` 下无 `SKIPPED` / `XFAIL` / `XPASS` / `ERROR` / `FAILED` 行——即封存 fixture 比对等依赖 `git` 的用例 **未发生静默 skip**（消解 P5 观察项 N2）。

### 6.2 全量 `backend/tests` — **BLOCKED（P0 存量）**

```bash
PYTHONPATH=backend python3 -m pytest backend/tests -q --collect-only
```

```text
34 tests collected, 13 errors in 0.20s
!!!!!!!!!!!!!!!!!!! Interrupted: 13 errors during collection !!!!!!!!!!!!!!!!!!!

ERROR backend/tests/test_curated_project_assets.py
ERROR backend/tests/test_duplicate_governance.py
ERROR backend/tests/test_evidence_chain.py
ERROR backend/tests/test_file_content_vault.py
ERROR backend/tests/test_inventory_scanner.py
ERROR backend/tests/test_markitdown_parser.py
ERROR backend/tests/test_mineru_pdf_parser.py
ERROR backend/tests/test_parse_job_registry.py
ERROR backend/tests/test_parse_quality_checker.py
ERROR backend/tests/test_parse_quality_report_summarizer.py
ERROR backend/tests/test_parser_router.py
ERROR backend/tests/test_search_service.py
ERROR backend/tests/test_streamlit_admin_lib.py
```

根因（与 902 无关）：

```text
backend/app/models/parse_registry.py:5: in <module>
    from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
E   ModuleNotFoundError: No module named 'sqlalchemy'
```

**判定：BLOCKED**（缺 `sqlalchemy` / `typer`，P0 环境未就绪的**存量**问题）。与 P5 基线一致：收集错误数前后均为 **13**，收集用例数 `1 → 34`（+33 全部来自新增 `test_sample_dataset.py`），**非 902 引入，也未破坏既有测试意图**。按指令未安装依赖、未做任何修补。venv 就绪后应由 QA 补跑。

---

## 7. P2 约束 C1–C8 复核

| ID | 约束 | E2E 证据 | 结论 |
|---|---|---|---|
| C1 | 运行时不连 MySQL | §4 探针零触发 | PASS |
| C2 | 不改 SQL / ORM | §5.2 `sql/**` 空 | PASS |
| C3 | 不写 `fixtures/中文路径/**` | §3.3 exit 2 + HEAD 字节 MATCH | PASS |
| C4 | 只写 `sample_dataset/` + `data/sample_dataset/` | §5.4 全仓快照零变化 | PASS |
| C5 | 同 sha256 跳过 | §3.2 mtime 未变 | PASS |
| C6 | manifest 字段非 DB 列 | 无 DB 触及（§4） | PASS |
| C7 | 不调用 001–013 CLI | §4 无子进程调用 | PASS |
| C8 | 不写 review / embedding 表 | §4 零 DB 访问 | PASS |

---

## 8. 观察项 / 残留风险

| # | 观察 | 影响 | 建议 |
|---|---|---|---|
| N1 | `docs/handoff-902-test-dataset.md`（**0 字节**）于本次 E2E 运行期间（2026-09-05 23:25:26）出现在工作区。该文件**不是** 902 脚本产物（脚本只写 fixture 树与 `data/sample_dataset/`，见 §5.4 全仓快照零变化），判断为其他角色/TL 提前放置 P8 占位。`p3_implementation_gate.md` §3 将 `docs/handoff-*.md` 列入**禁止修改**清单 | 不影响 P6 门禁；但 P3 黑名单项被创建，需 TL 裁决 | P7 由 TL 确认：该文件属 P8 正常产物（则应在 P8 阶段创建并授权），还是应移出当前工作区。QA 不删除、不改写 |
| N2 | 全量 `backend/tests` 回归受 P0 阻塞：13 个既有模块因缺 `sqlalchemy` / `typer` 无法收集 | 902 不可归因；与改动前基线一致 | 操作员完成 `backend/.venv` 后，由 QA 补跑全量回归并更新本报告 |
| N3 | 探针运行期在 `/tmp` 桩目录生成 `__pycache__`；已随探针目录一并删除，未污染仓库 | 无 | 无 |

**无缺陷（Defect）记录。** 未发现需退回 DEV 的阻断项。

---

## 9. 结论

**P6-GATE: PASS。**

E2E 三个场景（全新环境生成 / 重复执行幂等 / 封存资产保护）全部 PASS；不连库与无 parser 经**已自检武装的运行时探针**验证为零触发；写路径收敛经 `git status` 黑名单核查与**全仓 sha256 快照**双重确认；目标回归 33 passed / 0 skipped。全量回归因 P0 环境未就绪 BLOCKED，属存量问题且不可归因于 902。

**允许进入 P7 TL Final Review。**

---

### QA 纪律声明

- QA 未修改任何被验证对象（未改 `scripts/**`、`backend/tests/**`、`docs/sample_dataset.md`、`.gitignore`）。
- 未改 `tasks.md`；未改 `p5_qa_report.md`。
- 未替 DEV 修补任何缺陷；本轮无缺陷需修补。
- 未执行 merge；未做出「可 merge」判断；未自签验收。
- 临时探针与中间产物全部置于 `/tmp`，已清理（§4.4）；仓库工作区除 902 白名单外无新增残留。
- 本报告为 P6 唯一产出文件。
