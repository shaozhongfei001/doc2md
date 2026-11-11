# 历史 feature 分支核查与清理决策（2026-11-12）

## 背景

`origin` 上长期保留 4 个 2026-06 的分支，一直未合入 `main`：

```
feature/001-file-inventory          39ccb47 / 3 独有提交
feature/002-file-content-vault      2f7eb46 / 4 独有提交
feature/003-duplicate-governance    ba38c84 / 14 独有提交
feature/004-parser-router           cccb982 / 19 独有提交（含前三者全部提交）
```

Owner 要求按两个维度验证后再决定是否合并：**分支本身内容是否正确/有价值**，
以及**是否对 `llmke` 有优化的支撑价值**。本文件是核查结论与决策留痕；
这些分支随后被删除，删除前已确认无需要合并的内容。

## 一、核查方法（只读）

1. 显式 `git fetch origin '+refs/heads/*:refs/remotes/origin/*'` 拉全远端引用（该 remote
   原本只配了 `main` 的 refspec）。
2. 用 `git ls-tree -r` 逐分支建 path→blob 映射，与 `main` 比较，剥离
   `.venv/` / `__pycache__` / `.pyc` / `.so` / `.gz` 等非源码对象，得到
   「分支独有」「main 独有」「同名内容不同」三类实质差异。
3. 逐个读取 `specs/009~012` 20 个文件的行数、字符数与正文，判断是否为实质设计。
4. 对比 `backend/app/services/` 与 `backend/app/core/` 的模块集合，判断旧代码
   是否有 `main` 缺失的能力。
5. 在 `llmke` 主仓检索等价能力关键词，判断是否已有替代实现。
6. `gh api repos/shaozhongfei001/doc2md` 确认仓库可见性。

## 二、维度一：分支本身的内容质量

**它们是同一条演进链上的 4 个旧快照，不是 4 个独立功能。** 004 的独有提交里
包含 001/002/003 的全部提交，属累积关系。

| 核查项 | 实测 |
|---|---|
| 规模 | `main` 793 跟踪文件；4 个分支 138 / 152 / 156 文件（001 例外，见下）；`main` 独有 656–679 个文件 |
| 001 仓库卫生 | 误提交整个 `backend/.venv`：12130 个非源码对象（5173 `.pyc`、5118 `.py`、104 `.so`、152 `.gz` 等），合计 12,152 文件 / 2,475,902 行插入。PUBLIC 仓库 175 MB 主要来源；002 起已删除 |
| 001 误提交配置 | `config/app.yaml`（现已被 `.gitignore` 忽略）含本机绝对路径，属不应入库的本地配置；002 起已删除 |
| 20 个「独有」文件 | 全部是 `specs/009~012` 的模板占位稿：9–34 行、261–430 字符；4 组 `spec.md` 除功能名外逐字相同，正文写「个人历史项目文档知识库 **V1.1** / **MySQL** 元数据 / **Streamlit** 管理台」；75 个 spec 文件中仅 7 个含代码块/接口/表结构，且均不在 009~012 |
| 26 个同名差异文件 | 是 `main` 的旧版：`cli/main.py` 6.4K→49.5K、`parser_router.py` 10K→15.6K、`requirements.txt` 197B→886B、`.gitignore` 353B→638B；`inventory_scanner.py` 两边完全一致，已在 main 中 |
| 旧代码模块 | `004 有 / main 无 = []`；`main` 反而多 14 个 services 模块 |
| 交接/协作文档 | `.cursor/rules/007-agent-collaboration.mdc`、`docs/feature_index.md` 等 main 中均已有更新版本 |

**结论**：内容不是伪造，但对当前项目已过时且产品方向不符（V1.1 / MySQL / Streamlit
vs 当前四件套 + ES/Milvus/图）；009~012 是模板占位稿而非设计资产；001 存在明确的
仓库卫生问题。没有需要合并的独有实质内容。

## 三、维度二：对 `llmke` 的支撑价值

**为零。** `llmke` 当前依赖的能力全部已在 `doc2md/main` 中，且更完整：

- 009~012 里「规划」的功能，`main` 已实装：`evidence_chain.py` 744 行、
  `curated_project_assets.py` 745 行、`search_service.py` 642 行，并有
  `handoff-010/011/012/013` 交接文档。
- 增量 / 去重 / 队列：`main` 有 `kb_diff.py` 369 行、`parse_queue.py` 444 行、
  `document_ir_builder.py` 等；旧分支只有 5 个 services 模块。
- `llmke` 主仓已具备等价能力：`sources.json`(7 文件)、`citations`(10)、
  `fence`(15)、`content_uid`(14)、`set_digest`(8)、`SKIPPED_FRESH`(7)、
  `parse_manifest`(4)、`document_ir`(5)。
- 18097 `kb_parse_service.py` 直接消费 `doc2md/main` 产出的 doc2md 四件套，
  链路完全不经过这 4 个历史分支。
- 下一步「生产语料 FULL_REBUILD 基线」所需的是 `main` 里已原样保留的
  `inventory_scanner.py` + `kb_diff.py` + `parse_queue.py`，旧分支帮不上。

合并的净效果是把 12130 个非源码对象、一份本地配置和 26 个旧版代码倒灌进
`main`，反而会破坏 `llmke` 正在依赖的解析链路。

## 四、决策

1. **不合并到 `main`。**
2. 按历史分支**清理**（删除），不按功能合并。收益：让误提交的 venv 对象进入
   服务端 GC、消除 PUBLIC 仓库上不该存在的旧配置入口、分支视图只剩 `main`。
3. 不归档 20 个模板 spec 正文（无信息量），只保留本核查结论。
4. 本地配置相关的后续处置由 Owner 决定，本次不处理。

## 五、清理执行

见文末「执行结果」段（删除后追加）。
