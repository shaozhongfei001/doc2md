"""magic-pdf 兼容入口：修补 unimernet 与 transformers ≥4.5x 的 `cache_position` 不兼容。

背景（实测）：
    TypeError: UnimerMBartForCausalLM.forward() got an unexpected keyword argument 'cache_position'

`transformers 4.57` 的 `GenerationMixin` 在新版 generate 流程里会向 model.forward 传
`cache_position`，而 `magic_pdf` 内置的 unimernet 模型（`modeling_unimer_mbart.py`）仍是旧签名。
该参数对公式识别（自回归解码）无实际语义影响，故在入口处剥离即可。

用法（作为 magic_pdf_cmd 使用）：
    python /path/to/magic_pdf_patched.py -p <pdf> -o <out> -m txt
"""

from __future__ import annotations

import runpy

from magic_pdf.model.sub_modules.mfr.unimernet.unimernet_hf.unimer_mbart.modeling_unimer_mbart import (
    UnimerMBartForCausalLM,
)

_ORIGINAL_FORWARD = UnimerMBartForCausalLM.forward


def _forward_without_cache_position(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    # 第一处：transformers ≥4.5x 会传 cache_position，旧模型签名不接受 → 剥离
    kwargs.pop("cache_position", None)
    # 第二处：transformers ≥4.5x 传 EncoderDecoderCache 对象，而该模型按旧元组协议
    # 访问 past_key_values[0][0].shape → 置空并关闭缓存（公式解码序列短，重算代价可接受）
    kwargs["past_key_values"] = None
    kwargs["use_cache"] = False
    return _ORIGINAL_FORWARD(self, *args, **kwargs)


UnimerMBartForCausalLM.forward = _forward_without_cache_position

if __name__ == "__main__":
    runpy.run_module("magic_pdf.tools.cli", run_name="__main__")
