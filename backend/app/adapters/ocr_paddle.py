"""PaddleOCR 引擎适配器（015 plan §5.1 / §6 真值表）。

**依赖缺失是显式失败，不是降级**：`check_paddleocr_available()` 不满足即抛
`EngineDependencyMissing`，由 service 映射为 `DEPENDENCY_MISSING`（非 dry-run 时 CLI 退出码 1）。
绝不允许「paddleocr 缺失 → 悄悄改用 mock VLM 并落盘」。

本机未安装 paddleocr，故 `recognize()` 的真实解析分支不在 015 环境覆盖范围内
（plan §6 / O-3：真实引擎集成为独立 Spec）；测试通过注入假客户端覆盖上层逻辑。
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from app.adapters.vlm_adapter import OcrResult

logger = logging.getLogger(__name__)

ENGINE_NAME = "paddleocr"
PACKAGE_NAME = "paddleocr"
INSTALL_HINT = "pip install paddleocr paddlepaddle"


class EngineDependencyMissing(RuntimeError):
    """引擎依赖缺失。message 前缀固定为 `DEPENDENCY_MISSING`，供 CLI / 报告分类。"""


def import_paddleocr() -> Any | None:
    """返回 paddleocr 模块；不可导入返回 `None`（测试可 monkeypatch 本函数）。"""

    try:
        return importlib.import_module(PACKAGE_NAME)
    except Exception:  # noqa: BLE001 - 缺包 / 二进制不兼容均视为不可用
        return None


def check_paddleocr_available() -> None:
    """依赖存在则返回 `None`，否则抛 `EngineDependencyMissing`。"""

    if import_paddleocr() is None:
        raise EngineDependencyMissing(
            f"DEPENDENCY_MISSING: {PACKAGE_NAME} 不可导入（未安装或依赖不兼容）；"
            f"请执行 `{INSTALL_HINT}`，或改用 `--engine vlm`（015 默认 mock）。"
        )


class PaddleOcrClient:
    """真实 PaddleOCR 客户端。`is_mock = False`（具备真实识别能力）。"""

    is_mock = False
    engine = ENGINE_NAME

    def __init__(self, *, lang: str = "ch", use_gpu: bool = False) -> None:
        self.lang = lang
        self.use_gpu = use_gpu
        self._engine: Any | None = None

    def _ensure_engine(self) -> Any:
        check_paddleocr_available()
        if self._engine is None:
            module = import_paddleocr()
            assert module is not None  # check_paddleocr_available 已保证
            self._engine = module.PaddleOCR(lang=self.lang, use_gpu=self.use_gpu)
        return self._engine

    def recognize(self, image_path: Path, *, timeout_seconds: int = 0) -> OcrResult:
        engine = self._ensure_engine()
        raw = engine.ocr(str(image_path))
        return self._to_result(raw)

    def _to_result(self, raw: Any) -> OcrResult:
        """把 paddleocr 的嵌套返回结构拍平成 `OcrResult`（容忍 2.x/3.x 结构差异）。"""

        texts: list[str] = []
        confidences: list[float] = []
        for page in raw or []:
            for line in page or []:
                if not line:
                    continue
                payload = line[1] if len(line) > 1 else line[0]
                if isinstance(payload, (list, tuple)) and len(payload) == 2:
                    text, score = payload[0], payload[1]
                else:  # pragma: no cover - 结构异常时按纯文本处理
                    text, score = str(payload), None
                if text:
                    texts.append(str(text))
                if isinstance(score, (int, float)):
                    confidences.append(float(score))
        mean = sum(confidences) / len(confidences) if confidences else None
        minimum = min(confidences) if confidences else None
        return OcrResult(
            text="\n".join(texts),
            confidence_mean=mean,
            confidence_min=minimum,
            sample_count=len(confidences),
            engine=ENGINE_NAME,
            mock=False,
            engine_meta={
                "lang": self.lang,
                "use_gpu": self.use_gpu,
                "sample_count": len(confidences),
            },
        )


__all__ = [
    "ENGINE_NAME",
    "EngineDependencyMissing",
    "PaddleOcrClient",
    "check_paddleocr_available",
    "import_paddleocr",
]
