# L18 验收记录（L09/L10 三处未执行守卫）

- 入口：`loop-entry/L18` = `776d666`
- 代码：`588002d`（首版补断言）+ `201a925`（修复哑弹、红点前移）
- **状态：`CODE_MERGED` + `3/3 缺口 CLOSED` + 独立 QA 五条复验全过**

## 1. 来源：第二轮独立抽检（L09 抽取 / L10 装配）

`qa-l18` 注入 8 个变异，**11 个中被捕获 5 个，3 个 NOT_CAPTURED**（另一个为等价变异口径差异），且全量 1368 用例均无感：

| 变异 | 破坏的保证 | 为什么全绿 |
|---|---|---|
| MB4 未解析脚注 `evidence_ids=()` | 脚注溯源 | 旧断言只看 `state=NEEDS_REVIEW`，而 `scope=UNRESOLVED` **本来就**是 `NEEDS_REVIEW`，两条件恒真 |
| MB7 `expected_files={}` | 写盘自校验 | `write_assembly` 刚写完立刻提交，staging 永远完整，核对分支恒真通过 |
| MB8 证据去重失效 | 候选证据唯一性 | 所有 fixture 每格最多一个脚注引用，重复从未出现 |

## 2. 补的三条断言（只改测试，产品代码零改动）

| 缺口 | 用例 | 关键点 |
|---|---|---|
| MB4 | `test_an_unresolved_note_carries_its_own_evidence` | 断言 `evidence_ids == ['docx:footnote:7']`，**并加控制组**：剥掉证据的候选必须报 `MISSING_EVIDENCE` |
| MB7 | `test_a_generation_missing_a_declared_file_is_refused` | monkeypatch `write_staged` 写完即删 `report.json`，断言 `E_COMMIT_INCOMPLETE` 且无 generation 产出 |
| MB8 | `test_two_references_in_one_cell_do_not_duplicate_the_cell_evidence` | 唯一性断言**前移到 `assemble_package` 之前**，直接断言 `_evidence_candidates` |

## 3. 证据三坐标（独立 QA 实测，非作者自述）

| 变异（原文 → 改后） | 变红用例 | 失败位置与表达式 | 还原 |
|---|---|---|---|
| `notes_extract.py` `evidence_ids=(f"docx:{kind.lower()}:{ref_id}",),` → `evidence_ids=(),` | `test_an_unresolved_note_carries_its_own_evidence` | `test_producer_notes.py:233` `assert [] == ['docx:footnote:7']` | md5 → `c2eac8ba`，12 passed |
| `pipeline.py` `expected_files=expected,` → `expected_files={},` | `test_a_generation_missing_a_declared_file_is_refused` | `test_pipeline_e2e.py:377` `Failed: DID NOT RAISE <ExtensionError>` | md5 → `e390652f`，18 passed |
| `pipeline.py` `if evidence_id not in ids:` → `if True:`（两处） | `test_two_references_in_one_cell_do_not_duplicate_the_cell_evidence` | `test_pipeline_e2e.py:425` `assert 2 == 1`，`got [dup, dup]` | md5 → `e390652f`，18 passed |

## 4. 控制组活性（QA 加严了一档验证）

MB4 那条"不得出现 MISSING_EVIDENCE"是**否定式断言**，天然可能是空的：检测坏了它也照样绿。因此加了控制组，并由 QA 用两种方式证明它是活的：

| 施加的破坏 | 结果 |
|---|---|
| `notes.py` 的 `code="MISSING_EVIDENCE"` 改名 | `:247` 红：`assert 'MISSING_EVIDENCE' in ['MISSING_EVIDENCE_RENAMED']` |
| **关掉检测分支本身**（`if not evidence:` → `if False:`） | `:247` 红：`assert 'MISSING_EVIDENCE' in []` |

QA 的评价值得记下来：**改名只证明断言耦合了字面常量，关分支才证明它耦合了检测行为。**

## 5. 修掉的一个哑弹（QA 在复验中抓到的，属于测试自身）

首版写的是：

```python
codes = [issue.get("code") for issue in bound.issues]   # NoteIssue 不是 dict
```

`NotesResult.issues` 是 `NoteIssue` 对象列表，`issues` 在基线下为空，空迭代不触发属性访问——
于是它**一直绿着，看起来在守 MISSING_EVIDENCE，实际永远不会给出干净的断言失败**；一旦被触达就是 `AttributeError`。

这与 L13（`table_index==0` 恒真）、L16（`residual` 恒空）属于**同一类缺陷，只是这次出现在测试里**。
已改为 `issue.code` 并加控制组（`201a925`）。

## 6. 把"红在调用行"改成"红在断言行"

MB8 首版在变异时红在 `assemble_package(...)` 调用行（packet 层先拒绝），断言没跑到。
QA 判它仍属"钉住"（异常是产品主动拒绝，非崩溃），但认可形态不理想。
已把唯一性断言前移到 assemble 之前，现在红在 `:425`，**与它主张的那句话严格同一行**。

## 7. 提交范围（按 QA 的措辞要求）

> `588002d` / `201a925` **单个提交**只动两个测试文件；
> 区间 `aa9f5dd..201a925` 内另有一份契约 JSON（`776d666`）；
> `git diff aa9f5dd 201a925 -- backend/app` 输出为 **0 字节**。

所以准确说法是"**产品代码零改动**"，不能写成"区间只改了测试"。

## 8. 代价（QA 提示、我认可）

新用例 import 了私有函数 `_evidence_candidates`。代价是：该函数改名/改返回结构时这条用例会先红。
但这种红是**有意义的**（提示"你动了被断言的结构"），不是噪音，故不处理。

## 9. 覆盖边界

| 范围 | 独立抽检 |
|---|---|
| L09 / L10 / L11 / L13 / L16 | ✅ 已抽检（两轮共 20 变异） |
| L05 / L06 / L07 / L08 / L12 / L14 / L15 | ❌ **仍未做** |

本文件支持的说法：**"L09/L10 的三处守卫经独立变异抽检确认可被观测到破坏。"**
不外推为"全部 Loop 已验证"。

## 10. 结论

三处缺口 CLOSED。更重要的是，本轮再次印证了那条规律：

> 作者写的测试普遍只覆盖"他想得到的那条路径"；
> 缺陷住在 **`False` 分支、空集合、非首例**里——这三者恰好是自测最难覆盖的地方。
