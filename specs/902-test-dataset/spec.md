# Spec: 902 测试样本集

> Project: `doc2md` / 个人历史项目文档知识库 V1.1-SDD  
> Spec Directory: `specs/902-test-dataset/`  
> Phase: `P1 Tech Lead Plan`  
> Status: `ACTIVE SPEC / NOT IMPLEMENTED`  
> Authority: `specs/SPEC_INDEX.md`  
> Selection: `docs/tl-review-2026-09-05-env-and-active-spec.md`

---

## 1. 背景

`001–013` 已在 `main` 完成。本仓库是新导入工作区：没有项目 venv、没有 `config/app.yaml`、没有运行时目录，也没有可公开的 Office / PDF 样本。现有 fixture 只有：

```text
backend/tests/fixtures/中文路径/银行项目/方案.txt
backend/tests/fixtures/中文路径/银行项目/方案副本.txt
```

`scripts/create_sample_dataset.sh` 只生成上述两个 txt。007 真实 MinerU E2E 与 013 Quality Reports 真渲染，都因缺少安全样本而无法在本机复验。

SDD 第 6 条：文档解析必须有样本验证。902 先提供**不含敏感资料**的小样本集，而不是重开已完成 parser / 管理台 Spec。

---

## 2. 目标

构造一套可提交、可幂等再生、可供后续只读冒烟使用的小样本：

```text
中文路径 + 精确 sha256 重复
  + MarkItDown 路由扩展名
  + MinerU PDF 路由用的最小 PDF
  + 清单 manifest
  + 幂等生成脚本
  + pytest
```

样本用于**以后**对 001–013 做操作员冒烟。902 **本身不调用** scan / vault / parser / quality / search / Streamlit。

---

## 3. 范围

### 3.1 包含

- 提交用样本树：`backend/tests/fixtures/sample_dataset/`
- 可选运行时副本：`data/sample_dataset/`（gitignore 已覆盖 `data/personal-kb/`；902 只写 `data/sample_dataset/`）
- `manifest.yaml`：每个文件的用途、扩展名、是否重复对、目标路由
- 幂等脚本：扩展 `scripts/create_sample_dataset.sh`（必要时增加同目录 Python 生成器，不进 `backend/app/**`）
- pytest：`backend/tests/test_sample_dataset.py`
- 操作说明：`docs/sample_dataset.md`（如何生成、如何把 `source_registry_root` 指过去；不写密码）

MVP 文件集合（最小）：

| 相对路径 | 作用 |
|---|---|
| `中文路径/银行项目/方案.txt` | 中文路径；重复对 A |
| `中文路径/银行项目/方案副本.txt` | 与上一文件 **字节级相同**（精确 sha256 重复） |
| `中文路径/银行项目/会议纪要.md` | MarkItDown / 文本类 |
| `中文路径/银行项目/指标口径.csv` | CSV |
| `中文路径/银行项目/说明.html` | HTML |
| `中文路径/银行项目/元数据.json` | JSON |
| `office_like/最小文档.docx` | 004 路由 `office_to_markitdown`；最小 OOXML，无真实业务内容 |
| `pdf/hello.pdf` | 004 路由 `pdf_to_mineru`；最小合法 PDF，**不要求**本 Spec 调用 `magic-pdf` |

已有 `backend/tests/fixtures/中文路径/**` 与 `parse_quality_report_*.json` **保持不动**（001/002/003/010 测试依赖）。902 新树不得覆盖这些路径。

已有 008 JSON fixture 可在 `docs/sample_dataset.md` 中**引用**，供 013 质量页操作员自测；902 不复制、不改这些 JSON。

### 3.2 不包含

| 不做 | 原因 |
|---|---|
| 真实历史项目 / 投标 / 合同原文 | 敏感资料 |
| 源代码仓库样本 | 全局禁止 |
| 调用 MarkItDown / MinerU / `magic-pdf` | 属 005/007；902 只供文件 |
| 调用任何已有 CLI（`scan` 等） | 属操作员 P0 之后的冒烟，不是 902 实现 |
| 写 MySQL / 改 schema / 新 ORM | 无数据面 |
| 写 `raw_vault` / `parsed` / `curated` / `reports` / `quarantine` | 运行时产物，非样本 |
| 改 `backend/app/**` | 不新增业务服务或 CLI |
| Docker Compose（901） | 未激活 |
| Review workflow（008-review-workflow） | 未激活 |
| 自动删除 / 移动 / 重命名原始用户文件 | 全局禁止 |
| 上传外部云 | 全局禁止 |
| 图像扫描件作为 MVP 必选 | 007 未含 IMAGE 解析 |

---

## 4. 输入 / 输出

### 4.1 输入

- 仓库内已提交的 fixture 树（脚本以 fixture 为源，或由生成器写出与 fixture 相同的字节）
- 无 MySQL、无 `config/app.yaml` 依赖

### 4.2 输出

```text
backend/tests/fixtures/sample_dataset/
  manifest.yaml
  中文路径/银行项目/{方案.txt,方案副本.txt,会议纪要.md,指标口径.csv,说明.html,元数据.json}
  office_like/最小文档.docx
  pdf/hello.pdf

data/sample_dataset/     # 脚本生成的运行时副本，内容与 fixture 对齐
docs/sample_dataset.md
```

`manifest.yaml` 至少包含：`schema_version`、每个文件的 `rel_path`、`sha256`（生成后写入或由测试计算核对）、`purpose`、`duplicate_group`（若有）、`intended_route`。

---

## 5. 业务规则

1. 全部样本为虚构、公开安全内容（中文示例句即可），不得含账号、密码、密钥、真实公司机密。
2. `方案.txt` 与 `方案副本.txt` 必须 sha256 相同；其余文件必须 sha256 不同。
3. 必须包含至少一层中文目录名与中文文件名。
4. 脚本重复执行幂等：目标文件字节不变；不删除样本树以外的文件。
5. 脚本失败时已写出的目标文件可重跑覆盖**仅限** 902 目标路径。
6. 不得修改 `backend/tests/fixtures/中文路径/**` 既有两文件（除非测试证明未被 902 脚本改动）。
7. 最小 PDF / docx 由脚本或提交字节生成，不从网上下载。
8. 902 不读取用户真实源目录。

---

## 6. CLI / 脚本合同

```bash
# 将 fixture 同步到 data/sample_dataset/（幂等）
bash scripts/create_sample_dataset.sh
```

- 退出码 0：目标树与 fixture 一致（或已生成一致树）
- 不得连接网络
- 不得连接 MySQL
- `--help` 或脚本头注释须说明输出路径

不新增 `python -m app.cli.main ...` 子命令。

---

## 7. 验收摘要

详见 `acceptance.md`。核心：

- 样本树完整、中文路径、精确重复对
- 无敏感串
- 脚本幂等
- 不触碰原始用户文件与 001–013 业务代码
- pytest 通过

---

## 8. 与后续阶段的关系

902 **不**把 001–013 标为未完成。操作员在 P0 环境就绪后，可自愿用 `data/sample_dataset/` 做 scan 等冒烟；那是操作手册内容，不是本 Spec 的实现任务，也不是对 001–013 的重开。
