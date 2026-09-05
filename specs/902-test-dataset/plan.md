# Plan: 902 测试样本集

> Spec: `specs/902-test-dataset/`  
> Phase: `P1 Tech Lead Plan`  
> Status: `ACTIVE SPEC / NOT IMPLEMENTED`  
> 实现：P2/P3 已 PASS；P4 仍待你确认后才开始

---

## 1. 架构

902 是 **fixture + 脚本**，不是业务服务。

```text
scripts/create_sample_dataset.sh
        |
        +-- 读取或生成 backend/tests/fixtures/sample_dataset/**
        |
        v
data/sample_dataset/     (运行时副本，不入库)
        |
        v
backend/tests/test_sample_dataset.py
```

不引入 `backend/app/services/**`。不改 CLI。不改 ORM。不改 SQL。

---

## 2. 核心流程

```text
1. 确保 fixture 树存在且符合 spec §3.1 文件清单。
2. 生成或校验最小 PDF、最小 docx（无网络）。
3. 写入 / 校验 manifest.yaml。
4. 将树同步到 data/sample_dataset/（mkdir + 按文件复制；已存在且哈希相同则跳过）。
5. pytest：路径、中文、重复 sha256、manifest、敏感串、幂等、禁止改既有 fixture。
```

---

## 3. 数据与存储边界

| 路径 | 902 |
|---|---|
| `backend/tests/fixtures/sample_dataset/` | 写（提交） |
| `data/sample_dataset/` | 写（运行时，gitignore 友好） |
| `backend/tests/fixtures/中文路径/**` | **只读**（禁止改） |
| `backend/tests/fixtures/parse_quality_report_*.json` | **只读**（手册可引用） |
| MySQL 任意表 | **不访问** |
| `raw_vault` / `parsed` / `curated` / `reports` / `quarantine` | **不写** |
| 用户真实源目录 | **不读不写** |

`.gitignore` 已忽略 `data/personal-kb/`，未忽略 `data/sample_dataset/`。P4 二选一即可，写入 plan 锁定：

```text
锁定：data/sample_dataset/ 加入 .gitignore，避免生成物入库。
脚本只写该目录 + fixture 目录（fixture 本身应提交）。
```

Dev **可以**改 `.gitignore` 增加一行 `data/sample_dataset/`（见白名单）。

---

## 4. 幂等与异常

| 场景 | 处理 |
|---|---|
| 首次运行 | 创建目录与文件 |
| 重复运行 | 哈希相同则跳过；不同则仅覆盖 902 目标路径 |
| fixture 缺失 | 非零退出，错误信息写 stderr |
| 单文件写失败 | 记录错误；已成功文件保留；重跑可恢复 |
| 目标路径逃逸（`..`） | 拒绝；测试覆盖 |

不得 `rm -rf` 仓库根或运行时 vault。清理范围仅限 `data/sample_dataset/` 内由 manifest 列出的相对路径。

---

## 5. 最小 PDF / docx 约定

- **PDF**：手写或脚本写出的最小 `%PDF-1.x` 对象，可用常见 PDF 阅读器识别为 PDF 即可；一页、无嵌入字体子集要求；文本可为 `hello` / `示例`。
- **docx**：最小 OOXML zip（`[Content_Types].xml` + `word/document.xml`），正文虚构中文一句。不调用 MS Office，不下载模板。

两者体积应很小（建议各 < 50KB）。禁止提交真实扫描件或带元数据的办公原稿。

---

## 6. Dev 文件白名单（P4 唯一允许）

```text
scripts/create_sample_dataset.sh
scripts/create_sample_dataset.py          # 可选；仅当 bash 不足以写 PDF/docx
backend/tests/fixtures/sample_dataset/**
backend/tests/test_sample_dataset.py
docs/sample_dataset.md
.gitignore                                # 仅允许追加 data/sample_dataset/
specs/902-test-dataset/tasks.md           # 仅勾选
```

## 7. 禁止修改清单

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
config/app.example.yaml                   # 902 不改默认账号示例以外的事；本 Spec 不改 config
config/*.yaml
frontend/**
raw_vault / parsed / curated / reports / quarantine / source_registry
用户原始文件
001–013 已完成 Spec 五件套（除 SPEC_INDEX 已由 TL 改的 ACTIVE 标记）
specs/008-review-workflow/**
specs/901-docker-compose-deployment/**
```

---

## 8. 数据库

**无。** 902 零 DML、零新表、零 ORM。

P2 DB Review 预期：`PASS WITH CONSTRAINTS`（C1：运行时不连 MySQL；C2：不改 init SQL / 006 migration）。

---

## 9. 测试概要

见 `test_cases.md`。P4 至少落地：

- fixture 清单齐全
- 中文路径
- 重复对 sha256 相等
- 非重复文件 sha256 不等
- manifest 与磁盘一致
- 脚本跑两次字节不变
- 不修改既有 `fixtures/中文路径` 两文件
- 内容扫描：无 `password` / `BEGIN PRIVATE KEY` / 内网账号形态等简单敏感标记（允许 `change_me` 仅出现在非 902 文件）

---

## 10. 明确不做

- 不实现 review / embedding / Streamlit / search / parser
- 不装 `magic-pdf`、不建 venv、不写 `config/app.yaml`（操作员 P0，见环境审查）
- 不把样本当成真实 `source_registry` 自动 scan
- 不修复 007 E2E、不填充 013 质量页真实报告（手册可说明如何**复制**已有 008 fixture 到 `reports_root`，由操作员执行）

---

## 11. 建议分支

```text
feature/902-test-dataset
```

从当前 `main` 拉出。P4 不直接在 `main` 实现。
