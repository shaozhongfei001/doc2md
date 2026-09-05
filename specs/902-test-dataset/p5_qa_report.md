# 902 Test Dataset — P5 QA Report

> Role: QA Agent
> Spec: `specs/902-test-dataset/`
> Stage: P5 QA
> Branch: `feature/902-test-dataset`
> 被测对象：DEV P4 交付（T006/T007/T008/T009/T011/T012）
> 日期：2026-09-05

---

## 1. Gate Conclusion

**P5-GATE: PASS**

| 项 | 结果 |
|---|---|
| `test_sample_dataset.py` | **33 passed / 0 failed** |
| A001–A016 | **16 项全 PASS**（A016 为 PASS WITH NOTES） |
| P2 约束 C1–C8 | 全部满足 |
| 阻断缺陷 | **0** |
| 观察项 | 2（均不影响门禁，见 §6） |

未发现需要退回 DEV 的缺陷。**可进入 P6 E2E / P7 TL Final Review。**

---

## 2. 测试执行

```bash
PATH=<probe-bin>:$PATH PYTHONPATH=backend:<probe-stub> \
  python3 -m pytest backend/tests/test_sample_dataset.py -q
```

结果：`33 passed in 0.17s`（Python 3.10.12 / pytest 9.1.1 / rootdir 仓库根）

覆盖映射（`test_cases.md`）：

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

---

## 3. 验收矩阵 A001–A016

| ID | 标准 | 结论 | 证据 |
|---|---|---|---|
| A001 | 活跃 Spec 对齐 | **PASS** | 分支 `feature/902-test-dataset`；diff 仅 902 白名单 + P1–P3 既有改动；无 008/901/014 内容 |
| A002 | 样本清单完整 | **PASS** | 8 个样本与 `spec.md` §3.1 逐项对应，全部非空 |
| A003 | 中文路径 | **PASS** | `中文路径/银行项目/` 两层中文目录 + 中文文件名，UTF-8 可读 |
| A004 | 精确重复 | **PASS** | `方案.txt` / `方案副本.txt` 同为 `f70c9fd720237807…` |
| A005 | 非重复互异 | **PASS** | 其余 6 个两两互异，无碰撞 |
| A006 | 路由覆盖文件 | **PASS** | `office_like/最小文档.docx`、`pdf/hello.pdf` 各 1 |
| A007 | 无敏感内容 | **PASS** | 见 §4 独立严格扫描（含 docx 内部 XML / PDF 原始字节） |
| A008 | 既有 fixture 只读 | **PASS** | 与 `git HEAD` 逐字节比对，两文件均未变 |
| A009 | 脚本幂等 | **PASS** | 见 §5.1，字节 + mtime 双重证据 |
| A010 | 写路径收敛 | **PASS** | 见 §5.2，全仓快照对照 |
| A011 | 零数据库 | **PASS** | 见 §5.3 运行时探针 |
| A012 | 零 parser | **PASS** | 见 §5.3 运行时探针 |
| A013 | 无业务代码 | **PASS** | `git status backend/app sql config frontend` 全空 |
| A014 | 原始文件安全 | **PASS** | 脚本无 `rm` / `rmtree` / `unlink`；无移动、重命名 |
| A015 | 文档 | **PASS** | `docs/sample_dataset.md`：生成命令（3 处）、禁止事项章节、无密钥 |
| A016 | 测试 | **PASS WITH NOTES** | 33 passed；既有测试**未被修改**，收集错误数与改动前一致（见 §5.4） |

**总体：PASS**。notes 未触及 A001/A011/A012/A013/A014 任一禁止否定项。

---

## 4. A007 独立敏感扫描（QA 自执行，不复用 DEV 断言）

QA 自行构造更严的正则集，覆盖 docx 内部 XML 与 PDF 原始字节：

| 模式 | 命中 |
|---|---|
| 身份证 18 位 `\d{17}[\dXx]` | 无 |
| 银行卡 13–19 位 | 无 |
| 手机号 `1[3-9]\d{9}` | 无 |
| 邮箱 | 无 |
| 私钥 `BEGIN ... PRIVATE KEY` | 无 |
| 口令赋值 `(password\|passwd\|pwd\|secret\|api_key\|token)\s*[=:]` | 无 |
| `IP:PORT` | 无 |
| `AKIA...` | 无 |

结果：**CLEAN**。

---

## 5. 关键取证

### 5.1 A009 脚本幂等

隔离目录连续两次运行（间隔 2 秒）：

```
第一次：exit=0  files=8
第二次：exit=0  files=8

mtime 对比：MTIME IDENTICAL   ← 同内容跳过，未改写文件
字节对比：  diff -r fixture runtime → 全等
           diff -r 仓库fixture 隔离生成结果 → 全等
```

默认目标（`data/sample_dataset/`）同样验证：清空后生成、再运行一次，9 个文件 sha256 清单 `diff` 无差异。

> D-01 复核：修复前 docx 每次生成字节不同；修复后同脚本相隔 1.2 秒两次生成 docx 字节一致（993 B，`b4fbe3cf…`）。**缺陷已闭环。**

### 5.2 A010 写路径收敛

全仓（`find`，排除 `.git`）mtime + size 快照，执行 pytest 与默认目标重跑后再次快照：

```
唯一差异：./.pytest_cache/v/cache/nodeids   ← pytest 自身缓存，非脚本产物
```

结论：脚本只写 fixture 树与 `data/sample_dataset/`。`data/sample_dataset/` 经 `git check-ignore` 确认为忽略（`.gitignore:29`），`git status data/` 为空。

### 5.3 A011 / A012 零数据库、零 parser（运行时探针）

QA 构造**运行时陷阱**，而非仅静态扫描：

- `PYTHONPATH` 注入假 `pymysql` / `sqlalchemy` / `sqlalchemy.engine`，一旦 import 立即抛错
- `PATH` 前置屏蔽 `mysql` / `mysqladmin` / `magic-pdf` / `markitdown` / `mineru`，一旦调用以 97 退出

结果：脚本两次运行 `exit=0`、pytest `33 passed`，**探针零触发**。

源码静态复核：脚本中 `markitdown` / `mineru` / `magic-pdf` 字样仅出现在 `purpose` 描述与 `intended_route` 元数据，无 import、无调用、无子进程。网络调用扫描仅命中 OOXML 命名空间字符串（非真实请求）。

### 5.4 A016 既有测试意图未被破坏

改动前后对照（`git stash -u` 建立基线）：

| | 收集错误 | 收集用例数 |
|---|---|---|
| BASELINE（改动前） | 13 | 1 |
| AFTER（改动后） | 13 | 34 |

- 收集错误数**完全一致**，均为 P0 环境未就绪导致的 `ModuleNotFoundError`（`sqlalchemy`、`typer`）
- 用例数 +33，全部来自新增的 `test_sample_dataset.py`
- 既有 `backend/tests/test_*.py` **无一个被修改**（`git status` 仅显示新增项）

---

## 6. 观察项（不构成缺陷，供 TL/HO 记录）

| # | 观察 | 建议 |
|---|---|---|
| N1 | 全量 `backend/tests` 回归受 P0 阻塞：13 个既有模块因缺 `sqlalchemy` / `typer` 无法收集 | 操作员完成 `backend/.venv` 后，由 QA 补跑全量回归并更新本报告 |
| N2 | `test_sample_dataset.py` 依赖 `git` 命令做「封存 fixture 与 HEAD 比对」；若文件不在 HEAD 中会自动 skip | 可接受：两文件确在 HEAD；skip 行为已显式化，不会静默漏测 |

---

## 7. 白名单合规复核

| 白名单文件 | 状态 |
|---|---|
| `scripts/create_sample_dataset.sh` | 已改（仅 6 行委派 + 2 行注释） |
| `scripts/create_sample_dataset.py` | 新增 |
| `backend/tests/fixtures/sample_dataset/**` | 新增（8 样本 + manifest） |
| `backend/tests/test_sample_dataset.py` | 新增 |
| `docs/sample_dataset.md` | 新增 |
| `.gitignore` | 仅追加 1 行（第 29 行） |
| `specs/902-test-dataset/tasks.md` | 仅勾选 |

**未触碰黑名单**：`backend/app/**`、`sql/**`、`backend/tests/fixtures/中文路径/**`、`fixtures/parse_quality_report_*.json`、`fixtures/evidence_chain_*`、`fixtures/curated_project/**`、`fixtures/search/**`、`backend/requirements.txt`、`config/**`、`frontend/**`、specs 001–013 五件套。

C3 闭环确认：旧脚本原文 `mkdir -p backend/tests/fixtures/中文路径/银行项目` + `echo`/`cp` 写入已完全移除，改为委派 Python 生成器，且 `main()` 对封存路径返回退出码 2。

---

## 8. 结论

**P5 QA PASS。**

DEV 交付满足 A001–A016 与 C1–C8。零阻断缺陷。

**STOP → P6 E2E**（脚本幂等 + 不连库证据归档）与 **P7 TL Final Review**。

QA 未修改任何被测代码；未替 DEV 修补；未自签 merge。
