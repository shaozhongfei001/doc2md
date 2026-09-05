# 902 Test Dataset — P3 Implementation Gate

> Role: Tech Lead Agent  
> Spec: `specs/902-test-dataset/`  
> Stage: P3 Implementation Gate  
> P2: `p2_db_review.md` — **PASS WITH CONSTRAINTS**  
> Status: **P3-GATE PASS — P4 待你确认后才允许 Dev 改白名单文件**

---

## 1. Gate Conclusion

P3 Implementation Gate: **PASS**

902 获准在你确认后进入 **P4 Dev Implementation**。

P2 约束 **C1–C8** 全部锁定。无 schema migration。无 `backend/app/**`。

若 P4 需要连库、改 SQL、改 sealed fixture、调用 parser / CLI → **STOP**，回 TL + DB。

建议分支（P4 入口创建，不在 `main` 上实现）：

```text
feature/902-test-dataset
```

---

## 2. Inherited P2 Constraints (Locked)

| ID | P4 执行方式 |
|---|---|
| C1 | 脚本与 `test_sample_dataset.py` 不连 MySQL |
| C2 | 不碰 `sql/**`、`backend/app/models/**` |
| C3 | 停止写入 `backend/tests/fixtures/中文路径/**` |
| C4 | 只写 `sample_dataset/` fixture + `data/sample_dataset/` |
| C5 | 同 sha256 跳过；禁止广域 `rm -rf` |
| C6 | manifest 字段不是 DB 列 |
| C7 | 不调用 scan / vault / parse / quality / search / streamlit |
| C8 | 不写 review / embedding 表 |

---

## 3. P4 Dev File Whitelist

Dev **仅可创建或修改**：

```text
scripts/create_sample_dataset.sh
scripts/create_sample_dataset.py          # 可选
backend/tests/fixtures/sample_dataset/**
backend/tests/test_sample_dataset.py
docs/sample_dataset.md
.gitignore                                # 仅追加 data/sample_dataset/
specs/902-test-dataset/tasks.md           # 仅勾选
```

**禁止修改：**

```text
backend/app/**
backend/tests/test_*.py                  # 除 test_sample_dataset.py
backend/tests/fixtures/中文路径/**
backend/tests/fixtures/parse_quality_report_*.json
backend/tests/fixtures/evidence_chain_*/**
backend/tests/fixtures/curated_project/**
backend/tests/fixtures/search/**
backend/requirements.txt
sql/**
config/**
frontend/**
docs/handoff-*.md
specs/001–013 五件套
specs/008-review-workflow/**
specs/901-docker-compose-deployment/**
```

---

## 4. P4 必交付

对照 `tasks.md` T001–T011 与 `acceptance.md` A001–A016：

1. spec §3.1 全套样本（含中文路径、精确重复对、最小 docx、最小 PDF）
2. `manifest.yaml` 与磁盘一致
3. 脚本幂等同步到 `data/sample_dataset/`
4. 旧 `fixtures/中文路径` 两文件字节不变
5. `docs/sample_dataset.md`（无密码；P0 冒烟标为操作员可选）
6. `test_sample_dataset.py` 覆盖 `test_cases.md` 主路径

`create_sample_dataset.py` 仅用于本地生成 PDF/docx，**不得**放进 `backend/app/services/`。

---

## 5. 明确不做（再锁一次）

- 不建 venv、不写 `config/app.yaml`、不装 `magic-pdf`
- 不跑 `scan` / `parse-markitdown` / `parse-mineru-pdf`
- 不实现 008-review-workflow / 901
- 不重开 001–013

---

## 6. Dev 交接模板

```text
当前角色：DEV
当前 Spec：specs/902-test-dataset
当前分支：feature/902-test-dataset（从 main 拉出）
当前步骤：④ P4 Implementation
TL 批准的文件白名单：见本文件 §3
禁止修改：backend/app/**、sql/**、fixtures/中文路径/**、原始文件、vault/parsed/curated

请先读 tasks.md，再读 spec.md / plan.md / p2_db_review.md / acceptance.md。
完成后 STOP → QA。不要自我验收，不要 merge。
```

---

## 7. Exit

- [x] P2 PASS WITH CONSTRAINTS
- [x] 白名单与 `plan.md` §6 一致
- [x] C3 写入门禁（旧脚本污染路径）
- [ ] **你确认后** Dev 才改白名单文件

**STOP。** TL 不写 P4 代码。
