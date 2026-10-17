# STAGE P1 · L03 派工单（TL → dev-word / dev-grid / db-reviewer）

工作流 `DOC2MD-19CAP`；Loop `L03`（原生 Word 与表格结构）；契约 `DOC2MD-19CAP-V1.0`。

## 0. 共同约束（三个工作包都适用）

| 项目 | 值 |
|---|---|
| 仓库 | `/home/szf/dev/doc2md`，分支 `main`，L03 入口 commit `2d257e10b14c6e033fe6e798b03355465160021f` |
| 解释器 | `/tmp/doc2md-l00-venv/bin/python`（3.11.7，click 8.1.8）；`cd backend && PYTHONPATH=. <PY> -m pytest -q` |
| 已就绪依赖 | `python-docx 1.2.0`、`lxml 4.9.3`（勿新增依赖、勿改 `backend/requirements.txt`） |
| 禁改 | `backend/app/extensions/{__init__,contracts,identity,runner,cli}.py`、`io/**`、`sources/**`、旧 `app/**`、`sql/**`、`specs/**`、`config/app.yaml`；**任何已有测试文件** |
| 禁做 | **不要 `git add` / `git commit`**；不要跑 `git checkout/reset/clean`；不要写 `parsed/`、`raw_vault/`；不要联网、不要调模型 |
| 新建包 | 只允许建 `backend/app/extensions/word/__init__.py`；`backend/app/extensions/tables/` **不要**建 `__init__.py`（白名单只含 `tables/grid.py`，Python 3 命名空间包可用） |
| 错误码 | 只用 L01 已登记的码：`E_INPUT_INVALID`、`E_DOCX_MERGE_INVALID`、`E_DOCX_REVISION_UNRESOLVED`、`E_TABLE_SPAN_INVALID`、`E_TABLE_ALIGNMENT_AMBIGUOUS`、`E_EVIDENCE_MISMATCH`。**不得新增错误码**（新增需改共享 `__init__.py`，不在 L03 权限内） |
| 异常类 | 统一 `raise ExtensionError(code, message, detail=...)`，从 `app.extensions` 导入 |
| 测试纪律 | 每条否定式断言必须能被变异打红；测试用 `tmp_path` 现场构造 DOCX，禁止读仓内真实业务文件 |

---

## WP-1 · `dev-word`：T301 + T302

### 需求依据
- `01_SDD.md` §7（M04A 原生 Word 表格）、§14 错误码表
- TC：`TC-PI-F04-01-01/02/03/04`、`TC-PI-F04-04-03`（省略格需 XML 证据）
- 契约：`contracts/doc2md-19cap/v1.0.0/schemas/document-enhancement.schema.json`（`Table`/`Cell`/`DocxLocator`）

### 交付文件（仅这 4 个生产文件 + 3 个测试文件）
```
backend/app/extensions/word/__init__.py
backend/app/extensions/word/package.py        # 安全只读 OOXML
backend/app/extensions/word/final_view.py     # FINAL 视图与审阅分轨
backend/app/extensions/word/native_tables.py  # gridSpan/vMerge/gridBefore/After/嵌套
backend/tests/extensions/test_word_package.py
backend/tests/extensions/test_word_final_view.py
backend/tests/extensions/test_word_tables.py
```

### 必须实现的公开接口（TL 集成依赖，名字不可改）

```python
# word/package.py
DOCX_MAX_MEMBERS = 10_000
DOCX_MAX_MEMBER_BYTES = 100 * 1024 * 1024
DOCX_MAX_TOTAL_BYTES = 512 * 1024 * 1024
DOCX_MAX_COMPRESSION_RATIO = 200.0

class DocxPackage:
    path: Path
    member_names: tuple[str, ...]
    def read_part(self, name: str) -> bytes          # 只读、按名取
    def part_sha256(self, name: str) -> str
    def has_part(self, name: str) -> bool
    @property
    def source_sha256(self) -> str                   # 原件整文件 sha256

def open_docx(path: Path | str) -> DocxPackage
```

安全校验（缺一不可，全部 `E_INPUT_INVALID`，消息须说明原因）：
1. 非 ZIP / 非法 DOCX → 拒绝；
2. 成员名绝对路径、含 `..`、含反斜杠、含盘符 → 拒绝；
3. 成员数 > `DOCX_MAX_MEMBERS`、单成员解压后 > `DOCX_MAX_MEMBER_BYTES`、总量 > `DOCX_MAX_TOTAL_BYTES` → 拒绝；
4. 压缩比 > `DOCX_MAX_COMPRESSION_RATIO` → 拒绝（zip 炸弹）；
5. XML 部件字节中出现 `<!DOCTYPE` 或 `<!ENTITY` → 拒绝（外部实体/DTD）；
6. **不运行宏、字段、外部关系**：只读字节，不 `eval`/不解析 `w:instrText` 为代码。

```python
# word/final_view.py
@dataclass(frozen=True)
class FinalView:
    paragraphs: tuple[str, ...]      # 仅 FINAL 可见文字，按文档顺序
    table_count: int
    review_context: dict             # {"deletions": [...], "insertions": [...], "moves": [...], "comments": [...]}
    unresolved_structural: tuple[str, ...]   # 无法判定最终态的结构修订说明

def build_final_view(package: DocxPackage) -> FinalView
```
规则：`w:ins`/`w:moveTo` 文字计入 FINAL；`w:del`/`w:moveFrom` **不计入**，但原文与位置进 `review_context`；批注进 `review_context["comments"]`；结构性修订（`w:cellMerge`、`w:cellDel`、`w:trPr/w:del` 等）无法确定最终表格形状时记入 `unresolved_structural`，**不猜**。

```python
# word/native_tables.py
def extract_tables(package: DocxPackage, view: FinalView | None = None) -> list[dict]
```
- 返回**契约 `Table` 形状**的 dict 列表（键集严格：`table_id/ir_ref/parent_cell_id/n_rows/n_cols/cells/slots/omitted_slots/omission_evidence_ids/cell_semantics/notes/source_html_ref/structure_state/review_decision_ref`）。
- `cells[]` 固定键：`cell_id/row/col/rowspan/colspan/text/text_origin/text_alignment/engine_confidence/token_ids/evidence_ids/nested_table_ids`；本 Loop 一律 `text_origin="SOURCE_XML"`、`text_alignment="NATIVE"`、`engine_confidence=None`、`token_ids=[]`、`evidence_ids=[]`。
- 必须处理：`w:tblGrid` 列数、`w:gridSpan`（横向合并）、`w:vMerge`（`restart` 起、`continue` 续，续格必须连到"同列区间的上一个 anchor"，否则 `E_DOCX_MERGE_INVALID`）、`w:trPr/w:gridBefore|gridAfter`（显式省略格 → 进 `omitted_slots`）、**真空 tc 是有效空格**（`text=""`，不是省略）、嵌套 `w:tbl`（独立 `table_id`，父格 `nested_table_ids` 指回）。
- `slots[r][c]` 是 anchor cell_id 矩阵；覆盖位指回同一 anchor，**不复制文字**。
- 有 `omitted_slots` 时 `omission_evidence_ids` 不能为空（用 `["docx:gridBefore:<row>"]` 这类可复现字符串即可，但要与 `omitted_slots` 一一对应）。
- 表内文本拼接只取可见节点，`w:tab`→`\t`、`w:br`→`\n`，不展开外部链接/OLE。

### 必须写的测试（≥24 例）
- 横纵合并、组合合并、空白格、`gridBefore`、嵌套表、同名文本两列**不合并**；
- 文本修订（插入计入 / 删除排除）、批注分轨、`moveFrom/moveTo`；
- `files`：zip 炸弹（压缩比）、超成员数、`../` 成员、`<!DOCTYPE` 各 1 例；
- 错误继续格（`vMerge continue` 无 anchor）→ `E_DOCX_MERGE_INVALID`；
- **原件零变化**：读前读后 `source_sha256` 与文件 mtime/hash 不变。

---

## WP-2 · `dev-grid`：T303

### 需求依据
- `01_SDD.md` §8.3（表格矩阵）、§14；TC：`TC-PI-F04-04-01/02/03`
- 契约：`Table` 的 `slots`/`omitted_slots`/`omission_evidence_ids` 语义

### 交付文件
```
backend/app/extensions/tables/grid.py      # 不要建 tables/__init__.py
backend/tests/extensions/test_table_grid.py
```

### 必须实现的公开接口

```python
@dataclass(frozen=True)
class GridResult:
    n_rows: int
    n_cols: int
    slots: list[list[str | None]]
    omitted_slots: list[tuple[int, int]]
    anchor_cells: tuple[dict, ...]

def build_slots(cells: Sequence[Mapping], *, n_rows: int, n_cols: int) -> list[list[str | None]]
def validate_grid(
    *,
    cells: Sequence[Mapping],
    n_rows: int,
    n_cols: int,
    slots: Sequence[Sequence[str | None]],
    omitted_slots: Sequence[tuple[int, int]] = (),
    omission_evidence_ids: Sequence[str] = (),
) -> GridResult
def to_contract_table(
    table_id: str,
    cells: Sequence[Mapping],
    *,
    parent_cell_id: str | None = None,
    ir_ref: str | None = None,
    omission_evidence_ids: Sequence[str] = (),
) -> dict
```

拒绝规则（全部 `E_TABLE_SPAN_INVALID`，消息须点名具体 cell/坐标）：
1. `rowspan`/`colspan` 非正整数（0、负数、非整数）→ 拒绝，**不改成 1**；
2. anchor 越界（`row+rowspan > n_rows` 或 `col+colspan > n_cols`）；
3. 任意两 anchor 覆盖重叠；
4. `cell_id` 重复；
5. 计算出的 `None` 位置与 `omitted_slots` 不一致（未解释的洞）；
6. 有洞但 `omission_evidence_ids` 为空（洞必须有证据）；
7. 传入的 `slots` 与按 cells 重算的矩阵不等（slot-to-cell map 不符）。

`to_contract_table` 输出必须能**直接**被 `app.extensions.contracts.validate_root("document-enhancement", ...)` 接受（由测试构造最小 `Enhancement` 验证：`evidence` 含一条 TEXT_SPAN 或 PAGE_REGION，`tables[0]` 为该输出）。

### 必须写的测试（≥14 例）
- anchor 覆盖（rowspan/colspan 组合、跨多个逻辑槽），文字只存一次；
- 0/负/非整数 span、越界、重叠、重复 id、未解释洞、有洞无证据、slots 不符 —— 各 1 例；
- `gridBefore` 场景（有证据的省略）通过；扫描漏格冒充省略被拒；
- `to_contract_table` 输出经 `validate_root` 通过；把 `slots` 改错一格后 `validate_root` 必须失败（集成锚点）。

---

## WP-3 · `db-reviewer`：P0 补审（L01 + L02）

### 输入
`backend/app/extensions/**`、`backend/tests/extensions/**`、`sql/`、`docs/proposals/doc2md-19cap/v1.0.0/inputs/baseline_lock.json`。
事实基线：L01 `dfd59fd`、L02 `39eeb36`、当前 HEAD `2d257e1`。

### 判据（逐条给 PASS / RETURN + 证据行号或命令）
1. `backend/app/extensions/**` 无 `session.add/commit/delete/flush`、无 `INSERT/UPDATE/DELETE FROM`、无 `create_engine`/`from sqlalchemy`；
2. 无 `app.models` 导入；
3. 无 `sqlite3/redis/celery/pika/kafka`；
4. `sql/` 树 4 文件哈希 == `baseline_lock.json` 声明值；
5. `io/read_bridge.py` 不 import 旧写服务（`ParseRegistryService` 等在 docstring 出现属正常，只判 import 与调用）；
6. `extension_root` 解析后不与 `sql//raw_vault//parsed//curated/` 重叠或互为父子；
7. 无网络客户端（`requests/httpx/urllib.request/socket`）。

### 输出
`DB_REVIEW` 结论（PASS / RETURN + 问题清单），**不修改任何文件**。

---

## WP-4 · `qa-verifier`（P1 集成后派发，本轮不派）

独立复核：①复跑四条命令并比对数字；②对 `MUTATION_REPORT` 登记的未变异项补 ≥4 组变异；③核对 `to_contract_table` 与 `contracts.Table` 的键集一致性；④判定 `QA_PASS` 或 `QA_RETURN`（附逐条证据）。**不得修改 `backend/app/extensions/**`**。

---

## 交接与停止点

| 工作包 | 交付物 | 停止边界 |
|---|---|---|
| WP-1 | 4 生产文件 + 3 测试文件 + 自测输出 | 完成 T301/T302 即停，不做 T303/T304 之外的 Loop |
| WP-2 | 1 生产文件 + 1 测试文件 + 自测输出 | 完成 T303 即停 |
| WP-3 | DB_REVIEW 结论 | 只读，不改文件 |

回报格式（三者一致）：
1. 实际修改/新增文件清单（含行数与 md5）；
2. 实际命令 + 退出码 + 测试数字（passed/failed/skipped）；
3. 未完成项与缺口（含 `INPUT_BLOCKED` 理由）；
4. 是否触碰禁改文件（应恒为"否"）；
5. **不要提交**，等 TL 验收。
