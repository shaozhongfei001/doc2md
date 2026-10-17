# 08 · 重构指南：红线 / 安全区 / 验证门 / 建议顺序

## 1. 五条红线（动了就等于换项目）

| # | 红线 | 为什么 |
|---|---|---|
| **L-1** | 不改 `parsed_text.md` 的语义与 hash；不回写它 | 它是所有 offset / 证据 / chunk 的基准，改一次全链路证据失效 |
| **L-2** | 不改 `evidence_uid` 的 v1（`section_quote`）公式 | 已有证据行的 ID 会全变，等于清空证据库 |
| **L-3** | 不动 `sql/**`（尤其 migrations） | 001~017 是零迁移；新增表/列必须 Owner 批准 |
| **L-4** | 不改 `specs/001~016` 的封版内容 | 规格即验收；要改语义必须先加"差异决议"，不是改文档 |
| **L-5** | 不破坏 fail-closed 与"缺数据不编造" | 这两条是全项目最贵的教训（假绿 + 静默错配） |

补充两条工程红线：

- **L-6** 不引入硬编码 DB 连接/凭据（历史导致 123 个用例静默 skip）。
- **L-7** 不新增"只在本机成立"的依赖调用（`magic-pdf` 的公式模型已在别处崩过）。

---

## 2. 安全区（改这里风险低、收益高）

| 区 | 现状 | 可做的重构 | 风险 |
|---|---|---|---|
| `cli/main.py` | 1,216 行 / 19 命令，命令内造服务 + 打印 | 命令薄壳化、输出抽 `render.py`、参数模型化 | 低（有 CLI 测试 + 4 个 `--help` 已知红） |
| `parse_quality_checker.py` | 1,733 行，8+5 类检查硬编码 | `Rule` 协议 + 注册表；报告装配只认注册表 | 中（需保持 18 旧 code 行为逐字节一致） |
| 四个 parser 服务 | 接口近似但不同构 | 抽 `ParserAdapter` Protocol；统一 `ParseItem/ParseError` | 中（要与 005/007 契约对齐） |
| 配置加载 | 9 个 yaml 各自兜底 | 统一 schema 校验 + fail-fast + 启动自检 | 低-中（默认值必须与现网一致） |
| 错误码 | `DECISION_*` 分散 | 收敛 `errors.py` 分层枚举 | 低（纯改名 + 别名兼容期） |
| `document_ir_builder.py` | 500 行，纯数据装配 | 拆 `mapping/`（按 type 一个 mapper）+ `normalizer` | 低（115 用例护着） |
| 测试基建 | 共享 DB、CWD 敏感 | 库级隔离 / marker / factory fixtures | 中（但收益最大：消除 flaky） |

---

## 3. 必须保持的接口（外部可见契约）

```text
① 磁盘产物：parsed_text.md / parsed_metadata.json / parse_manifest.json（键集 + 路径基准）
② 目录布局：raw_vault/by_hash/ab/cd/<sha>/original.bin、parsed/by_hash/ab/cd/<sha>/...
③ DB 22 张表与其关键列（尤其 kb_document_chunk.start_offset/end_offset、kb_evidence.source_char_*）
④ CLI 19 个命令名 + 主要选项（脚本/文档/CI 依赖）
⑤ 状态枚举取值（manifest.status / decision / route_type / issue codes）
⑥ IR schema document-ir/1.0 的封闭枚举与必填键
```

> 允许 additive 扩键；**不允许**改既有键名/语义。

---

## 4. 验证门（每步重构都要过）

```text
G1 全量测试：613+ passed，failed ≤ 4（必须仍是那 4 个 --help），skipped ≤ 2
G2 契约测试全绿：test_parser_router_v2 / test_pdf_sniffing / test_document_ir / test_image_parser / test_evidence_chain
G3 变异复验：抽 2 条否定断言做变异 → FAIL → 还原 PASS → md5 一致
G4 产物比对：同输入重跑，parsed_text.md hash、manifest 既有键值不变
G5 零迁移：`sql/migrations` 无新增；`git status` 仅预期文件
```

**建议**：把 G1/G2/G5 做成 pre-commit 或 CI 第一道；G3/G4 做 nightly（成本高）。

---

## 5. 建议重构顺序（按 ROI / 风险比）

| 阶段 | 动作 | 收益 | 风险 | 前置 |
|---|---|---|---|---|
| **S-1** | 测试基建：DB 隔离（独立 schema 或事务回滚）+ 可选依赖 marker | 消除 flaky，验收信号可信 | 低 | 无 |
| **S-2** | 修 `--help` 基线（钉 `click<8.2` 或升 typer），让测试真正全绿 | 建立"全绿即正常"的基线 | 低 | S-1 |
| **S-3** | `cli/main.py` 薄壳化 + 输出层抽取 | 命令可测、可组合 | 低 | S-2 |
| **S-4** | `ParserAdapter` Protocol 收敛四个 parser | 消除重复、便于加新引擎 | 中 | S-3 |
| **S-5** | `quality_checker` 规则注册表化（先兼容 18+5 个 code） | 加检查从"改 5 处"变"加 1 个 Rule" | 中 | S-4 |
| **S-6** | 配置 schema 化 + 启动自检（缺依赖/缺模型/缺配置早报） | 消除"静默用默认值" | 低-中 | S-2 |
| **S-7** | 017 剩余阶段（P3 渲染/服务/CLI → P4 VLM/证据链） | 打通 IR 全链路 | 高（新功能） | S-4 |
| **S-8** | `bbox` 映射（`middle.json` → 块） + 单位实测冻结 | 证据可定位 | 中 | S-7 |

> **不要**在 S-1/S-2 之前动业务代码：你会拿一个 flaky 且 4 红信的测试套件当"回归护栏"，判断力会失真。

---

## 6. 迁移策略建议（strangler，而非重写）

1. **新接口先并存**：例如 `ParserAdapter` 与旧服务共存，用适配器包装旧实现，测试跑两遍对比。
2. **按调用点切换**：CLI 先切新入口，旧的保留一个 `--legacy` 开关一版。
3. **契约快照**：对同一批 fixture，新旧实现产出 `parsed_text.md` / `manifest` 必须逐键相等（允许 novel keys）。
4. **删旧代码放在最后一步**，且以"契约快照 + 全量绿"为前提。

---

## 7. 交付要求（如果你要回交给本项目）

- 每个阶段一个 commit 系列，附：改了什么、为什么、如何验证（命令 + 数字）。
- 任何"新增拒绝逻辑"必须附变异证据（变异→FAIL / 还原→PASS）。
- 任何契约变更必须附**差异决议**（改哪个键、谁消费它、兼容期多长）。
- 不要顺手格式化全仓（历史 diff 会失控）。
