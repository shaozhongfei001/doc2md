# P0 · DB_REVIEW 复验（第 2 轮）

| 项目 | 值 |
|---|---|
| 审查角色 | `db-reviewer`（未参与 `backend/app/extensions/**` 代码编写，全程只读） |
| 复验对象 | `35d21fb`（FAIL-01/02 的修复提交），仅 5 个文件 |
| 漂移检查 | `git diff 35d21fb -- backend/app/extensions backend/tests/extensions` 为空 → 受审文件与提交一致 |
| 结论 | **PASS** |

## 1. FAIL-2026-10-18-01 复验：pass

审查方重跑原攻击用例（`base_dir=/tmp` + `project_root=<repo>`）：

| 输入 | 结果 |
|---|---|
| `<repo>/sql`、`<repo>/sql/migrations`、`<repo>/parsed`、`<repo>/raw_vault`、`<repo>/curated`、`<repo>/.cursor/rules`、`<repo>` 本身 | 全部 `E_CONFIG_ROOT_FORBIDDEN` |
| `sql/nested`（base 侧锚定） | 拒绝（双锚定两侧都有效） |
| `/tmp/x/extension_data`、`extension_data`、`./extension_data`、`data/ext` | 全部 ACCEPT（未过度收紧） |

端到端两条新测试被确认是**真断言**而非空转：`test_runner_passes_the_repo_anchor_so_the_outside_config_cannot_escape` 真正创建仓外 `config.yaml` 并写 `extension_root: <repo>/sql`，断言 `E_CONFIG_ROOT_FORBIDDEN`。

## 2. FAIL-2026-10-18-02 复验：pass

| AC | 结果 |
|---|---|
| ① 返回对象不含口令 | `StorageRoots` 的 repr/`__dict__` 均无标记；`hasattr password/mysql` 均 False；字段仅 7 个路径字段 |
| ② 无 mysql 段可加载 | 成功（旧 loader 在此路径会 `KeyError`） |
| ③ 缺 storage 段 | 受控 `E_CONFIG_INVALID`，非 `KeyError` |
| ④ 解耦 | `grep -rn "app\.core" backend/app/extensions` → NO_MATCH；`_load_legacy_config`/`legacy_config_loader` 已无残留调用点 |

真实 `config/app.yaml` 经窄加载器读取成功，`parsed_root` 与旧 `_resolve_storage_path` 口径一致（绝对路径原样、相对按 `<config_dir>/..` 解析）。

## 3. 第 2 轮新发现与处置（commit `8558349`）

### 3-1 发现 1（P3）守卫仍是 opt-in → 已改为默认开启

**事实**：`project_root` 默认 `None`，省略即回到原缺陷（审查方实测 `resolve_extension_root("<repo>/sql", base_dir="/tmp")` 仍 ACCEPT）。当前唯一生产调用点已传参，故无现网缺口，但属"依赖调用方记得传"的弱保证。

**处置**：`io/paths.py` 新增 `DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[4]`（由文件位置推导，不依赖 cwd），`project_root` 缺省取该值；显式传 `None` 也不再关闭仓库锚点。
**TL 实测**：省略 `project_root` 时 `sql` / `parsed` / 仓根三例均 REJECT，`/tmp/x/extension_data` 仍 ACCEPT。
**回归**：`test_repo_anchor_is_on_by_default`、`test_default_project_root_points_at_the_repository`。

### 3-2 发现 2（断言强于证据）→ 已改为可证口径

**事实**：`yaml.safe_load` 整体解析文档，`mysql` 子树在解析期**确实过内存**、随后被丢弃；原 docstring "never read, so no secret enters this process" 与测试名 `..._never_parses_...` 都强于实际能力。

**处置**：
- docstring 改为精确口径：解析期过内存，但**本层不保留、不挂载、不序列化**；
- 测试改名 `test_storage_loader_never_retains_the_mysql_section`，并新增断言：返回对象**任一字段**的 `repr` 与 dict 内容都不得含口令标记；
- 未采用 `yaml.compose`/流式按 key 提取：额外复杂度不改变风险结论（口令不再被保留/暴露），故把"解析期过内存"作为**已知残余**登记（见 §4）。

### 3-3 发现 3（nits）→ 已删除死代码

`StorageRoots.to_kwargs()` 全仓零调用，且注解 `dict[str, Path]` 与可能返回 `None` 不符 → 删除。

## 4. 已知残余（登记，不修）

| ID | 残余 | 判断 |
|---|---|---|
| RES-01 | YAML 解析期 `mysql` 子树短暂过内存 | 风险点是"保留/暴露"，已消除；彻底"不读"需换解析方式，收益不抵复杂度 |
| RES-02 | `snapshot_read_set` 使 `extension_root` 持续增长 | 清理策略留 L07/L08，当前不清理已提交版本 |
| RES-03 | `config/app.yaml` 明文口令且不在 `baseline_lock` 内 | 存量问题，需 Owner 决定；窄加载已切断扩展层读取路径 |

## 5. 数字

- 受审三文件：修复前 127 → `35d21fb` 后 139 → `8558349` 后 **141** passed（+2）。
- TL 所涉 9 个测试文件：**286 passed**。
- 全量 `tests/extensions` 当前**不可复现**：`dev-word` 正在实时改 `word/**`，两轮连跑分别出现 8 failed 与 23 failed，全部落在**未跟踪**的 word 测试上；受审文件在其所属测试中恒全绿。该现象本身作为证据保留——阶段内不做全量数字结论。

## 6. 结论

P0 的 `DB_REVIEW` 维持 **PASS**（第 1 轮 PASS + 第 2 轮复核修复并通过）。
第 2 轮新提的 3 项已全部处置；`RES-01`—`RES-03` 登记为已知残余。
独立复验的三项修复由 `qa-verifier` 在 P1 验收中再确认一次（避免同一审查方连续自证其建议）。
