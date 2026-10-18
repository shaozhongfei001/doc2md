# OI03 部分关闭：PaddleOCR 本机 GPU 部署实测

- 日期：2026-10-18
- 决策依据：Owner「Paddle 模型在本机部署，不替换为其他模型」
- **状态：部署成功 + 两项能力实测通过；阈值校准仍未完成（缺语料，见第 6 节）**

## 1. 为什么不是 Ollama

Ollama 只能跑 GGUF 的 LLM/VLM。PaddleOCR 是 PaddlePaddle 框架的检测/识别模型，**没有 GGUF 形态**，无法由 Ollama 部署。

两者能力也不同，不能互相替代：

| | PaddleOCR | VLM（gemma4 vision） |
|---|---|---|
| 输出 | 文本 + **字符级坐标** + 逐行分数 | 自然语言 / JSON |
| 适合 | 版面、表格网格、坐标对齐 | 图像语义分类 |
| 本项目用途 | `pipeline` 的表格与文本层 | `vision/classify` 的图像类型判断 |

Owner 已卸载 Ollama，本机不再有 VLM 通道；`vision/classify` 的输入仍由上游提供，不影响既有 1427 用例。

## 2. 环境与版本（实测）

| 项 | 值 |
|---|---|
| Python | 3.11.7（独立 venv `/tmp/paddle-gpu-venv`） |
| paddlepaddle-gpu | **3.3.1**（`paddlepaddle_gpu-3.3.1-cp311-cp311-linux_x86_64.whl`） |
| paddleocr | **3.7.0** |
| paddlex | **3.7.2**（含 `[ocr]` extras） |
| 驱动 / 运行时 | Driver 555.42.02（CUDA 12.5），Paddle 运行时 CUDA 11.8 |
| GPU | RTX 3060 Laptop 6 GB，算力 8.6 |
| 模型体积 | **1.9 GB**（`/home/szf/.paddlex/official_models`） |

**为什么用 cu118 wheel**：驱动 12.5 向下兼容 CUDA 11.8；cu126 wheel 需要更新的驱动，风险更大。

安装命令（可复现）：

```bash
python3.11 -m venv /tmp/paddle-gpu-venv
/tmp/paddle-gpu-venv/bin/pip install paddlepaddle-gpu==3.3.1 \
  -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
/tmp/paddle-gpu-venv/bin/pip install "paddleocr==3.7.0"
/tmp/paddle-gpu-venv/bin/pip install "paddlex[ocr]==3.7.2"   # PP-StructureV3 必需
```

**必须独立 venv**：paddle 会钉 `numpy`/`protobuf`/`opencv` 版本。装进测试 venv 会把
**1427 passed / 131 skipped** 变成另一个数字，此前所有"已验"结论就失去可对齐性。

自检：

```text
paddle 3.3.1 | is_compiled_with_cuda: True
PaddlePaddle works well on 1 GPU.
device: gpu:0
```

## 3. 能力一：文本 OCR（PP-OCRv5_server）

输入：1000×640 生成表格图（5×4，中文 + 数字 + 百分比），文件 `/tmp/oi03/table_cjk.png`。

| 指标 | 实测 |
|---|---|
| 模型加载 | 1.5 s |
| 推理 | **0.31 s / 页** |
| 识别行数 | 20 |
| 完全正确 | **20 / 20** |
| 分数分布 | 19 行 ≥0.9966，1 行为 1.0000（最低 0.9966） |

输出字段：`rec_texts` / `rec_scores` / `rec_polys` / `rec_boxes`——**逐行分数与坐标齐全**，可直接喂给现有 `engine_confidence` 与坐标对齐。

## 4. 能力二：表格结构（PP-StructureV3）

| 指标 | 实测 |
|---|---|
| 加载 | 81.4 s（含 6 个模型，一次性） |
| 推理 | **5.09 s / 页** |
| 检出表格 | 1 |
| `table_res_list[0]` 键 | `cell_box_list` / `pred_html` / `table_ocr_pred` |
| **单元格数** | **20**（5×4，与图一致） |
| 单元格几何 | `[x1,y1,x2,y2]`，例 `[41.0,40.7,270.3,136.0]` |
| HTML | 正确还原表头行与 5 行数据 |

**这是关键结论**：它给的不只是整表 HTML，**每个单元格都有独立几何框**。因此可以反推
`row/col`（按 y/x 聚类），而不是只能拿 HTML 猜结构。

## 5. 它证明了什么、没证明什么

| 已证 | 未证 |
|---|---|
| 本机 GPU 能跑 PaddleOCR 3.7.0 | 真实银行扫描件的精度（当前只有生成图） |
| 文本识别 20/20、0.31 s/页 | 低质量扫描/倾斜/印章遮挡下的鲁棒性 |
| 表格给出 20 个 cell 几何 | **rowspan/colspan 的还原规则**（HTML 有属性，几何需推导） |
| 逐行 score 可用 | 阈值 `0.6` 是否合理（需要分数分布，见第 6 节） |

## 6. 仍未关闭

| 编号 | 内容 | 需要什么 |
|---|---|---|
| `OI03`（余项） | 真实 VLM/OCR 质量与容量验收 | 真实语料（`OI04`） |
| `L32-LIM-01` | 阈值 `0.6` 未校准 | 一批真实页面的 `rec_scores` 分布 + 人工判定 |
| — | 表格几何 → `row/col/rowspan/colspan` 的推导 | 新 Loop（纯函数 + 录制夹具） |
| — | `paddleocr` 与测试 venv 隔离下的适配层 | 子进程/HTTP，不 `import paddle` 进主 venv |

**"装好了" ≠ "能用了"**：本文件只证明模型在本机跑得动、两个 API 输出可得。
把它接进 `pipeline` 还需要一个适配层，把 `cell_box_list` 变成契约里的
`cells[{cell_id,row,col,rowspan,colspan}]`，并用 fail-closed 拒绝推不出的结构。
