# TL Review: 本机环境就绪 + Active Spec Selection

> 角色：Tech Lead（`TL`）  
> 日期：2026-09-05  
> 仓库：`/home/szf/dev/doc2md`（`main` @ `9fa6777`）  
> 权威索引：`specs/SPEC_INDEX.md`  
> 本文件只做审查与选型。**不写 `backend/**`，不建 venv，不写 `config/app.yaml`，不改 MySQL。**

---

## 0. 会话声明

| 项 | 值 |
|---|---|
| 角色 | Tech Lead（`TL`） |
| 选定 ACTIVE Spec | `specs/902-test-dataset/` |
| 分支 | `main`（P4 前应开 `feature/902-test-dataset`） |
| 流水线步骤 | ① P1 Plan 已产出；**STOP，待你确认后才进 P2** |
| Dev 白名单 | 见 `specs/902-test-dataset/plan.md` §6 |
| 黑名单 | 全部 `backend/app/**`、`sql/**`、原始用户文件、`raw_vault` / `parsed` / `curated` 真实产物 |

---

## 1. 本机环境就绪检查（只读）

检查范围：本仓库工作区 + 本机 PATH / 端口 / 包。未创建文件，未安装依赖，未登录数据库（仅用 example 凭据探测，失败即停）。

### 1.1 结论

| 级别 | 结论 |
|---|---|
| **总体** | **未就绪**。001–013 代码已在 `main`，但本机还不能复现 CLI / pytest / Streamlit。 |
| **阻塞** | 无项目 venv；无 `config/app.yaml`；无法验证 `personal_kb`；conda 污染 PATH。 |
| **非阻塞** | `magic-pdf` 未装（007 已知缺口）；013 无真实质量报告文件。 |

### 1.2 检查表

| 项 | 结果 | 含义 |
|---|---|---|
| OS | Ubuntu 22.04，Linux 6.8，磁盘剩余约 235G | 主机可用 |
| 仓库 | `main` 干净，单 commit `9fa6777` 导入 001–013 | 无旧 `pkb_sdd` 多分支历史 |
| 旧路径 `/home/szf/dev/pyws/pkb_sdd` | **不存在** | 旧 handoff 路径失效 |
| Python | Anaconda `3.11.7`；系统另有 `3.10` | 满足 3.11+，但必须用**项目 venv** |
| `backend/.venv` / 根 `.venv` | **不存在** | 依赖未隔离 |
| `markitdown`（当前解释器） | **缺失** | 005 不可跑 |
| Anaconda `typer` / `click` | `typer 0.25.1` + `click 8.4.1` | **违反** 013 钉扎（`typer==0.15.1`，`click>=8.1,<8.2`） |
| Anaconda `streamlit` | 1.30.0；`streamlit` CLI 报 `No module named 'streamlit.cli'` | 不可用系统/conda Streamlit 启 013 |
| `config/app.yaml` | **不存在**（仅有 `app.example.yaml`） | 正确未入库；本机未配置 |
| 运行时目录 `raw_vault` / `parsed` / `curated` / `reports` / `source_registry` / `quarantine` | **不存在** | 新工作区，无历史产物 |
| `data/` | 仅 `.gitkeep` | 可作 902 生成根 |
| MySQL 服务 | `active`；`0.0.0.0:3306` 在听 | 主机已有库进程 |
| 系统包 | `mysql-server` / `mysql-client` **8.0.41** | 与项目 MySQL 8.0+ 对齐 |
| PATH 上的 `mysql` | **Anaconda** `/home/szf/anaconda3/bin/mysql`（mysqld 显示 5.7.24） | 会盖住 `/usr/bin/mysql`。初始化请用 **`/usr/bin/mysql`** |
| example 凭据 / 空 root / 当前用户 socket | 均 **连接失败** | **不能确认** `personal_kb` 是否存在、006 表是否已建 |
| `~/.my.cnf` | 不存在 | 无本机默认账号 |
| Docker | 20.10.18；compose 可用；容器均为 Hadoop / registry | **不要**在这台上新开 3306 容器，避免和本机 MySQL 抢端口 |
| `docker-compose.yml`（本仓库） | **无** | 901 尚未实现 |
| `magic-pdf` | **不在 PATH** | 007 真实 E2E 仍不可做 |
| 真实 Office / PDF 样本 | 仓库内 **无** | 仅有两个 19 字节中文 txt fixture |
| `scripts/create_sample_dataset.sh` | 只写那两个 txt | 不足以支撑 004/005/007/013 冒烟 |
| `scripts/init_db.sh` | 调用 PATH 上的 `mysql` | 在本机当前 PATH 下会走到 conda 客户端 |
| `scripts/run_pipeline.sh` | 调用仍是 placeholder 的 `build-parse-queue` / `parse` | 不能当流水线 |

### 1.3 操作员 P0（**不是 902 实现任务**，本审查不执行）

要在本机跑 001–013，需要你本人或后续运维会话完成：

1. `python3.11 -m venv backend/.venv`，再 `pip install -r backend/requirements.txt`（不要用 Anaconda 基环境）。
2. 复制 `config/app.example.yaml` → `config/app.yaml`，填入真实 MySQL 账号；**不要提交**。
3. 用 `/usr/bin/mysql`（不要用 conda `mysql`）执行 `sql/001_init_schema_v1_1.sql` 与 `sql/migrations/006_parse_registry_v1.sql`。
4. 确认库 `personal_kb`、关键表（含 `kb_parse_run` / `kb_evidence` / `kb_project`）存在，且 `ngram_token_size=2`（012 合同）。
5. 902 落地后，再用样本目录做只读冒烟（scan / search / Streamlit），而不是拿真实私有文档。

P0 未完成 **不阻断** 902 P1/P2/P4：902 MVP **不连 MySQL、不跑 parser**。P0 阻断的是「本机验收 001–013 / 007 真 MinerU / 013 质量页真报告」。

---

## 2. Active Spec Selection Review

### 2.1 候选与否决

| 候选 | 裁决 | 理由 |
|---|---|---|
| **`902-test-dataset`** | **选定 ACTIVE** | SDD 第 6 条：解析必须有样本验证。本仓库是新导入环境，只有两个 txt，007/013 已知缺口都是「没有可公开样本」。902 无 schema、不碰已完成 service，能先把 001–013 变成可复验。 |
| `008-review-workflow` | **保持 FUTURE STUB** | 下一产品能力，但五件套仍是模板；且依赖可跑环境 + 可复核样本。现在开工会发明一整套 review 产品设计。表 `kb_review_item` / `kb_manual_correction` 已在 init SQL，等 902 完成后再做 Selection。 |
| `901-docker-compose-deployment` | **保持 SUPPORT / FUTURE** | 本机已有 MySQL 8 听 3306；Docker 已被 Hadoop 占用；仓库无 compose。现在做 901 有端口冲突和运维风险，收益低于样本集。 |
| 重开 001–013 / 007 真 MinerU | **拒绝** | 已完成 Spec 不得重开，除非另立 defect Spec。MinerU E2E 仍受 `magic-pdf` 与样本路径限制，不是本轮范围。 |
| 新建 `014-*` | **拒绝** | 环境就绪是操作员 P0，不是新功能 Spec。未列入索引的编号不得擅自开工。 |
| 只改协作规范 / README 漂移 | **本轮不做** | 有价值，但是文档债，不单独开 ACTIVE Spec。902 完成后如需再授权 docs-only 任务。 |

### 2.2 选定合同

```text
ACTIVE = specs/902-test-dataset/
目标   = 不含敏感资料的小样本集 + 幂等生成脚本 + pytest
不做   = review UI、Docker、parser 调用、MySQL 写入、真实私有文档
```

### 2.3 902 之后（预告，未激活）

```text
902 DONE
  → 操作员完成 P0（venv / app.yaml / schema）
  → 用样本对 001–013 做只读冒烟（非重开 Spec）
  → 再做下一次 Active Spec Selection
      首选评估：008-review-workflow
      备选：901-docker-compose-deployment
```

---

## 3. P1 产出

| 文件 | 动作 |
|---|---|
| `specs/902-test-dataset/spec.md` | 从模板改为可实施合同 |
| `specs/902-test-dataset/plan.md` | 流程、白名单、幂等、不做项 |
| `specs/902-test-dataset/tasks.md` | P1–P8 任务拆分 |
| `specs/902-test-dataset/acceptance.md` | A001–A016 |
| `specs/902-test-dataset/test_cases.md` | TC 对照表 |
| `specs/SPEC_INDEX.md` | 902 → **ACTIVE** |
| `docs/feature_index.md` | 902 → ACTIVE |
| `README.md` §9.7 | 指向 902 合同 |

---

## 4. 门禁

- [x] 环境只读检查完成（§1）
- [x] Selection Review 完成（§2）
- [x] 902 五件套已写
- [x] SPEC_INDEX 已标 902 ACTIVE
- [ ] **你确认 P1** 后才进入 P2 DB Review
- [ ] P2 PASS 后才进 P3 Implementation Gate
- [ ] P3 白名单确认后才允许 Dev 改文件

**STOP。** TL 不写业务代码。未获你确认前，Dev / DB / QA 不要开工。
