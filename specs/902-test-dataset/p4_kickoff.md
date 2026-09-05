# P4 Kickoff — 902 测试样本集

> 角色：TL
> 日期：2026-09-05
> 分支：`feature/902-test-dataset`
> 前置：P2-GATE PASS WITH CONSTRAINTS（C1–C8）、P3 Implementation Gate PASS
> 裁决：**P4-GATE = OPEN（P4 可开始）**

---

## 1. 裁决

Owner 已于 2026-09-05 确认 P4 开始。P4 由 **DEV** 执行，范围严格限于 `plan.md` §6 白名单。

允许修改：

```text
scripts/create_sample_dataset.sh
scripts/create_sample_dataset.py
backend/tests/fixtures/sample_dataset/**
backend/tests/test_sample_dataset.py
docs/sample_dataset.md
.gitignore                        # 仅追加，不得删改既有行
specs/902-test-dataset/tasks.md   # 仅勾选
```

禁止触碰：`backend/app/**`、`sql/**`、`backend/tests/fixtures/中文路径/**`、`config/*.yaml`、`frontend/**`、001–013 五件套。

禁止：连接 MySQL、调用 MarkItDown / MinerU、访问网络、写 `raw_vault/` `parsed/` `curated/` `reports/`、自我验收、merge。

---

## 2. 状态校正（TL 现场核查）

`tasks.md` 原声明 `实现状态：NOT STARTED`，但工作区已存在部分 P4 产物。经逐项核查，实况如下：

| 任务 | 声明 | 实况 | 判定 |
|---|---|---|---|
| T001 fixture 全套 MVP 文件 | 未做 | 8 个样本 + `manifest.yaml` 已存在 | **已完成** |
| T002 重复对字节相同、其余互异 | 未做 | `方案.txt` / `方案副本.txt` 同为 `f70c9fd7…`；其余 6 个两两互异 | **已完成** |
| T003 中文目录 + 中文文件名 | 未做 | `中文路径/银行项目/` 下 6 个中文名文件 | **已完成** |
| T004 最小 `hello.pdf` / `最小文档.docx` | 未做 | 均存在，纯手工构造，无网络、无真实公文 | **已完成（但见 D-01）** |
| T005 `manifest.yaml` 与磁盘一致 | 未做 | 9 条记录 sha256 与磁盘逐一比对一致 | **已完成** |
| T006 脚本幂等同步 | 未做 | `create_sample_dataset.py` 已建，`.sh` 改为委派 | **已完成（但见 D-01）** |
| T007 `.gitignore` 追加 `data/sample_dataset/` | 未做 | `.gitignore` 无 `sample_dataset` | **未完成** |
| T008 `docs/sample_dataset.md` | 未做 | 文件不存在 | **未完成** |
| T009 `backend/tests/test_sample_dataset.py` | 未做 | 文件不存在 | **未完成** |
| T010 不改既有 fixture / 不改 `backend/app/**` | — | `git status` 仅显示 `scripts/create_sample_dataset.sh` 修改 + 两个新增未跟踪项；`fixtures/中文路径/**` 未动 | **合规** |
| T011 勾选 | 未做 | 全未勾 | **未完成** |

结论：P4 已被**提前部分执行且未登记**。本文件即为补登记。存量产物经审查**合规且予以采纳**，DEV 无需重做 T001–T005，只需修复 D-01 并补完 T007/T008/T009/T011。

---

## 3. 缺陷单

### D-01（阻断级）`最小文档.docx` 生成不确定，违反 A009 幂等

**验收依据**

- `spec.md` §120：脚本重复执行幂等，**目标文件字节不变**
- `acceptance.md` A009：`create_sample_dataset.sh` 连续两次，目标文件哈希不变
- `p2_db_review.md` C5：幂等 = 内容 sha256 相同则跳过

**证据**（TL 于隔离临时目录复现，未污染仓库）

同一脚本相隔 2 秒两次生成，`最小文档.docx` 字节不同：

```text
32cdf7f53dfa88b992bc214640072da7005349b7212c4854ae51379391ce9745   第一次
27932a8c5f232ee27444a2d4f873aeba3b19a35abc3d4ba5d8228f8c397fab5d   第二次
0e50926f1d4cb931f4e0e31501c42ed9af89183c6439a010650abbc709cf86ac   仓库现存 fixture
```

以仓库现有 fixture 为输入原地重跑，`最小文档.docx` 被改写、`manifest.yaml` 的 sha256 行随之变更：

```text
diff -r backend/tests/fixtures/sample_dataset /tmp/p902c/fx
35c35
<     sha256: "0e50926f1d4cb931f4e0e31501c42ed9af89183c6439a010650abbc709cf86ac"
---
>     sha256: "0a422428cc6e822a9421d5f12ee6b78af46c7c6c68a064dfe3263c9361ef53c7"
二进制文件 ... 最小文档.docx 不同
```

其余 7 个样本 + 目录结构幂等，**仅 docx 一处失败**。

**根因**

`scripts/create_sample_dataset.py` `minimal_docx_bytes()` 用 `zf.writestr(name, data)` 写 zip。`zipfile` 在这种调用下为每个成员写入**当前系统时间**作为 `date_time`，导致 zip 头字节随时间变化。`write_bytes_if_changed()` 的「同内容跳过」保护因此对 docx 永久失效。

**修复方向**（DEV 自行落地，TL 不代写代码）

改为显式构造 `zipfile.ZipInfo`，固定 `date_time` 为常量（如 `(1980, 1, 1, 0, 0, 0)`）并固定 `compress_type` 与 `external_attr`，再 `writestr(zipinfo, data)`；三个成员写入顺序固定。修复后须满足：

1. 连续两次生成，8 个样本 sha256 全部不变；
2. 以仓库现存 fixture 原地重跑，`git status` 对 `backend/tests/fixtures/sample_dataset/` 无字节变更；
3. 若修复导致 docx 字节与当前 `0e50926f…` 不同，需同步更新 `manifest.yaml` 该行 —— 这属白名单内，允许。

### D-02（次级）`data/sample_dataset/` 未 gitignore

`.gitignore` 无对应条目，脚本 `--runtime-root` 产物存在误入库风险。由 T007 消除，仅允许**追加一行**。

---

## 4. P4 任务清单（在 `tasks.md` §5 生效）

存量采纳 T001–T005、T006（待 D-01 修复），新增：

- T012 修复 D-01：docx zip 确定化，并使 8 个样本连续两次生成 sha256 全不变

DEV 须完成：T006 修正 + T007 + T008 + T009 + T012 + T011 勾选。

`docs/sample_dataset.md`（T008）须写明：生成命令、样本用途表、重复对说明、**P0 冒烟为操作员可选步骤**、**全文不含任何口令**。

`backend/tests/test_sample_dataset.py`（T009）须覆盖 `test_cases.md` TC001–TC00N，至少含：路径存在、中文名、重复对同 sha、非重复互异、manifest 与磁盘一致、敏感串扫描、幂等、`fixtures/中文路径/**` 未被改动。测试**不得** `create_engine` / `session.commit` / 调 parser。

---

## 5. 已通过的检查（TL，只读）

- 样本树 8 文件 + `manifest.yaml`，与 `spec.md` §3.1 清单一致
- 重复对 `f70c9fd7…` 相同；其余 6 个两两互异 → A004 / A005 满足
- 敏感串扫描（password / secret / token / 身份证 / 银行卡 / 手机号 / 邮箱）无命中
- `manifest.yaml` 9 条 sha256 与磁盘当前字节一致
- `fixtures/中文路径/**`（001–010 封存资产）未被触碰 → C 类约束满足
- `.sh` 仅 6 行委派 `python3`，无 `rm -rf` 广域删除 → C5 后半条满足
- 脚本含 `resolve_under_root()` 路径逃逸防护，拒绝绝对路径与 `..`
- 未连接 MySQL、未引入 parser、未访问网络

---

## 6. Exit

DEV 完成 T006/T007/T008/T009/T011/T012 后 **STOP**，交 QA（P5）。

TL 不代跑 pytest，不代签验收。QA PASS 前任何文档不得出现「可 merge」。
