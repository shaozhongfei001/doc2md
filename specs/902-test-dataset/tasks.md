# Tasks: 902 测试样本集

> Spec: `specs/902-test-dataset/`  
> 当前阶段：`P7 TL Final Review — PASS`  
> 实现状态：`实现完成并验收通过`（P5 QA PASS → P6 E2E PASS → P7 PASS；33 passed / 0 skipped；A001–A016 全 PASS；C1–C8 全满足）  
> 下一步：**等待 Owner 授权** commit / merge / 改 DONE。TL 未执行任何一项。  
> 裁决与缺陷单：`specs/902-test-dataset/p4_kickoff.md`

---

## 1. 角色

| 阶段 | 角色 | 职责 |
|---|---|---|
| P1 | TL | 选型、五件套、SPEC_INDEX |
| P2 | DB | 确认无 schema / 无 DML |
| P3 | TL | Implementation gate + 白名单复核 |
| P4 | DEV | 仅白名单实现 |
| P5 | QA | pytest |
| P6 | QA / E2E | 脚本幂等 + 不连库证据 |
| P7 | TL | Final Review |
| P8 | HO | handoff（不 merge，除非你授权） |

---

## 2. P1 — Tech Lead Plan

Owner: TL

- [x] 本机环境只读检查
- [x] Active Spec Selection Review
- [x] 写 `spec.md` / `plan.md` / `tasks.md` / `acceptance.md` / `test_cases.md`
- [x] `SPEC_INDEX.md`：902 → ACTIVE
- [x] `docs/feature_index.md` / README §9.7 同步
- [x] 明确不做 008-review-workflow / 901 / 014 / 重开 001–013

Exit: **P1 已于 2026-09-05 由你确认。**

---

## 3. P2 — DB Review

Owner: DB（TL 代行审查，未改 SQL）

- [x] 确认 902 不新增 / 不修改任何表
- [x] 确认脚本与测试不 `CREATE ENGINE` / 不 `session.commit`
- [x] 结论写入 `specs/902-test-dataset/p2_db_review.md`
- [x] **P2-GATE: PASS WITH CONSTRAINTS**（C1–C8）

Forbidden：改 `sql/**`、改 `backend/app/models/**`。

---

## 4. P3 — Implementation Gate

Owner: TL

- [x] P2 已 PASS
- [x] 白名单与 `plan.md` §6 一致
- [x] 写入 `p3_implementation_gate.md`
- [x] 宣布 P4 可开始（Owner 2026-09-05 确认，裁决见 `p4_kickoff.md`）

---

## 5. P4 — Dev Implementation

Owner: DEV。先读本文件、`plan.md`、`p4_kickoff.md`。

**P4-GATE = OPEN**（Owner 2026-09-05 确认）。

允许修改（白名单）：

```text
scripts/create_sample_dataset.sh
scripts/create_sample_dataset.py
backend/tests/fixtures/sample_dataset/**
backend/tests/test_sample_dataset.py
docs/sample_dataset.md
.gitignore
specs/902-test-dataset/tasks.md
```

任务：

- [x] T001 建立 `backend/tests/fixtures/sample_dataset/` 全套 MVP 文件（见 spec §3.1）— TL 核查采纳
- [x] T002 `方案.txt` / `方案副本.txt` 字节级相同；其余互异 — TL 核查采纳
- [x] T003 中文目录 + 中文文件名 — TL 核查采纳
- [x] T004 最小 `hello.pdf` 与 `最小文档.docx`（无网络、无真实公文）— TL 核查采纳
- [x] T005 `manifest.yaml` 与磁盘一致 — TL 核查采纳
- [x] T006 扩展 `scripts/create_sample_dataset.sh`：幂等同步到 `data/sample_dataset/`
- [x] T007 `.gitignore` 追加 `data/sample_dataset/`（第 29 行，仅追加一行）
- [x] T008 `docs/sample_dataset.md`（生成命令、用途、P0 冒烟是操作员可选、无密码）
- [x] T009 `backend/tests/test_sample_dataset.py`
- [x] T010 不修改既有 `fixtures/中文路径` 两文件；不改 `backend/app/**`
- [x] T011 勾选本列表已完成项
- [x] T012 修复 D-01：`minimal_docx_bytes()` 改用固定 `ZipInfo.date_time` / `create_system` / `external_attr`

**T012 修复结果**：

- `最小文档.docx` 连续两次生成字节一致（993 B，sha256 `b4fbe3cf…`）
- 全新目录两次生成，fixture 树 + runtime 树 `diff -r` 全等
- 原地重跑对仓库 fixture 无字节变更
- docx 字节较修复前变化，`manifest.yaml` 对应 sha256 已同步（白名单内）

完成后 **STOP → QA**。禁止自我验收、禁止 merge。

---

## 5.1 DEV 交接备注（供 QA 复核）

- 测试依赖：仅标准库 + pytest，不引 `app/**`，便于 venv 就绪前运行
- 本地验证：`PYTHONPATH=backend python3 -m pytest backend/tests/test_sample_dataset.py -q` → **33 passed**
- 全量回归：`backend/tests` 收集阶段 13 个既有模块报 `ModuleNotFoundError`（`sqlalchemy`、`typer`），
  为 **P0 环境未就绪的存量问题**，与 902 无关；新测试文件收集与执行均正常
- 白名单外零扩散：`git status` 仅含白名单文件 + P1–P3 既有改动

---

## 6. P5 / P6 — QA

- [x] `PYTHONPATH=backend pytest backend/tests/test_sample_dataset.py -q` → **33 passed**
- [x] 全量 `backend/tests` 回归 → **BLOCKED（P0）**：改动前后均为 13 个既有模块 `ModuleNotFoundError`（`sqlalchemy`/`typer`），
      收集错误数一致，非 902 引入；venv 就绪后由 QA 补跑
- [x] 脚本执行两次，目录 mtime/哈希证据（字节 + mtime 双重一致）
- [x] 确认无 MySQL 连接、无 parser 子进程（运行时探针：假 `pymysql`/`sqlalchemy` + PATH 屏蔽 CLI，零触发）
- [x] 写 `p5_qa_report.md` → **P5-GATE: PASS**
- [ ] 写 `p6_e2e_report.md`

---

## 7. P7 / P8

- [x] P6 E2E 报告 `p6_e2e_report.md` — **P6-GATE: PASS**
- [x] Handoff：`docs/handoff-902-test-dataset.md`（HO，P8）
- [x] TL Final Review `p7_final_review.md` — **P7-GATE: PASS**
- [ ] **不**自动 merge `main`；不自动改 902 为 DONE，除非你授权
- [ ] 待授权：commit / merge / SPEC_INDEX 改 DONE / feature_index 与 README 同步 / `.codebuddy/` ignore 处置
