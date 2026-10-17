# 00 · 先读我（外部智能体重构交接包）

> **项目**：`doc2md` — 个人知识库（PDF/Office 文档 → 结构化文本 → 证据链 → 检索/问答底座）
> **交接日期**：2026-10-17  ·  **代码基线**：`main@52a63ec`（已推送 GitHub）
> **规模**：后端 `backend/app` 46 文件 / 15,754 行；测试 20 文件 / 587 用例 / 16,161 行；前端 Streamlit 2 文件 / 1,207 行；SQL 22 表 / 527 行；规格 161 文件 / 37,449 行
> **当前测试**：`613 passed / 4 failed（既有环境基线）/ 2 skipped`

---

## 0. 这份包是干什么的

你要重构/重设计这个项目。**不要先读全部源码**——`backend/app` 有 1.5 万行、`specs` 有 3.7 万行，逐文件读会浪费大量上下文。本包已把
「架构 + 契约 + 决策理由 + 文件职责 + 符号索引 + 测试基线 + 技术债 + 重构禁区」压缩成 9 份文档，读完即可开工。

**建议阅读顺序（约 30–40 分钟）**：

| 顺序 | 文件 | 读完你会得到 |
|---|---|---|
| 1 | `01-architecture.md` | 系统分几层、两条主数据流、存储与 DB 布局 |
| 2 | `02-design-decisions.md` | 18 条关键决策**及其理由**（含"为什么不能那样改"） |
| 3 | `05-data-contracts.md` | 三件套 / Document IR / DB / 配置的字段级契约 |
| 4 | `03-file-map.md` | 每个文件干什么、大小、对外 API（按需跳读） |
| 5 | `06-test-baseline.md` | 怎么跑、基线数字、4 个已知失败、变异方法论 |
| 6 | `07-debts-and-risks.md` | 已知缺口与真实风险（别把它们当 bug 重复修） |
| 7 | `08-refactor-guide.md` | 重构红线、安全区、验证门、建议顺序 |
| 8 | `04-code-index.md` | 全量类/函数签名索引（**查符号时用，不要通读**） |
| 9 | `09-specs-index.md` | 001~017 规格状态与验收 ID 一览 |

---

## 1. 项目一句话定位

把散落在磁盘上的文档（PDF / Word / Excel / Markdown…）**变成机器可用的结构化知识**：

```
扫描 → 内容去重 → 内容寻址仓库(raw_vault) → 路由(嗅探PDF文本层) → 解析(文本/表格/图片)
     → 质量检查(含语义指标) → 证据链(chunk + evidence) → 检索(MySQL FULLTEXT) → 管理界面
```

所有中间产物**落盘可审计**（`parsed/by_hash/<sha>/`），所有步骤**幂等 + 只读原件**，DB 只存登记与索引，不存正文。

---

## 2. 十条"如果只能记住十件事"

1. **原件只读**：`raw_vault` 与源目录任何写操作都被 `ensure_readonly()` 拦；正文一律落在 `parsed/`。
2. **内容寻址**：一切以 `sha256` 为身份（`content_uid == sha256`），路径由 `by_hash/ab/cd/<sha>/` 派生。
3. **三件套契约**（005/007 起冻结）：`parsed_text.md` + `parsed_metadata.json` + `parse_manifest.json`，只允许 additive 扩键。
4. **Document IR 是第四件产物**（017）：`document_ir.json`，块级 bbox/reading_order/content_ref + 表格 cells 矩阵 + 图像 asset 对齐。
5. **fail-closed 优先于"能跑"**：校验不过**绝不落盘半成品**（半成品比没有更危险，会被下游当有效数据）。
6. **缺数据一律 `null` + 计数，禁止估算**（bbox / stats / caption / 置信度）——这是全项目反复被强调的红线。
7. **幂等 + dry-run 是一等公民**：同 sha 重跑不重复写、`--force` 才重建、`--dry-run` 零写入。
8. **零 schema 迁移**（001~017）：产物类型走既有 VARCHAR 列，`sql/migrations` 不得新增（要迁移必须 Owner 批准）。
9. **反假绿文化**：任何"必须不出现 X"的断言，都要用变异证明它真的能失败（否则是恒真假断言）。
10. **规格即验收**：`specs/<id>/acceptance.md` 的 A0xx-01~12 是硬判据，不是文档装饰。

---

## 3. 你能直接跑起来的命令

```bash
cd backend
PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q            # 全量
PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q tests/test_document_ir.py
PYTHONPATH=. /home/szf/anaconda3/bin/python -m app.cli.main --help   # CLI 入口
```

> **解释器注意**：必须用 `/home/szf/anaconda3/bin/python`（3.11.7）。系统 `python3` 是 3.10，`Typer/Click` 版本不匹配。
> **DB 测试**：需要 MySQL；不注入 `PKB_MYSQL_*` 会大面积 skip（历史教训：123 个用例曾因硬编码库名而"从未真正执行"）。

---

## 4. 包内清单

```
00-READ-ME-FIRST.md           ← 本文件
01-architecture.md            分层 / 数据流 / 存储 / DB / CLI
02-design-decisions.md        18 条决策与理由（含禁止事项）
03-file-map.md                46 个后端文件 × 职责 × LOC × 对外 API
04-code-index.md              AST 生成的类/函数签名全量索引
05-data-contracts.md          三件套 / IR / DB / 配置 字段级契约
06-test-baseline.md           测试布局、基线、已知失败、变异方法论
07-debts-and-risks.md         技术债与真实风险
08-refactor-guide.md          红线 / 安全区 / 验证门 / 重构顺序建议
09-specs-index.md             001~017 规格与验收 ID 状态
10-artifacts/                 真实运行产物（PDF→文本→IR 样例，可直接对照契约）
11-git-history.md             近期 26 个提交的意图脉络
metrics.json                  机器可读的规模/索引数据
```

---

## 5. 三条"请不要做"的事

1. **不要读 `specs/001~013` 全文**：那些是已 DONE 的封版规格，只需看 `09-specs-index.md` 的状态与契约摘要；真要细节再看对应 `acceptance.md` 的条目。
2. **不要读 `backend/tests` 全部**：587 用例、1.6 万行；改某模块时只跑对应测试文件。
3. **不要"顺手清理"以下东西**（详见 `08-refactor-guide.md`）：`parsed_text.md` 语义、`evidence_uid` v1 公式、`sql/**`、`specs/**` 封版部分、`07_mineru` 的 v1 行为分支。
