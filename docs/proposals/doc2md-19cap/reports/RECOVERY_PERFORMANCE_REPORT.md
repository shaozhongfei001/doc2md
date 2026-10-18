# T805 故障注入与性能报告

- 结论：**逻辑已测，真实故障注入与性能测量 `NOT_RUN`**。

## 1. 已测（逻辑层）

| 场景 | 行为 | 证据 |
|---|---|---|
| 提交中断留下 staging | `plan_recovery` 只列本根 staging 为可删，committed 保留 | `test_switch_recovery.py` |
| staging 路径越出根 | 拒绝处理并记 `STAGING_OUTSIDE_ROOT`，不删不动 | 同上 |
| 提交产物与 manifest 摘要不符 | `E_EVIDENCE_MISMATCH`，**不回退旧缓存** | 同上 |
| manifest 无摘要字段 | 直接拒绝（缺摘要不等于可用旧版） | 同上 |
| 单写者锁被占 | `E_LOCK_BUSY`（L04 worker） | `test_worker_limits.py` |
| 页面/请求/时间预算超限 | 拒绝继续，不静默缩量 | 同上 |
| ROI 第二轮 | 构造上禁止（`ValueError`） | `test_vision_roi.py` |

## 2. 未做（如实，`NOT_RUN`）

| 项 | 原因 | 影响 |
|---|---|---|
| 进程被杀 / 断电 / 磁盘满 | 无注入环境 | 只证明"代码路径会拒绝"，未证明"真实中断后数据可恢复" |
| 备份/恢复演练 | 无备份目标 | 未验证 |
| cold / warm 时延 | 无真实引擎与语料 | **不得**引用任何性能数字 |
| 资源边界（内存/GPU/句柄） | 无 GPU、无大样本 | 未验证 |
| 并发切换压测 | 无路由接口 | 未验证 |

## 3. 明确禁止的表述

本包**不提供**任何"性能达标""可支撑 X 万页""切换秒级"类结论。SDD 14 的 `page=100 / worker=1 / CPU=2 / retry=2 / ROI=1 / 180s / 600s` 只是**初始配置建议**，
不是银行 SLA，也不是容量承诺。
