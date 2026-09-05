# 样本数据集（Spec 902）

`backend/tests/fixtures/sample_dataset/` 是一套**可提交、可幂等再生、非敏感**的小样本，用于 parser 与只读冒烟验证。

> Spec：`specs/902-test-dataset/`（ACTIVE）
> 生成脚本：`scripts/create_sample_dataset.sh` → 委派 `scripts/create_sample_dataset.py`
> 测试：`backend/tests/test_sample_dataset.py`

---

## 1. 生成命令

```bash
# 生成 fixture（幂等），默认同步到 data/sample_dataset/
bash scripts/create_sample_dataset.sh

# 只生成 fixture，不同步运行时副本
bash scripts/create_sample_dataset.sh --skip-sync

# 指定输出位置（调试用）
python3 scripts/create_sample_dataset.py \
  --fixture-root /tmp/fx --runtime-root /tmp/rt
```

脚本行为：

- 目标文件已存在且字节相同 → 跳过（不改写 mtime）
- 只写 fixture 树与 `data/sample_dataset/` 两处
- 拒绝绝对路径与 `..` 逃逸（抛出 `PathEscapeError`）
- 拒绝写入 `backend/tests/fixtures/中文路径/`（001–010 封存资产，退出码 2）

**幂等保证**：连续两次运行，8 个样本字节完全不变。此前 `最小文档.docx` 因 zip 成员写入系统时间而不稳定，已改为固定 `ZipInfo.date_time`（见 `specs/902-test-dataset/p4_kickoff.md` D-01）。

---

## 2. 样本清单

| 相对路径 | 用途 | 预期路由 |
|---|---|---|
| `中文路径/银行项目/方案.txt` | 中文路径；精确重复对 A | `text` |
| `中文路径/银行项目/方案副本.txt` | 与上一文件**字节级相同** | `text` |
| `中文路径/银行项目/会议纪要.md` | Markdown 样本 | `markitdown` |
| `中文路径/银行项目/指标口径.csv` | CSV 样本 | `text` |
| `中文路径/银行项目/说明.html` | HTML 样本 | `markitdown` |
| `中文路径/银行项目/元数据.json` | JSON 样本 | `markitdown` |
| `office_like/最小文档.docx` | 最小 OOXML，供 office 路由 | `markitdown` |
| `pdf/hello.pdf` | 最小 PDF，供 pdf 路由（仅文件，不调 `magic-pdf`） | `mineru` |

`manifest.yaml` 记录每个文件的 `rel_path` / `sha256` / `purpose` / `duplicate_group` / `intended_route`，由脚本生成，与磁盘字节保持一致。

**精确重复对**：`方案.txt` 与 `方案副本.txt` sha256 相同（`f70c9fd7…`），用于 003 重复治理验证。其余文件两两互异。

---

## 3. 禁止事项

- 全部内容为**虚构**，不含真实公文、客户资料或业务数据
- 不含任何密码、私钥、token、证件号、银行卡号；文档与样本中均无连接口令
- 脚本与测试**不连接 MySQL**、不 `create_engine`、不 `session.commit`
- 不调用 MarkItDown / MinerU / `magic-pdf`
- 不修改 `backend/tests/fixtures/中文路径/**`
- 不写入 `raw_vault/` `parsed/` `curated/` `reports/`
- 不自动 merge 到 `main`

---

## 4. 校验

```bash
PYTHONPATH=backend python3 -m pytest backend/tests/test_sample_dataset.py -q
```

覆盖：清单完整性、中文路径与中文内容、重复对同 sha、非重复互异、PDF 头、docx zip 结构、manifest 与磁盘一致、敏感串扫描、封存 fixture 未改、脚本幂等、零数据库、零 parser、路径逃逸防护。

---

## 5. 后续（关联，非当前范围）

- **P0 环境待办**（操作员任务）：创建 `backend/.venv`、按 `config/app.example.yaml` 生成 `config/app.yaml`。
  若后续把 conda 的 `bin` 前置到 `PATH`，`mysql` 会命中 conda 路径而非系统客户端，届时须显式使用 `/usr/bin/mysql`。
- 后续 spec 可用本样本集做 parser 只读冒烟；真实 GPU/模型能力需另立项验证。
