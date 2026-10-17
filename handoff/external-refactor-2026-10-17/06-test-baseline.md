# 06 · 测试基线与质量方法

## 1. 怎么跑

```bash
cd backend
export PYTHONPATH=.
/home/szf/anaconda3/bin/python -m pytest -q                     # 全量
/home/szf/anaconda3/bin/python -m pytest -q tests/test_document_ir.py
/home/szf/anaconda3/bin/python -m pytest -q -rs                 # 看 skip 原因
```

**DB 测试需要注入连接**（否则大量 skip，属于**假绿**）：

```bash
export PKB_MYSQL_HOST=... PKB_MYSQL_PORT=... PKB_MYSQL_DATABASE=... \
       PKB_MYSQL_USER=... PKB_MYSQL_PASSWORD=...
```

> 历史教训：测试曾把库名/密码硬编码（`personal_kb`/`mahound`），在库名不同的机器上
> **123 个用例静默 skip**，"从未真正执行过"。现已统一走 `tests/_db_env.py`（env 优先、缺省回退旧值）。
> 重构时**不要**再引入硬编码连接串。

---

## 2. 当前基线（2026-10-17，`main@52a63ec`）

| 项 | 数值 |
|---|---|
| 全量 | **613 passed / 4 failed / 2 skipped** |
| 测试文件 | 20 个 / 587 个 `def test_`（收集数 613，含参数化） |
| 采集行数 | 16,161 行 |
| 最大文件 | `test_document_ir.py` 1,745 行 / 115 例 |
| 次大 | `test_parse_quality_checker_semantic.py` 1,368 行 / 60 例、`test_image_parser.py` 1,145 行 / 55 例 |

### 2.1 4 个"已知失败"（环境基线，不是回归）

```
tests/test_evidence_chain.py::test_cli_help_documents_contract
tests/test_markitdown_parser.py::test_cli_parse_markitdown_help
tests/test_parser_router.py::test_cli_route_parsers_help
tests/test_search_service.py::test_cli_help_documents_contract
```

**统一根因**：`click 8.4.1` × `typer 0.15.1` 不兼容 —— 任何 `--help` 渲染都抛
`TypeError: Parameter.make_metavar() missing 1 required positional argument: 'ctx'`。
**与业务代码无关**；修法二选一：钉 `click<8.2` 或升级 typer。重构时若要"全绿"，从这里入手。

### 2.2 2 个 skip 的正常原因

`paddleocr installed; dependency-missing branch unreachable` —— 本机装了 paddleocr，
而这两个用例专门验证"缺依赖时的降级"，故 skip。**在没有 paddleocr 的环境会真跑**。

---

## 3. 测试分层与约定

| 层 | 文件特征 | 是否触 DB | 说明 |
|---|---|---|---|
| 纯函数单测 | `test_document_ir.py`(校验器/builder)、`test_pdf_sniffing.py` | 否 | 最快，重构后应保持秒级 |
| 服务级 + DB | `test_parse_quality_checker.py`、`test_evidence_chain.py` | 是 | 需 `PKB_MYSQL_*`，自建 fixture 行并清理 |
| CLI 级 | `test_cli_*` / `CliRunner` | 部分是 | 受 click/typer 版本影响 |
| 契约/回归 | `test_parser_router_v2.py`（1,129 行） | 否 | 路由真值表 + fail-closed 不变式 |
| 变异复验 | 内嵌在各文件里的 `*_mutation` 用例 | 否 | 见 §4 |

**fixture 策略**：一律 `tmp_path` 现场构造（JSON 手写、图片用 Pillow 现生成），不往仓库塞大语料。
**DB fixture**：直接插 `kb_file_content` / `kb_parse_result` 行，用例结束按 sha256 前缀清理。

---

## 4. 变异方法论（本项目最重要的质量遗产）

> **背景**：曾有 6 个迭代"首报门禁全绿"，实际决定性断言全是**空转**——所有夹具都是合规正例，
> 拒绝分支从不进入，于是 `assert CODE not in codes` 恒真。后来统一改成「变异必须能把它打红」。

### 4.1 标准做法

1. 写**成对断言**：该报时报（正例）+ 不该报时不报（反例，显式 `not in`）。
2. 对每个否定断言，找出它依赖的**判定代码**（不是测试注释）。
3. 临时变异该判定（放宽/短路/删分支）→ 目标用例**必须 FAIL**。
4. 逐字还原 → **必须 PASS**，并核对文件 md5 回到原值。
5. 记录：变异点、变异态 md5、实测失败用例名、还原后 md5。

### 4.2 已积累的 20+ 个变异点（可直接复用）

| 迭代 | 变异 | 期望 |
|---|---|---|
| 014 | `CACHEABLE_SNIFF_STATUSES` 改为恒真 | 缓存用例 2 failed |
| 014 | `ENCRYPTED` 路由改为 `PDF_DIGITAL` | import 期 `AssertionError`（fail-closed 不变式） |
| 015 | `if ocr_result.mock:` → `if False and …` | Mock 落盘用例 2 failed |
| 015 | MockVlmClient 注入伪造文本 | 1 failed |
| 015 | `_unsupported_output` 谎称 `applied_ops` | 无 cv2 下 3 failed |
| 015 | 路由 row1 破坏（`if False and paddleocr_available`） | 双环境均 failed |
| 016 | 乱码/置信度阈值改成硬编码 | 对应用例 failed |
| 016 | 缺 confidence 时也产 `LOW_CONFIDENCE` | 3 failed（反编造） |
| 016 | 删 `min_sample_chars` 跳过 | 2 failed |
| 016 | 删 1.0+语义键 fail-closed 守卫 | 1 failed |
| 017 P1 | `reading_order` 只判越界 | 3 failed |
| 017 P1 | 缺键校验用 `.get` 补默认 | 1 failed |
| 017 P1 | 删 asset sha 对齐 | 1 failed |
| 017 P2 | `block_id` 改 0-based | 1 failed |

**重构验收建议**：改动任何"拒绝/不产出"逻辑后，至少抽 2 条否定断言做变异，证明没被架空。

---

## 5. 已知测试债（不要重复踩）

| # | 现象 | 根因 | 建议 |
|---|---|---|---|
| T-1 | 全量偶发多出 `test_chinese_path_integration` 失败（`StaleDataError: UPDATE kb_file_content expected 1 row`） | 共享测试库中的既有行被其他用例删/改，**跨文件顺序敏感** | 已确认与 017 无关；重构时应给 DB 测试做**库级隔离**（每 worker 独立 schema 或事务回滚） |
| T-2 | 偶发 `test_mineru_pdf_parser` 6 个失败，复跑即恢复 | 同上 | 同上 |
| T-3 | `pytest` 单跑文件与全量结果可能不同 | 用例依赖执行顺序/残留 | 重构目标：所有用例可独立运行 |
| T-4 | CLI `--help` 类用例全红 | click/typer 版本 | 见 §2.1 |
| T-5 | 无 cv2 环境需特殊跑法（`PYTHONPATH` 注入伪 `cv2` 模块） | 依赖可选化验证 | 建议做成 pytest marker：`@pytest.mark.no_cv2` |

---

## 6. 重构后的"最小验收门"（建议写进 CI）

```text
1) 全量：failed ≤ 4（且必须仍是那 4 个 --help），skipped ≤ 2
2) 契约测试必须全绿：test_parser_router_v2 / test_pdf_sniffing / test_document_ir / test_image_parser
3) 至少 2 条否定断言的变异复验：变异→FAIL、还原→PASS、md5 一致
4) 产物契约快照：同输入 → parsed_text.md hash 不变、manifest 既有键值不变
5) 零迁移：`git status --porcelain sql/migrations` 为空
```
