# L10 验收记录（T1001–T1004，端到端组装）

- 工作流：`DOC2MD-19CAP` / 修订 `DOC2MD-19CAP-V1.0.1-ADDENDUM-L10`
- 入口：`loop-entry/L10` = `20f8f39`
- 代码：`5c12dbb`（组装 + CLI）、`2394683`（空 packet 修复）
- 合并：`--ff-only` → `main` = `2394683`
- **状态：`CODE_MERGED` + `INDEPENDENT_QA_PENDING`**

## 1. 为什么开这个 Loop

`grep extract_tables|derive_header_paths|build_review_packet app/extensions/runner.py app/extensions/cli.py`
在 L10 之前**零命中**：九个 Loop 的模块从未被任何入口调用，扩展层没有处理过一份文档。
L10 是第一条真实链路。

## 2. 门禁

| 门 | 结果 |
|---|---|
| A1 | 官方工具不支持（`unknown Loop`）→ 等价自检：区间**恰好 3 个文件**（`pipeline.py`、`cli.py`、`test_pipeline_e2e.py`），全部在"新增 + 定点编辑"白名单内，无未跟踪文件 |
| B | **1319 passed / 133 skipped**（L09 后 1306，+13） |
| C | `check_baseline --allow-index-append` → `passed=true`，`errors=[]` |
| E2E（新增门） | 真实 docx 走通并落盘；`--dry-run` 零写入 |

`cli.py` 属定点编辑：只新增 `assemble` 子命令与一个分支，既有子命令语义与退出码未变
（全量用例含既有 CLI 测试，全部通过）。

## 3. 真实文档实测（非合成）

用仓内真实文件 `data/sample_dataset/office_like/最小文档.docx`：

```text
source_sha256 = b4fbe3cf92797699e508b655e25b48d0b1c8bd2acb39b57790a16ef68f612921
dry-run  → exit 0，executed=false，ext 目录零文件
真提交   → objects/by_hash/b4/fb/<sha>/DEMO-REAL-1/{manifest.json, report.json, review_packet.md}
```

该样例**不含表格**（`tables=0`），所以链路虽然走通但没有产出表头路径——这是**如实结果**，
不是失败；它同时暴露了下面这个真实缺陷。

## 4. 真实运行抓到的缺陷（已修）

首跑产出的 `review_packet.md` 的 sha 为 `e3b0c442…`，即**空字符串的 sha256**。
「空文件」和「本文档没有表格」是两种陈述，只有一种为真。

修复（`2394683`）：无表格时输出明确说明 + 源摘要 + 生成号，并新增用例钉住。

## 5. 一个被实测纠正的假设

我原以为"同一 `generation_id` + 不同内容"应报 `E_VERSION_CONFLICT`，测试证明**不会**：
`ArtifactStore` 以 `(content_sha256, generation_id)` 定位目录，不同内容落在不同目录。
所以我改的是**测试**（断言旧代未被改写、两份并存），不是代码——**没有让测试去迁就实现，
也没有让实现去迁就我的假设**。

## 6. 新增限制（如实登记）

| 编号 | 限制 | 影响 |
|---|---|---|
| `L10-LIM-01` | 产物不是 `DocumentEnhancement`：`missing_contract_fields` 显式列出缺 geometries / evidence locator / visuals / continuations / review decision | 不能宣称"扩展产物已完整符合根契约" |
| `L10-LIM-02` | 只走 Word 原生表路径；扫描件、图像、流程图、统计图都不在此链路上 | 这些能力仍停在各自 Loop 的函数层 |
| `L10-LIM-03` | `evidence_candidates` 只带 id，不带 locator | 复核包可读，但证据不可回指到原文片段 |

## 7. 结论

L10 达成目标：**一份真实 docx 能走完整条链并原子落盘，dry-run 零写入**。
这是本项目第一次把 L01–L09 串起来，也第一次让"真实运行"发现问题（空 packet），
而这个问题在合成用例里不会出现——因为合成用例总是带表格。

下一步（L11 重算执行器）的前置条件"一条真实链路跑通"**已满足**。
