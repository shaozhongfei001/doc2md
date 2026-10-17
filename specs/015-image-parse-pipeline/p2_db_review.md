# 015 Image Parse Pipeline — P2 DB & Data Review

> Role: Independent DB & Data Reviewer（ir-architect）— **独立复核者**，非 015 实现者
> Spec: `specs/015-image-parse-pipeline/`
> Stage: **P2 DB & Data Read Review**（`tasks.md:230` 流程链 `… → STOP → P2 DB Review → QA → E2E → TL Final Review`）
> Gate owner: Tech Lead（报告由复核者产出，**最终 PASS/FAIL 由 TL 裁定**）
> Reviewed state: 015 图像解析流水线**实现态**（POST-implementation 治理门禁）
> Date: 2026-10-16
> Supersedes: `specs/015-image-parse-pipeline/evidence/p2_db_review.md`（路径更正前置件，已移除；以本件为准）
> 判据（TL 采纳）：**additive-only、零 schema 变更、读多写少且写仅经 006 registry**

---

## 1. Gate Conclusion

**P2-GATE: PASS**

015 图片解析流水线在 **DB & Data 维度**满足：**零 schema/migration 变更**、**additive-only（不新增表/不新增列）**、**读多写少且写仅经 006 `ParseRegistryService`**、**dry-run 零写**、**014/016/017 边界零触碰**。DB 写面无越界，**无 FAIL 项、无阻塞项**。

**档位说明**：本件取 **PASS**（而非 `PASS WITH CONSTRAINTS`）。理由 —— 上述不变量在**实现态即已闭合**，不存在需 QA/E2E 前置裁定的约束性缺口或条件性批准；下列「锁定不变量」为 QA/E2E 期间**须保持**之既有事实（informational lock），非待整改项。

**Locked Invariants（锁定不变量，QA/E2E 须保持）**：

```text
I1  015 零 schema 变更 —— 复用 006 既有表（kb_parse_run / kb_parse_result / kb_parsed_artifact）；不得新增/修改 sql/** 或迁移。
I2  写仅经白名单服务 ParseRegistryService.register_parse_report()；015 服务层禁 raw SQL 写 / 禁直接 session.add/commit/flush。
I3  读路径三表只读：kb_file_content / kb_file_instance / kb_raw_vault_object（+ kb_document 间接）—— 015 不得写入。
I4  dry-run 零写 —— register=register and not dry_run；dry-run 不产报告、不入库。
I5  parser_name 恒为常量 "image_ocr"；dry_run 字段仅报告内容，kb_parse_run.dry_run 恒 0（006 schema 注释即锁定）。
I6  014/016/017 边界零触碰（014 四冻结件 md5 不变；016/017 无 backend 代码）。
```

**Migration:** **NOT required** —— 015 消费 006 既有表，**不自建、不迁移**。

**DB write:** **APPROVED** —— 仅 006 三表（`kb_parse_run` / `kb_parse_result` / `kb_parsed_artifact`），且**唯一经** `ParseRegistryService`。

**Filesystem write:** **APPROVED** —— 仅 015 自有产物（`parsed/` 下本管线三件套 + 批次报告）；`raw_vault`/`original.bin` 只读。

---

## 2. Mandatory Questionnaire

| # | Question | Answer |
|---|----------|--------|
| 1 | 015 引入新表/新列？ | **NO** —— additive-only，复用 006 |
| 2 | 需要 schema migration？ | **NO** |
| 3 | 写库路径数量？ | **唯一 1 条** —— `image_parser.py:1195` → `ParseRegistryService.register_parse_report()` |
| 4 | 015 服务层有 raw SQL 写？ | **NO** —— `image_parser.py` 内 `.add/.commit/.flush/.execute/.executemany` 计数 = **0**；无 SQL `text()` |
| 5 | 015 服务层直接 session 写？ | **NO** —— `session` 仅用于**三表只读**（`:668-697`）+ 交服务（`:1194`） |
| 6 | 读哪些表？ | `kb_file_content` / `kb_raw_vault_object` / `kb_file_instance`（+ `kb_document`）—— **SELECT only** |
| 7 | 写哪些表？ | `kb_parse_run` / `kb_parse_result` / `kb_parsed_artifact` —— 经 006 registry |
| 8 | dry-run 是否写库？ | **NO** —— 三重防护（见 §5.3） |
| 9 | `parser_name` 取值？ | 常量 `"image_ocr"`（`image_parser.py:64`），无用户可控注入 |
| 10 | ORM 模型齐备？ | **YES** —— 读/写模型均已存在，**015 不新增模型** |
| 11 | 014/016/017 是否被触碰？ | **NO** —— 014 四冻结件 md5 全命中；016/017 仅 spec（`??`），无 backend 代码 |
| 12 | Schema 缺口 → STOP？ | **YES（未触发）** —— 若写需新列/新表 → migration spec 先于实现 |

---

## 3. Schema Verification

### 3.1 读表（015 只读，不写）

| Table | ORM | 文件 | 015 访问 | 依据行 |
|-------|-----|------|----------|--------|
| `kb_file_content` | `KbFileContent` | `models/file.py:49` | SELECT（候选集） | `image_parser.py:657` |
| `kb_file_instance` | `KbFileInstance` | `models/file.py:15` | SELECT（ext 回退） | `image_parser.py:690` |
| `kb_raw_vault_object` | `KbRawVaultObject` | `models/vault.py:11` | SELECT（vault_dir） | `image_parser.py:670` |
| `kb_document` | `KbDocument` | `models/document.py` | SELECT（parse_status 回写由 006 服务，非 015 直写） | 经 registry |

读取代码块（`image_parser.py:_load_candidates_from_db` / `_resolve_fallback_ext`，`:655-697`）：

```668:697:backend/app/services/image_parser.py
        with self.session_factory() as session:
            for row in session.scalars(query).all():            # select(KbFileContent)
                vault_object = session.scalar(...)              # select(KbRawVaultObject)
                fallback_ext = self._resolve_fallback_ext(session, row)
                ...
        # _resolve_fallback_ext：
        instance = session.scalar(...)                          # select(KbFileInstance)
```

### 3.2 写表（006 既有，015 复用）

来源：`sql/migrations/006_parse_registry_v1.sql`（**既有**，015 **未改**）。

| Table | 用途 | 幂等键（既有 UNIQUE） |
|-------|------|----------------------|
| `kb_parse_run` | 批次运行登记 | `run_uid` UNIQUE；`uk_parse_run_report (report_path(512), parser_adapter_version)` |
| `kb_parse_result` | 每内容解析结果 | `uk_parse_result_run_content (run_uid, content_uid, parser_adapter_version)` |
| `kb_parsed_artifact` | 磁盘产物索引 | `uk_artifact_scope (run_uid, content_uid, artifact_type, parser_name, parser_adapter_version)` |

`kb_parse_run.dry_run` 列注释（006 L13）= `'持久化 run 恒为 0'` —— **schema 层即锁定「入库 run 不可是 dry-run」**，与 §5.3 三重闸一致。

### 3.3 Migration assessment

```text
sql/ 相对 HEAD 零改动（git diff --stat HEAD -- sql/ = 空）。
alembic/ 目录不存在；migrations/ 仅既有 006（含 _down + .gitkeep）。
015 五代码件 grep 'create_all|Base.metadata|declarative_base|alembic|MetaData(' = 0 命中。
015 消费 006 既有表，字段充分（parser_name/parser_adapter_version/filters_json/report_path 等齐全）。
=> Migration NOT required for 015.
```

---

## 4. Report → DB Field Mapping（注册路径）

015 批次报告（`parse_image_report_{UTC}.json`）字段 → 006 表列映射：

| 报告字段 | 来源行 | 目标列 | 状态 |
|----------|--------|--------|------|
| `report_type` | `image_parser.py:1159` | （校验用，非直存） | OK |
| `parser_name` = `"image_ocr"` | `:1160` | `kb_parse_run.parser_name` | OK — 常量 |
| `parser_adapter_version` = `"015_mvp_v1"` | `:1161` | `kb_parse_run.parser_adapter_version` | OK — 常量 |
| `filters` | `:1163` | `kb_parse_run.filters_json` | OK — JSON |
| `summary.*` | `:1170-1171` 附近 | `total/in_scope/parsed/skipped/failed/empty_count` | OK |
| `items[].content_uid / sha256 / status / route_type / parser_profile / parsed_dir / manifest_path / metadata_path / text_path` | `:971-1043` | `kb_parse_result.*`（`decision`/`status`/`route_type`/`parsed_dir`/`manifest_path`/`metadata_path`/`text_path` …） | OK |
| items → 磁盘产物（text/metadata/manifest/report） | `:1033` 等 | `kb_parsed_artifact`（`PARSED_TEXT`/`PARSED_METADATA`/`PARSE_MANIFEST`/`PARSE_REPORT`） | OK |

`parser_profile` / `engine_actual` / `fallback_reason` 为 015 扩展语义，落 `kb_parse_result.metadata`（JSON）—— **无需新增列**（additive-only 成立）。

---

## 5. Write Path & Idempotency

### 5.1 唯一写路径

```1189:1195:backend/app/services/image_parser.py
    def _register_report(self, *, report_path: Path, registry_service: Any | None) -> None:
        service = registry_service
        if service is None:
            from app.services.parse_registry import ParseRegistryService
            service = ParseRegistryService(self.session_factory)
        service.register_parse_report(report_path=report_path)
```

**写序列（先文件后 DB）**（`image_parser.py:615-629`）：

```text
① result.report_path = self._write_batch_report(...)   # 先落 JSON 报告（文件制品）
② if register: self._register_report(...)              # 再入库（唯一 DB 写）
```

入库消费**已落盘报告路径**（`register_parse_report(report_path=...)`），非内存态 —— 保证 DB 登记有持久文件依据。

### 5.2 白名单服务内部写（006 既有，非 015 引入）

`parse_registry.py:227-296`：`self.session_factory()` → `_get_or_create_run_for_report` → `session.commit()`（`:244`）→ `create_parse_run(RUNNING)` → `session.commit()`（`:253`）→ 逐 item `with session.begin()`（`:259`）+ 失败逐条 `session.rollback()`（`:271`）并记 `DB_ERROR` → run 级 `PARSE_REPORT` 产物登记（`:285`）。

### 5.3 Idempotency（幂等键，均 006 既有）

```text
kb_parse_run          : run_uid UNIQUE + uk_parse_run_report(report_path(512), parser_adapter_version)
kb_parse_result       : uk_parse_result_run_content(run_uid, content_uid, parser_adapter_version)
kb_parsed_artifact    : uk_artifact_scope(run_uid, content_uid, artifact_type, parser_name, parser_adapter_version)
重跑幂等              : _get_or_create_run_for_report（同 report_path+adapter_version → 复用 run）
015 侧幂等键          : _should_skip_idempotent（:795-813）按 manifest 的 parser_name/adapter_version/profile 判定
```

### 5.4 dry-run 零写（三重防护）

```text
闸1 CLI 入口   : cli/main.py:1186  register = register and not dry_run    → dry-run 时 register 恒 False
闸2 服务内层   : image_parser.py:615  if not dry_run:  + 同时守 _write_batch_report 与 _register_report
闸3 下游兜底   : parse_registry.py:187-191  拒绝 report['dry_run'] is True（ERROR_INVALID_DRY_RUN_REPORT）
dry-run 分支   : image_parser.py:576-581（would_parse + continue，不调引擎、不落三件套）
```

---

## 6. ORM Status

| Model | 文件 | 状态 | 015 角色 |
|-------|------|------|----------|
| `KbFileContent` | `models/file.py:49` | Exists | 只读 SELECT |
| `KbFileInstance` | `models/file.py:15` | Exists | 只读 SELECT |
| `KbRawVaultObject` | `models/vault.py:11` | Exists | 只读 SELECT |
| `KbParseRun` | `models/parse_registry.py:12` | Exists | 写（经 006 服务） |
| `KbParseResult` | `models/parse_registry.py:58` | Exists | 写（经 006 服务） |
| `KbParsedArtifact` | `models/parse_registry.py:109` | Exists | 写（经 006 服务） |

**015 不新增任何 ORM 模型**（additive-only 在 ORM 层亦成立）。

**015 导入清单**（`image_parser.py`）：`:47 create_db_engine/create_session_factory`、`:58 KbFileContent/KbFileInstance`、`:59 KbRawVaultObject`（**无 schema/迁移模块导入**）。

---

## 7. Config Verification

| 项 | 来源 | 值 | 备注 |
|----|------|----|------|
| 015 配置文件 | `config/image_parse.yaml` | `version: 1` | 缺失时用内置同值默认（`load_image_parse_config`） |
| `parser.adapter_version` | `image_parse.yaml:11` | `"015_mvp_v1"` | 幂等键组成 |
| 边界声明 | `image_parse.yaml:6` | 「与 `config/pdf_sniffing.yaml`（014 独享 FROZEN）互不相干，禁止交叉修改」 | 边界自证 |
| DB 连接 | `AppConfig.storage` + `create_db_engine` | 复用全局 | 015 不自定义 DSN |

> 015 **未新增任何 DB 连接/DSN 配置**；仅解析自身解析参数。

---

## 8. P2 Mandatory Rulings

| # | Ruling | Decision |
|---|--------|----------|
| 1 | 015 复用 006 registry SQL？ | **YES** — 无 migration |
| 2 | 015 新增 ORM 模型？ | **NO** — 全 Exists |
| 3 | 修改既有 sql/migration？ | **NO** |
| 4 | DB 写白名单 | **仅** `kb_parse_run` / `kb_parse_result` / `kb_parsed_artifact` |
| 5 | DB 写唯一入口 | **`ParseRegistryService.register_parse_report()`**（`image_parser.py:1195`） |
| 6 | 015 服务层 raw SQL 写 | **FORBIDDEN**（实测 0 命中） |
| 7 | 读表 | `kb_file_content` / `kb_file_instance` / `kb_raw_vault_object`（+ `kb_document`）**SELECT only** |
| 8 | dry-run 写库 | **NONE** — 三重闸 |
| 9 | `parser_name` | 常量 **`"image_ocr"`**；`dry_run` 入库恒 0 |
| 10 | 写序列 | **先文件报告 → 后入库** |
| 11 | 014/016/017 边界 | **零触碰**（014 四冻结件 md5 全命中；016/017 无 backend 代码） |
| 12 | Schema 缺口 → STOP？ | **YES（未触发）** |

---

## 9. Scope Preview（QA/E2E 阶段）

### 9.1 Allowed（现态已实现）

```text
backend/app/services/image_parser.py            # 管线主服务
backend/app/adapters/image_preprocess.py
backend/app/adapters/image_quality.py
backend/app/adapters/ocr_paddle.py              # 惰性 import
backend/app/adapters/vlm_adapter.py             # mock
backend/app/cli/main.py                         # parse-images 子命令（append，171/0）
config/image_parse.yaml
backend/tests/test_image_parser.py
parsed/** 下本管线三件套 + 批次报告（文件写）
kb_parse_run / kb_parse_result / kb_parsed_artifact  DML（经 006 服务）
```

### 9.2 Forbidden

```text
sql/** / migrations/** 变更（新增表/列）
014 独享件：config/pdf_sniffing.yaml（FROZEN）
014 冻结件：core/pdf_sniffing.py / core/parser_routing.py / services/mineru_pdf_parser.py
016 / 017 任何实现代码
015 服务层 raw SQL 写 / 直接 session.add/commit/flush
kb_file_content / kb_file_instance / kb_raw_vault_object 写入
raw_vault original.bin 读取或变更
```

---

## 10. Risks & Non-blocking Notes

| ID | Note | Severity |
|----|------|----------|
| N1 | `_register_report` 异常向上抛出（fail-loud，非静默）→ 「报告已落盘 + 未入库」部分态，但显式报错、可幂等重跑 | Low — 建议 E2E 覆盖「入库失败可重试」 |
| N2 | `register_parse_report` 内多次 `commit()`（`:244/:253`）+ 逐 item `begin()` —— 批中途失败可致部分提交（逐条 rollback + 记 DB_ERROR） | Low — **属 006 既有受控语义，非 015 引入** |
| N3 | `parser_profile` / `engine_actual` / `fallback_reason` 无专用列，落 `metadata` JSON | Low — additive-only 下合法 |

**均不阻塞 P2 通过。** N1/N2 为运行期健壮性观察，非 DB 写越界；如需整改应另立 006 事项。

---

## 11. before / after 现拼 md5（pre-declare）

**before** 采集：`2026-10-16 19:16:42`（本件写盘前）。
**after** 现拼：见本块末（本件为**本任务唯一写动作**）。

| 文件 | before md5 | 变动 |
|------|-----------|------|
| `backend/app/services/image_parser.py` | `0d6aaf31ddc9605be88e64ba6ab905d7` | 被复核件，**未改** |
| `backend/app/cli/main.py` | `f864a1830b823605f9230631aef0ed4c` | 被复核件，**未改**（015 侧 171/0 纯 append） |
| `backend/app/services/parse_registry.py` | `ac494c7901dc581d847635ed3ca5eed8` | 只读核对，**未改** |
| `config/image_parse.yaml` | `8c19fd68ca0688fead91d59426fc8865` | 被复核件，**未改** |
| `sql/001_init_schema_v1_1.sql` | `bf3c8ee66aa26c8533546110c66050e9` | **未改** |
| `sql/migrations/006_parse_registry_v1.sql` | `ac9d52bf895ba07e6040ae0e083da9a6` | **未改** |
| `sql/migrations/006_parse_registry_v1_down.sql` | `83ff7f351a3ccd62a1620dc56b78d193` | **未改** |
| 014 禁区 `core/pdf_sniffing.py` | `f6799625eccfaac3e4d7078b0099cf03` | 冻结，**未改** |
| 014 禁区 `core/parser_routing.py` | `f41c6441c84b73c579bd55e11e4173bf` | 冻结，**未改** |
| 014 禁区 `services/mineru_pdf_parser.py` | `d8383fdedbfbe161963636b323d08f25` | 冻结，**未改** |
| 014 禁区 `config/pdf_sniffing.yaml` | `8a790c352a61f26b4288fb1b6134f5f0` | 冻结，**未改** |
| **本件** `specs/015-image-parse-pipeline/p2_db_review.md` | **不存在**（T0） | **本任务唯一新增** |

**红线判据**：除本件（唯一新增）外，全部被复核文件 **after ≡ before（逐字一致）**。

---

## 12. P2 Exit Checklist

```text
[x] 015 读写清单现拼独立取证（image_parser.py 三表只读 + 唯一写经 registry）
[x] 零 schema/migration 变更（sql/ diff 空；alembic 无；015 代码 0 命中）
[x] 写仅经 ParseRegistryService；服务层 raw SQL 写 = 0
[x] dry-run 零写（三重闸）
[x] 014/016/017 边界零触碰（014 四冻结件 md5 全命中）
[x] parser_name="image_ocr" 注册/manifest DB 安全性核验
[x] before/after 现拼 md5（§11）
[x] 交叉校验 TL 参考值（image_parser.py 0d6aaf31… 逐字一致）
[x] PASS 建议已出（TL 最终裁定）
[ ] TL 裁定（PASS / PASS WITH CONSTRAINTS / FAIL）
[ ] 过门 → QA → E2E → TL Final Review
```

---

## 13. STOP

P2 DB & Data Review 完成。**建议：PASS，放行进入 `QA → E2E → TL Final Review`**（最终裁定权归 TL）。

015 实现态**未因本次复核发生任何代码/ SQL 变更**；本次唯一写 = 本报告文件。

---

## 附录 A：与前置（路径更正）件的关系

前置件 `specs/015-image-parse-pipeline/evidence/p2_db_review.md`（md5 `de45c70bb86794e6288c1a6aaf8d8beb`）系**路径更正前**产物；按 TL 指令「对齐 011/013 先例（`specs/<id>/p2_db_review.md`，非 evidence/ 子目录）」，**已移除该误置件，内容重编入本件（平铺路径 + 先例结构 + 档位 PASS/PASS WITH CONSTRAINTS/FAIL）**。取证结论逐条一致（6/6 PASS），无实质变化。

---

## 附录 B：冻结声明失效更正（team-lead，2026-10-17）

本报告 §11 表格中下列两行在**复核时点（2026-10-16）为真**，但已被 2026-10-17 的修复变更，现盘实测值如下：

| 制品 | 本报告记录 | 现盘值（现拼） | 变更说明 |
|---|---|---|---|
| `backend/app/services/image_parser.py` | `0d6aaf31ddc9605be88e64ba6ab905d7` | `927b9029776d4221e1113142936236de` | **真实 bug 修复（1 行）**：`_resolve_fallback_ext` 用 `KbFileInstance.instance_uid` 查询，模型实际字段为 `file_instance_uid`（`app/models/file.py:19`）。任何带 `master_file_instance_uid` 的行走该分支必抛 `AttributeError` —— 即「回退扩展名解析」此前从未生效。改为 `file_instance_uid` 后，`tests/test_image_parser.py` 相关用例由 FAIL 转 PASS。 |
| 014 禁区 `backend/app/services/mineru_pdf_parser.py` | `d8383fdedbfbe161963636b323d08f25` | `a82a3653ec7998d30eabfd16e99b0a9e` | 失败信息可诊断性增强（新增 `_process_output_tail` + 2 处错误消息），见 014 `acceptance.md` §7 登记。判定逻辑 / 判定码 / 清理语义均未动。 |

> **口径**：P2 的取证结论（读写清单、零 schema/migration 变更、写仅经 `ParseRegistryService`、dry-run 零写）**不因上述变更改变**——两处改动都未新增任何写路径、未触碰 `sql/**`、未改变 registry 调用链。
> 但「**未改**」字样已不适用于现盘：后续 015 的验收引用一律以本附录现盘 md5 为准，原表保留为复核时点快照。

### B.1 独立复核（verify-015-fix，2026-10-17）

**复核对象**：`backend/app/services/image_parser.py` 的 1 行代修——`_resolve_fallback_ext`（`:687-697`）由 `KbFileInstance.instance_uid` 改为 `KbFileInstance.file_instance_uid`。现盘 md5 `927b9029776d4221e1113142936236de`（本次复核**未改动任何 app/ tests/ config/ 文件**，唯一写 = 本节）。

**1. 字段定义（贴原行）**

```19:23:backend/app/models/file.py
    file_instance_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    ...
    file_ext: Mapped[str | None] = mapped_column(String(32))
```

- `KbFileInstance`（`__tablename__ = "kb_file_instance"`，`models/file.py:15`）**无** `instance_uid` 字段；DB 列名亦为 `file_instance_uid`（其前身语义见 `sql/001_init_schema_v1_1.sql`）。实测：`KbFileInstance.instance_uid` → `AttributeError: type object 'KbFileInstance' has no attribute 'instance_uid'`；`hasattr(KbFileInstance, "file_instance_uid") is True`。
- `instance_uid` 全仓仅两处出现为**局部变量名 / 日志占位符**（`services/inventory_scanner.py:139` `instance_uid = instance.file_instance_uid`、`:153` 日志格式串），不是任何模型的字段，**不构成对 team-lead 判断的反例**。

**2. 调用链与语义**

```text
_load_candidates_from_db (:673)  fallback_ext = self._resolve_fallback_ext(session, row)   # 每条候选无条件调用
  └─ _resolve_fallback_ext (:687)  row.master_file_instance_uid 为空 → None（安全短路）
       instance = SELECT KbFileInstance WHERE file_instance_uid = row.master_file_instance_uid
       instance is None → None
       返回 normalize_file_ext(instance.file_ext or ext_from_path(instance.source_path))
消费点 (:679)  normalize_file_ext(row.file_ext) or fallback_ext     # 仅当 content.file_ext 空/不可归一时回退值生效
```

- 触发条件：`KbFileContent.master_file_instance_uid` 非空。该列由 `inventory_scanner.py:274`（`content.master_file_instance_uid = master.file_instance_uid`）与 `duplicate_governance.py:223` 写入，取值**就是** `kb_file_instance.file_instance_uid`，故等值 join 命名空间一致，**取到的是该内容的主实例**，扩展名/文件名归属正确。
- 语义结论：修复后取值正确；`file_ext` 缺失时以 `source_path` 推断，与 `file_content_vault.py:235-238`、`parser_router.py:372-375`、`mineru_pdf_parser.py:906-909`、`markitdown_parser.py:379-382` 四处既有实现**完全同构**（同一 `file_instance_uid` 关联 + 同一 `file_ext or ext_from_path` 回退），即 image_parser 原为唯一异类。

**3. 同类缺陷扫描**

```text
grep -rnoE 'KbFileInstance\.[a-zA-Z_0-9]+' backend/app backend/tests
  → content_uid(1) created_at(6) file_instance_uid(5) id(6) is_duplicate_instance(1)
    sha256(10) source_path(12) source_path_hash(2) status(6)   —— 全部为模型实有字段
grep -rnE '\.instance_uid\b' backend/            → NONE
grep -rnE '(^|[^_a-zA-Z])instance_uid' app/ tests/ → 仅上述 2 处局部变量/日志串
grep -rn 'instance_uid' backend/app/models/      → file.py:19(file_instance_uid) file.py:58/duplicate.py:19(master_file_instance_uid)
```

**扫描结论：零同类缺陷。** 其余四个解析/落库服务及测试桩均使用正确字段名（测试桩如 `test_duplicate_governance.py:163/196/199/504` 亦为 `item.file_instance_uid`）。本修复后全仓不再存在错误的 `instance_uid` 属性访问。

**4. 实测（本人亲自执行，非引用）**

```text
cd backend && PYTHONPATH=. /home/szf/anaconda3/bin/python -m pytest -q -rs tests/test_image_parser.py
（PKB_MYSQL_* 由 config/app.yaml 的 mysql 段注入，未回显密码）
=> 53 passed, 2 skipped, 0 failed in 9.03s
SKIPPED [1] tests/test_image_parser.py:404: paddleocr installed; dependency-missing branch unreachable
SKIPPED [1] tests/test_image_parser.py:415: paddleocr installed
```

两次 skip **均为 paddleocr 已安装**导致「依赖缺失」分支不可达，**无 MySQL 不可用 skip** → `test_db_candidate_loader_returns_image_candidates` 本轮**真实执行**。

**分支是否真被进入（独立取证）**：同一配置下实测 `_load_candidates(...)` 返回 **2 条候选，2/2 的 `master_file_instance_uid` 非空**；由于 `:673` 是无条件调用（早于 `:679` 的 `or fallback_ext` 短路），该用例必然进入回退分支 2 次。另对 `kb_file_content` 中 `master_file_instance_uid IS NOT NULL` 的 **14 行**逐行直接驱动 `_resolve_fallback_ext`：**14 次调用零异常**，其中 2 行命中返回扩展名（样例 `.pdf`）、12 行因主实例不在 `kb_file_instance` 而返回 `None`（属正常降级）。用例断言本身（`:1085-1088` 仅校验 list/64 位 sha/original_path 非空）**不能**证明分支被进入，故上述取证为必要补充。

**5. 对「原代码必抛 AttributeError」的独立判断**

**同意**，且有两条独立依据：① 上述 `AttributeError` 实测复现（类上无 `instance_uid`）；② 该属性访问发生在 **SQL 构造阶段**（`select(...).where(KbFileInstance.instance_uid == ...)`），早于任何 `session.scalar` 执行，因此是「加载即崩」而非「静默返回 None」。

**但对 team-lead 的影响面表述需补正一处**：实际后果**重于**「回退扩展名解析此前从未生效」。因 `_resolve_fallback_ext` 在 `_load_candidates_from_db` 中**无条件调用**，只要候选集里有**任一行** `master_file_instance_uid` 非空，整批 DB 候选加载即抛错中断，`parse-images` 的 DB 路径完全不可用（现盘 2/2 候选均带 master uid，故该用例修复前必为 FAIL）。「回退值取不到」只是其中最轻的一层表现。此补正不改变修复方向与结论，仅纠正对后果的定性。

**本节 verdict：修复正确，且无遗漏同类缺陷。** 建议 TL 以现盘 `927b9029776d4221e1113142936236de` 为准；如需将该 1 行纳入受控变更记录，建议同时把「后果定性」按上段补正为「DB 候选加载整链中断」。

### B.2 TL 定性采纳：后果从「降级」上调为「DB 路径整体不可用」（team-lead，2026-10-17）

采纳 `verify-015-fix` 的补正 —— 附录 B 中「回退扩展名解析此前从未生效」**低估了后果**：

- `_resolve_fallback_ext` 由 `_load_candidates_from_db`（`:673`）**无条件调用**，且属性访问发生在 **SQL 构造阶段**、早于 `session.scalar` 执行；
- 因此只要候选集中**任一行** `master_file_instance_uid` 非空，**整批 DB 候选加载即中断**，`parse-images` 的 DB 路径完全不可用；
- 「回退值取不到」只是最轻的一层表现。

**定性**：这是 **P0 级功能缺失（015 DB 路径整体不可用）**，不是可诊断性 / 边缘降级问题。

修复本身不变（`image_parser.py` 现盘 `927b9029776d4221e1113142936236de`）；复核实测 `tests/test_image_parser.py` = **53 passed / 2 skipped**（2 skip 均为 paddleocr 已安装使依赖缺失分支不可达，**无** MySQL-unavailable skip）。同类缺陷全仓扫描**零命中**（`KbFileInstance.*` 属性全部为实有字段；`.instance_uid` 在 `backend/` 下零命中）。
