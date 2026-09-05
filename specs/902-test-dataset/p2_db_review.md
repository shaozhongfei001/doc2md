# 902 Test Dataset — P2 DB & Data Review

> Role: DB & Data Agent（本会话由 TL 代行审查，不改业务代码、不改 SQL）  
> Spec: `specs/902-test-dataset/`  
> Stage: P2 DB & Data Review  
> Base: P1 五件套 + `docs/tl-review-2026-09-05-env-and-active-spec.md`  
> User: P1 **已确认**（2026-09-05）

---

## 1. Gate Conclusion

**P2-GATE: PASS WITH CONSTRAINTS**

902 可以进入 **P3 Implementation Gate**。

902 没有数据面：不新建表、不改 init SQL / 006 migration、不新增 ORM、运行时不连 MySQL。  
本审查把「数据」定义为：**样本文件字节 + `manifest.yaml` 中的 sha256 / 重复对**。

---

## 2. Mandatory Questionnaire

| # | Question | Answer |
|---|----------|--------|
| 1 | 需要新表吗？ | **NO** |
| 2 | 需要改 `sql/001_init_schema_v1_1.sql` 吗？ | **NO** |
| 3 | 需要 `sql/migrations/**` 吗？ | **NO** |
| 4 | 需要新 ORM / 改现有 models 吗？ | **NO** |
| 5 | P4 可以写任何 MySQL 表吗？ | **NO** — 零 DML，零 SELECT |
| 6 | P4 可以 `create_engine` / `session_scope` / `pymysql.connect` 吗？ | **NO** |
| 7 | 幂等键是什么？ | 文件内容 sha256；目标路径相对 manifest；哈希相同则跳过 |
| 8 | 会发明未文档化 DB 字段吗？ | **NO** — `manifest.yaml` 不是表 |
| 9 | 会污染 001–013 测试 fixture 吗？ | **有风险（现有脚本）** — 见 C3 |
| 10 | 会写 `raw_vault` / `parsed` / `curated` / `reports` 吗？ | **NO** |
| 11 | schema 偏差？ | **无** — 无 schema 变更 |
| 12 | 本机 `personal_kb` 未验证是否阻断 P2？ | **NO** — 902 不连库 |

---

## 3. Schema / ORM

对照 `sql/001_init_schema_v1_1.sql` 与 `sql/migrations/006_parse_registry_v1.sql`：

| 对象 | 902 关系 |
|---|---|
| `kb_file_instance` / `kb_file_content` | 不读不写。样本以后可供操作员 `scan`，那是 P0 冒烟，不是 902 实现 |
| `kb_duplicate_group` | 不写。重复对只体现在两个同 sha256 文件 + manifest |
| `kb_parse_*` / `kb_document*` / `kb_evidence` / `kb_project*` | 不访问 |
| `kb_review_item` / `kb_manual_correction` / `kb_embedding_ref` | 不访问（008-review-workflow 未激活） |
| `backend/app/models/**` | 禁止修改 |
| `backend/app/core/database.py` | 禁止 import |

**Migration: NOT required. NOT authorized.**

---

## 4. 幂等（文件系统）

| 场景 | 要求 |
|---|---|
| 首次 `create_sample_dataset.sh` | 写出 `data/sample_dataset/` 且与 fixture 哈希一致 |
| 第二次 | 哈希相同 → skip；不新增「第二份」主文件 |
| fixture 已提交 | 重跑不得改已提交字节（除非 Dev 在同一次 P4 内故意改正样本，且测试更新） |
| 失败重跑 | 只覆盖 902 允许的目标相对路径 |

不得用 SQL upsert。不得为「清理」DELETE MySQL 行。不得 `rm -rf` 仓库根、`raw_vault`、`parsed`、既有 `fixtures/中文路径`。

---

## 5. 数据污染

| 风险 | 裁决 |
|---|---|
| **现有** `scripts/create_sample_dataset.sh` 向 `backend/tests/fixtures/中文路径/银行项目/` **写入** | **C3 锁定**：P4 必须停止向该路径写入。001/002/003/010 依赖这两份 txt |
| 脚本把样本写进 `raw_vault` / `parsed` / 用户源目录 | 禁止 |
| pytest 连真实 `personal_kb` | 禁止。测试只用本地路径 |
| `data/sample_dataset/` 被 git 提交 | 必须 `.gitignore`（plan 已锁） |
| manifest 含 `../` 逃逸 | 拒绝复制；TC014 |
| 样本含真实机密 | A007；不得入库 |

---

## 6. Locked Constraints（P3/P4 继承）

| ID | Constraint |
|---|---|
| C1 | 运行时与测试 **不连 MySQL**（不 create_engine、不 session、不 pymysql） |
| C2 | **不改** `sql/001_init_schema_v1_1.sql`、`sql/migrations/**`、任何 ORM |
| C3 | **禁止**再写 `backend/tests/fixtures/中文路径/**`。现有脚本的 mkdir/echo/cp 必须迁走 |
| C4 | 只写 `backend/tests/fixtures/sample_dataset/`（提交）与 `data/sample_dataset/`（运行时） |
| C5 | 幂等 = 内容 sha256 相同则跳过；不删除样本树以外文件 |
| C6 | `manifest.yaml` 的 `sha256` / `duplicate_group` 是文件元数据，**不是** `kb_*` 列 |
| C7 | 不调用 001–013 CLI，不写 vault/parsed/curated/reports |
| C8 | 不发明 DB 字段；不把 review/embedding 表当 902 输出 |

若 P4 发现「必须入库 / 必须 scan / 必须改 schema」→ **STOP**，交还 TL + DB，不得自行扩大。

---

## 7. 交还 Dev 的约束清单（不代写代码）

1. 重写 `scripts/create_sample_dataset.sh`，源与目标不得再指向 `fixtures/中文路径`。
2. 测试断言既有 `方案.txt` / `方案副本.txt`（旧路径）字节不被脚本改动。
3. 测试禁止 import `app.core.database` / `app.models`。
4. `.gitignore` 增加 `data/sample_dataset/`。

**无需 Dev 改 SQL。**

---

## 8. 结论

| 项 | 结果 |
|---|---|
| 幂等 | 无 DB 问题；文件系统规则已锁 |
| 数据污染 | **现有脚本有污染风险**，C3 强制消除 |
| schema 偏差 | 无 |
| 原始文件解耦 | 通过（不读用户源目录） |

**通过 → STOP，交 P3 TL Implementation Gate。**
