# ADR-19CAP-01 · 范围门禁的解释口径与 QA 目标冻结

- 状态：**ACCEPTED**（TL 决定，记录于 P1 验收期）
- 触发：`qa-verifier` 的 P1 独立验收给出 `QA_VERDICT: RETURN`，唯一阻断项为 A5 门禁在**当前 HEAD** 为红
- 影响：不改变任何产品代码、不改变机器合同的任何字段与白名单

## 1. 问题陈述

`check_loop_diff.py` 在 `base..HEAD` 区间内取样（`git diff <base>` + `git ls-files --others`），
把 L03 的 TL 控制文档 `docs/proposals/doc2md-19cap/v1.0.0/evidence/L03/TL_ACCEPTANCE.md` 判为
`OUTSIDE_LOOP_WHITELIST`（L03 白名单只含 `word/**`、`tables/grid.py`、`test_word_*.py`、`test_table_grid.py`）。

同一提交 `07da949` 在 clean 树上跑该工具是 **passed=true / 7 files / errors=[]**，
所以红的原因是**区间里混入了代码提交之后的文档提交**，不是 DEV 越界。

## 2. 为什么不能按 QA 建议 (a) 改白名单

QA 建议在机器合同里给 L03（以及 L01/L02）加 `docs/proposals/doc2md-19cap/v1.0.0/evidence/<loop>/**`。**不可行**，原因有二：

1. **工具读的是 `PACK` 内的合同，不是工作副本。**
   `tools/check_loop_diff.py:11-12`：
   ```python
   package = Path(__file__).resolve().parents[1]        # -> PACK
   con = json.loads((package/'loop/loop_contract.json').read_text())
   ```
   所以改工作副本 `loop/proposals/doc2md-19cap/v1.0.0/loop_contract.json` **对判定没有任何影响**。
2. **改 `PACK` 会破坏来源快照。**
   `PACK` 是只读来源快照，其完整性由 `PACK/MANIFEST.json`（70 条逐文件 sha256）与 `check_delivery.py` 证明。
   在 `PACK` 里改一行合同，`manifest-integrity` 立刻失败，整个采用的溯源性失效。

结论：**不为让门禁变绿而改白名单或改快照**——那正是"为消除误报而扩大原代码白名单"被禁止的做法，
只是换了个发生位置。

## 3. 决定

Per-loop 范围门禁改为**两个显式断言**，不再依赖单次工具调用的 `base..HEAD` 语义：

**A1（代码区间）** 该 Loop 的代码提交集合，其父区间必须完全落在该 Loop 白名单内：

```bash
# L03 实例
git diff --name-only a783dc8c1c7f762f3f7d0998b87a42c34418b9e8 07da949
```
实测输出（7 个文件，全部属 L03 白名单）：
```
backend/app/extensions/word/__init__.py
backend/app/extensions/word/final_view.py
backend/app/extensions/word/native_tables.py
backend/app/extensions/word/package.py
backend/tests/extensions/test_word_final_view.py
backend/tests/extensions/test_word_package.py
backend/tests/extensions/test_word_tables.py
```
同一区间在 clean 树上跑 `check_loop_diff --loop L03 --base a783dc8` 亦为 `passed=true`（QA 已独立复现）。

**A2（尾部归属）** 代码提交之后的每个提交必须**逐个归属**：要么是纯文档（路径全部以
`docs/proposals/doc2md-19cap/` 开头），要么是**另一个 Loop 的代码提交**且其自身 A1 断言通过。

```bash
for c in $(git rev-list --reverse <loop_code_commit>..HEAD); do
  echo "--- $c $(git log -1 --format=%s $c)"
  git diff --name-only "$c^" "$c"
done
```
实测（截至 `6e8669e`）：
```
--- e690ecb  docs  -> docs/proposals/doc2md-19cap/v1.0.0/evidence/L03/TL_ACCEPTANCE.md
--- 68a8307  docs  -> docs/proposals/doc2md-19cap/v1.0.0/evidence/L03/TL_ACCEPTANCE.md
--- 746bd88  docs  -> docs/proposals/doc2md-19cap/v1.0.0/evidence/L03/TL_ACCEPTANCE.md
--- 6e8669e  code  -> backend/app/extensions/io/config.py        ← L01 自有文件（io/**，L01 白名单）
                      docstring 修正，无行为变化；见该提交说明
```
`6e8669e` 是 L01 范围的修复（`io/**` 属 L01 白名单），**不属于 L03 的 DEV 代码区间**；
它的归属由 A1 的同类断言证明（父提交 `746bd88` → 仅 `io/config.py` 变更）。

## 4. 同时采纳的流程约束（由本轮真实事故得出）

| # | 约束 | 源自 |
|---|---|---|
| P-1 | 派发独立 QA 时必须给出 `QA_TARGET=<完整 sha>`，且**从派发起不再有任何提交**，直到 QA 回报 | 本轮 QA 开工 HEAD=07da949，审查期间被 3 次 doc 提交推走（QA 记为 MAJOR 2） |
| P-2 | 上一 Loop 的缺陷修复必须在本 Loop 打开前完成；若中途仍需修，则**重新基线**并在 ADR 记录新的 `base` | L01 的 35d21fb/8558349/a783dc8/6e8669e 都落在 L03 开工之后 |
| P-3 | 证据文档随验收走，但**不与代码同批**；提交前先跑 A1，确认代码区间是干净的 | 本轮 A5 变红的直接原因 |
| P-4 | 提交信息里的数字一律现拼（`wc -l`/`md5sum`/实跑输出），不引用他人回报的数字 | QA MINOR 5 指出 07da949 行数 534 vs 实测 588；TL 自己也在 6e8669e 初稿把 79 写成 77 |

## 5. 对 QA 的复核口径

QA 只需重渲染 A5：用 §3 的 A1 + A2 两条断言替代原来的单次工具调用，其余 A1–A4、B、C、D、E、F 结论**无需重做**（受审代码 md5 未变）。
`QA_TARGET` 取 `6e8669e6bee3d7c29fbd42e277df64272cb66a54`（当前 HEAD，代码与 07da949 的差异仅 `io/config.py` 的 docstring）。

## 6. 已知局限（如实登记）

本 ADR 是把一个"单 base、单白名单"的工具用在**多提交历史**上的解释性补救，不是工具修复。
工具本身无法表达"某段区间属于 A Loop、另一段属于 B Loop"，因此 A2 只能靠逐提交归属的人工/脚本核对。
若后续要把该门禁做成 CI 硬门禁，应**另立项**实现"按提交归属校验"，而不是继续扩大本合同的白名单。
