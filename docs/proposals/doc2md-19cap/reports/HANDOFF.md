# 交付交接（L08 / T806）

## 1. 交付物位置

| 内容 | 路径 |
|---|---|
| 原始契约包（只读快照） | `docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017/` |
| 采用后工作副本（唯一可编辑） | `docs/proposals/doc2md-19cap/v1.0.0/` |
| Spec 五件套 | `specs/proposals/doc2md-19cap-v1.0.0/` |
| 字段/接口/身份契约 | `contracts/doc2md-19cap/v1.0.0/` |
| 机器契约与追溯 | `loop/proposals/doc2md-19cap/v1.0.0/` |
| 各 Loop 证据 | `docs/proposals/doc2md-19cap/v1.0.0/evidence/L00…L07/` |
| 终局报告 | `docs/proposals/doc2md-19cap/reports/` |
| 新增产品代码 | `backend/app/extensions/**` |
| 新增测试 | `backend/tests/extensions/**` |

## 2. 运行方式

```bash
# 全量（与 L00 基线同环境）
cd backend
PYTHONPATH=. /tmp/doc2md-l00-venv/bin/python -m pytest -q -p no:cacheprovider

# Loop 范围门禁（每个 Loop 一个 base commit）
PY="/home/szf/anaconda3/bin/python"
PACK="docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017"
"$PY" "$PACK/tools/check_loop_diff.py" --repo . --loop L08 --base-commit <40位commit>

# 受保护文件
"$PY" "$PACK/tools/check_baseline.py" --repo . --allow-index-append
```

## 3. 接手人必须知道的三件事

1. **旧服务只读**：`backend/app/services/**`、`sql/**`、`parsed/`、`raw_vault/` 未被本工作流写入；
   新增层通过 `io/read_bridge.py` 只读接入。任何回写都属违约。
2. **`LOCAL_READY ≠ ACTIVE`**：`release/switch.py` 在缺任一必需 consumer 的真实 ACK 时抛
   `E_CONSUMER_NOT_CONNECTED`。不要为了"跑通流程"改成模拟 ACK——那会直接制造假绿。
3. **三个上游生产者缺口**（`L05-GAP-01/02/03`）：表头带、`page_index`/列宽、脚注文本目前无人产出。
   要先补生产者，L05 才能端到端；补的时候属于 L03/L04 的范围，需走 ADR。

## 4. 已知缺口清单（按 Loop）

| Loop | 缺口 | 处置建议 |
|---|---|---|
| L02 | MB3–MB5 变异未复跑；MySQL 131 例未执行 | 有 DB 环境后补跑 |
| L04 | `lock` 未解析；Paddle 字段未对 golden；阈值未校准；T405 `NOT_RUN` | 真实环境就位后按 T405 流程补 |
| L05 | 三个上游生产者缺失；真实 Word 语料未跑 | 先补生产者 |
| L06 | 图文一致性仅词面；Chart2Table 字段未验；T606 `NOT_RUN` | 语义比对需单独立项 |
| L07 | outbox 无真实 MQ；影响集未驱动真实重算 | 重算执行器属后续 |
| L08 | 银行接口未接；故障注入与性能 `NOT_RUN` | 见 `BANK_JOINT_REPORT` |

## 5. 未声明事项（不得由本文件推得）

- 客户签署、需求冻结、独立 QA 通过、银行验收；
- `specs/017-unified-document-ir` 完成（其状态与裁定保持不变）；
- 任何性能/容量/SLA 结论；
- 正式 spec 编号 018（未占用）。

## 6. 回滚方式

代码层：每个 Loop 一个分支（`loop/L04`…`loop/L08`）与 `loop-entry/L0x` tag，
可按 tag 定位到任一 Loop 的入口状态；文档层为纯追加，`SPEC_INDEX.md` 仅在末尾追加过一节。
