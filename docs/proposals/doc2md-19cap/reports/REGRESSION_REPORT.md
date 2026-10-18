# T804 回归报告

- Loop：`L08` / 任务 `T804`
- 执行：TL（非独立 QA，见 `INDEPENDENT_QA_PENDING`）
- 环境：`/tmp/doc2md-l00-venv/bin/python`（`click==8.1.8`，与 L00 基线一致）

## 1. 全量回归

| 项 | 命令 | 结果 |
|---|---|---|
| 全量 | `PYTHONPATH=. pytest -q -p no:cacheprovider`（`backend/`） | **1279 passed / 0 failed / 133 skipped** |
| extensions | `pytest tests/extensions -q` | 见全量 |
| 基线 | L00 实测 482 passed / 4 failed / 133 skipped（默认解释器） | 4 failed 已定位为 `click 8.4.1` 违背 `click<8.2`，钉版本后 0 failed |

133 skip 的构成在 L00 已逐条登记：131 例 MySQL 不可用、2 例 `paddleocr` 已装致缺失分支不可达。
本轮**未变**：仍为 `NOT_EXECUTED`，不因全量通过而记作 PASS。

## 2. 旧 UID 公式不变（固定向量）

```text
backend/app/services/evidence_chain.py::_evidence_uid
expected: c5caa6a6f06755e79ffd6d7c0c84d4b700056434bd07a4dd5d9cdb15c2df6dc5
actual  : c5caa6a6f06755e79ffd6d7c0c84d4b700056434bd07a4dd5d9cdb15c2df6dc5
MATCH   : True
```

## 3. 原件 / parsed / 受保护文件

`check_baseline.py --repo <worktree> --allow-index-append` → `passed=true`，`errors=[]`。
322 个受保护文件内容哈希与基线锁一致；`raw_vault/`、`parsed/`、`sql/` 无写入。

## 4. 合同负例变异（确认测试不是摆设）

在副本 `/tmp/mut-l08` 上注入：把 `FAILED` 从对账分母中剔除（模拟"让数字好看"）：

```text
FAILED tests/extensions/test_switch_recovery.py::test_denominator_is_the_full_source_list_even_with_failures
1 failed, 23 passed
```

定向测试命中，说明"失败必须留在分母"不是靠注释保证的。副本已删除，真仓未受影响。

## 5. 未做（如实）

| 项 | 状态 |
|---|---|
| 银行真实源/consumer 联调 | `NOT_CONNECTED`（无接口） |
| 真实切换/回退验收 | `NOT_RUN`（无路由控制与 ACK） |
| 故障注入（进程被杀/断电/磁盘满） | `NOT_RUN`（无注入环境） |
| cold/warm 性能与资源边界 | `NOT_RUN` |
| 独立 QA | `PENDING` |
