"""图像质量评分适配器（015 plan §5.2 / §8.3 D-3）。

职责单一：给一张图打分，**不做路由决策**（阈值判定在 `app.services.image_parser`）。

- `laplacian`：cv2 `Laplacian().var()`，量纲大、对清晰度敏感；cv2 存在时优先。
- `pillow_gradient`：仅依赖 Pillow，取缩放后水平相邻像素灰度差的均值；cv2 缺失时的回退。
- 两种方法的量纲不同，故各自有独立阈值（见 `config/image_parse.yaml`）。

约定：除「未知 scorer」抛 `ValueError` 外，本模块**不抛异常**；任何评分失败都返回
`score=None` + `method="unavailable"`，由调用方 fail-safe 处理（不阻断批处理）。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCORER_AUTO = "auto"
SCORER_LAPLACIAN = "laplacian"
SCORER_PILLOW = "pillow_gradient"
SCORER_UNAVAILABLE = "unavailable"

SUPPORTED_SCORERS: tuple[str, ...] = (SCORER_AUTO, SCORER_LAPLACIAN, SCORER_PILLOW)

# Pillow 回退路径的采样边长（避免大图逐像素扫描过慢）。
PILLOW_SAMPLE_EDGE = 256


class _ScorerUnavailable(RuntimeError):
    """某条评分路径不可用（缺依赖 / 图像不可解码 / 数值退化），触发下一条路径。"""


@dataclass(frozen=True)
class ImageQualityResult:
    """评分结果。`score is None` 表示评分失败（调用方应 fail-safe 到 VLM 路径）。"""

    score: float | None
    method: str
    detail: str

    @property
    def available(self) -> bool:
        return self.score is not None


def import_cv2() -> Any | None:
    """返回 cv2 模块；不可导入时返回 `None`（测试可 monkeypatch 本函数模拟缺失）。"""

    try:
        return importlib.import_module("cv2")
    except Exception:  # noqa: BLE001 - 缺依赖/二进制不兼容都视为不可用
        return None


def _import_numpy() -> Any:
    try:
        return importlib.import_module("numpy")
    except Exception as exc:  # noqa: BLE001
        raise _ScorerUnavailable(f"numpy 不可用: {exc}") from exc


def _read_gray(image_path: Path, cv2: Any, np: Any) -> Any:
    """用 imdecode 读取灰度图，规避非 ASCII 路径问题。"""

    try:
        raw = Path(image_path).read_bytes()
    except OSError as exc:
        raise _ScorerUnavailable(f"读取失败: {exc}") from exc
    if not raw:
        raise _ScorerUnavailable("文件为空")
    buffer = np.frombuffer(raw, dtype=np.uint8)
    gray = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise _ScorerUnavailable("cv2.imdecode 返回 None（格式不支持或已损坏）")
    return gray


def _laplacian_score(image_path: Path) -> ImageQualityResult:
    cv2 = import_cv2()
    if cv2 is None:
        raise _ScorerUnavailable("opencv 不可用")
    np = _import_numpy()
    gray = _read_gray(image_path, cv2, np)
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return ImageQualityResult(
        score=variance,
        method=SCORER_LAPLACIAN,
        detail="cv2.Laplacian().var()",
    )


def _pillow_gradient_score(image_path: Path) -> ImageQualityResult:
    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        raise _ScorerUnavailable(f"Pillow 不可用: {exc}") from exc

    try:
        with Image.open(image_path) as handle:
            gray = handle.convert("L")
            gray.thumbnail((PILLOW_SAMPLE_EDGE, PILLOW_SAMPLE_EDGE))
            width, height = gray.size
            if width < 2 or height < 2:
                raise _ScorerUnavailable("图像过小，无法计算梯度")
            pixels = gray.load()
            total = 0
            count = 0
            for y in range(height):
                previous = pixels[0, y]
                for x in range(1, width):
                    current = pixels[x, y]
                    total += abs(int(current) - int(previous))
                    count += 1
                    previous = current
    except _ScorerUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 解码失败等
        raise _ScorerUnavailable(f"Pillow 解码/统计失败: {exc}") from exc

    if count == 0:  # pragma: no cover - width>=2 已保证非 0
        raise _ScorerUnavailable("无相邻像素对")
    return ImageQualityResult(
        score=total / count,
        method=SCORER_PILLOW,
        detail=f"水平相邻灰度差均值（采样 ≤{PILLOW_SAMPLE_EDGE}px）",
    )


def score_image(image_path: Path | str, scorer: str = SCORER_AUTO) -> ImageQualityResult:
    """给图像打分。

    `scorer="auto"` 时先试 Laplacian、失败再试 Pillow；两者都失败 → `unavailable`。
    """

    if scorer not in SUPPORTED_SCORERS:
        raise ValueError(
            f"未知 scorer: {scorer!r}；可选 {', '.join(SUPPORTED_SCORERS)}"
        )

    path = Path(image_path)
    if scorer == SCORER_LAPLACIAN:
        attempts = [_laplacian_score]
    elif scorer == SCORER_PILLOW:
        attempts = [_pillow_gradient_score]
    else:
        attempts = [_laplacian_score, _pillow_gradient_score]

    details: list[str] = []
    for attempt in attempts:
        try:
            return attempt(path)
        except Exception as exc:  # noqa: BLE001 - 单条路径失败不阻断
            details.append(f"{attempt.__name__}: {exc}")

    return ImageQualityResult(
        score=None,
        method=SCORER_UNAVAILABLE,
        detail="; ".join(details) or "无可用的评分路径",
    )
