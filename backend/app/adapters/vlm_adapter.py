"""VLM 适配器（015 plan §8.2 D-2 / O-3）。

015 **只交付 Protocol + Mock**：

- `VlmClient`：唯一契约（`recognize`），真实 HTTP/SDK/密钥实现另开 Spec；
- `MockVlmClient`：`mock=True`、`text=""`、`engine_meta={"note": "mock adapter; no real recognition"}`；
- `build_vlm_client(config)`：未知 `adapter` 值一律 `ValueError`（禁止静默降级成 mock）。

`OcrResult` 放在本模块，作为**引擎结果的公共类型**（`ocr_paddle` 也复用它），避免重复定义。

**D-2 诚实性红线**：`mock=True` 的结果由 `app.services.image_parser` 显式拒绝落盘；
本模块只负责如实标记，不负责拦截。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

ENGINE_NAME = "vlm"
ADAPTER_MOCK = "mock"
SUPPORTED_VLM_ADAPTERS: tuple[str, ...] = (ADAPTER_MOCK,)

MOCK_NOTE = "mock adapter; no real recognition"


@dataclass(frozen=True)
class OcrResult:
    """引擎识别结果（paddleocr / vlm 共用）。

    `mock=True` 表示结果不是真实识别产物 → service 层必须拒绝落盘。
    `confidence_*` 为 `None` 表示引擎未给出真实置信度 → 元数据写 `null`，不得编造。
    """

    text: str
    confidence_mean: float | None = None
    confidence_min: float | None = None
    sample_count: int = 0
    engine: str = ENGINE_NAME
    mock: bool = False
    engine_meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VlmClient(Protocol):
    """VLM 客户端契约。`is_mock` 供路由层判断「是否具备真实识别能力」。"""

    is_mock: bool

    def recognize(self, image_path: Path, *, timeout_seconds: int) -> OcrResult:  # pragma: no cover
        ...


class MockVlmClient:
    """015 唯一的 VLM 实现：**永远不产生真实识别结果**。"""

    is_mock = True
    engine_meta: dict[str, Any] = {"note": MOCK_NOTE}

    def __init__(self, **_ignored: Any) -> None:
        pass

    def recognize(self, image_path: Path, *, timeout_seconds: int = 0) -> OcrResult:
        return OcrResult(
            text="",
            confidence_mean=None,
            confidence_min=None,
            sample_count=0,
            engine=ENGINE_NAME,
            mock=True,
            engine_meta=dict(self.engine_meta),
        )


def build_vlm_client(vlm_config: Mapping[str, Any] | None = None) -> VlmClient:
    """依配置构造 VLM 客户端；未知 adapter 抛 `ValueError`（不静默降级）。"""

    config = dict(vlm_config or {})
    adapter = str(config.get("adapter", ADAPTER_MOCK)).strip().lower()
    if adapter not in SUPPORTED_VLM_ADAPTERS:
        raise ValueError(
            f"不支持的 VLM adapter: {adapter!r}；"
            f"015 仅交付 {', '.join(SUPPORTED_VLM_ADAPTERS)}（真实 VLM 另开 Spec）"
        )
    return MockVlmClient()


def client_is_mock(client: Any) -> bool:
    """判断客户端是否为 015 的 mock 适配器。

    判定口径 = plan §6 真值表里的 `vlm adapter = mock|real`，即「配置/构建出来的适配器
    类型」，因此只认两种情况：

    - `None` / 无 `recognize` → 不可信（fail-safe，等价于无可用引擎）；
    - `MockVlmClient` → mock（真值表 `vlm|mock`、`auto|*|mock` 行）；
    - 其余带 `recognize` 的客户端 → 视为 real。

    **刻意不读客户端上的 `is_mock` 运行时标记**：该标记描述的是「结果可能是伪造的」，
    而结果级诚实性由 service 层对 `OcrResult.mock` 的拒绝分支（D-2 红线）兜底。若在
    路由层据它短路，会连引擎调用一起跳过，从而无法证明 A015-06 要求的
    「引擎确被调用过、且仍零写入」。
    """

    if client is None:
        return True
    if isinstance(client, MockVlmClient):
        return True
    return not callable(getattr(client, "recognize", None))
