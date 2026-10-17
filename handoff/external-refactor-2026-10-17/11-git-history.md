# 11 · 近期提交脉络

> 用途：理解「最近改了什么、为什么」，避免重复已否掉的方案。完整 message 见 `git log`。

## 最近 40 个提交

```text
52a63ec feat(017-P2): Document IR builder + parser ir_source 保留；PDF 富文本端到端跑通
098d44c docs(017): 计划状态同步 P1=PASS，登记裁定链与两项过程债
90d691b docs(017): 追加裁定 R-15~R-18（P1 实现者提出的 4 处契约歧义）
e92eae3 feat(017-P1): Document IR schema 校验器 + fixture 构造器 + parsed_paths 三键
d752e40 docs(017): P1 契约审计 13 项 SPEC_GAP + TL 裁定 R-01~R-14 与三处文档收口
3354dbc docs(017): 无人值守执行计划 V1.0（7 阶段 / 双角色 / TL 硬验收门）
544772e docs(016): QA 追加复核 M9/M10 —— 全程序 12 次变异 12/12 命中
86c164f docs(016): 独立验收 QA_PASS + S1 三件收口，017 解锁
72078ab docs(016): 实现留痕同步 —— (c) 全字段相等证据、fail-closed 守卫、CLI E2E
5821c9c feat(016): 008 质检第五类语义检查（乱码率/置信度/表格/空正文）
15fcfc6 docs: 登记 016 规格内部冲突裁定与白名单例外（矩阵 §7）
8fe7ed7 docs(016): D-2 归属裁定 —— 不并入 016，留作 015 后续修订
df2ae25 test(015): 关闭 QA 遗留 O-1/O-2，015 转 QA_PASS
a863d02 test(015): 修复 A015-12「无 cv2 环境全绿」+ 记录独立验收结论
f269a13 docs: SPEC_INDEX 状态同步 + S1 文件级白名单冲突矩阵
5dc5fc2 docs(017): 统一文档中间表示设计稿 + 五件套（排期在 S1 之后，未开工）
2e42bbc docs(016): 008 质检第五类语义检查规格就绪（实现未开工）
8d4decc feat(015): 独立图片解析管线（预处理 → 质量评分 → PaddleOCR/VLM 路由）
9b70ea0 feat(014): PDF 内容嗅探路由 + 冻结件失败可诊断性
012d91a test: 测试 DB 连接环境变量化，忽略运行期缓存与工具产物
1e1432d merge(902): integrate 902-test-dataset sample dataset
ee5c8b7 docs(902): mark 902 DONE and clear active spec
8070b80 docs(902): record gate reports P2-P7 and handoff
6c47568 feat(902): add non-sensitive sample dataset with idempotent generator
4c4c042 docs(902): finalize 902 spec five-piece set
4c474b6 docs(902): set 902-test-dataset as ACTIVE spec
9fa6777 Update README.md
```

## 当前 HEAD

```text
52a63ec feat(017-P2): Document IR builder + parser ir_source 保留；PDF 富文本端到端跑通
 backend/app/services/document_ir_builder.py        |  500 +++++++
 backend/app/services/mineru_pdf_parser.py          |   39 +
 backend/tests/test_document_ir.py                  |  135 ++
 docs/demo/pdf-rich-to-text/README.md               |  128 ++
 ...738db740bddb1d72b1bbca14fe24dedee42949231fa.jpg |  Bin 0 -> 27198 bytes
 ...7bf004799a59bfd0760fb1b71c2908c2440fedbc664.jpg |  Bin 0 -> 45106 bytes
 docs/demo/pdf-rich-to-text/document_ir.json        |  271 ++++
 .../pdf-rich-to-text/ir_source/content_list.json   |   32 +
 .../ir_source/magic_pdf_patched.py                 |   38 +
 docs/demo/pdf-rich-to-text/ir_source/middle.json   | 1469 ++++++++++++++++++++
 .../pdf-rich-to-text/ir_source/mineru.config.json  |   18 +
 docs/demo/pdf-rich-to-text/parse_manifest.json     |   47 +
 docs/demo/pdf-rich-to-text/parsed_metadata.json    |   11 +
 docs/demo/pdf-rich-to-text/parsed_text.md          |   12 +
 14 files changed, 2700 insertions(+)
```

## 规格演进顺序（重要）

```text
001~013 基础链路（DONE，封版）
  ↓
014 PDF 内容嗅探（QA_PASS）→ 015 图片管线（QA_PASS）→ 016 语义质检（QA_PASS）   [S1]
  ↓
017 Document IR：P1 已验收 / P2 已实现（builder + ir_source）/ P3~P7 未做
```
