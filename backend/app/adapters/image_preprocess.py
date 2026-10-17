"""图像预处理适配器（015 plan §5.3 / §8.4 D-4）。

设计约束（A015-04）：

- **任一 op 失败只记 warning 并跳过**，绝不静默声称已执行；
- 全部 op 失败 / cv2 缺失 / 源图不可解码 → 回退到原图（`output_path` 仍可用），管线继续；
- **不抛异常**（唯一例外是内部 `_apply_op` 的未知 op，也会被上层捕获转为 skipped）。

产物：`<staging_dir>/preprocess/<op>.png`，仅记录**实际执行成功**的 op。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.adapters.image_quality import import_cv2

logger = logging.getLogger(__name__)

OP_DESKEW = "deskew"
OP_DENOISE = "denoise"
OP_BINARIZE = "binarize"
SUPPORTED_OPS: tuple[str, ...] = (OP_DESKEW, OP_DENOISE, OP_BINARIZE)

SKIP_OPENCV_MISSING = "opencv_missing"
SKIP_UNREADABLE_SOURCE = "unreadable_source"
SKIP_ERROR = "error"

PREPROCESS_SUBDIR = "preprocess"


@dataclass(frozen=True)
class PreprocessOutput:
    """预处理结果。

    `output_path`：供引擎读取的图像路径（无任何 op 成功时等于原图）。
    `applied_ops`：**实际执行成功**的 op（写元数据用；不含被跳过的）。
    `skipped_ops`：`"<op>:<reason>"` 形式，供 A015-04 断言「不得静默声称已执行」。
    """

    output_path: Path
    applied_ops: tuple[str, ...] = ()
    skipped_ops: tuple[str, ...] = ()
    ops_detail: tuple[dict[str, str], ...] = ()
    asset_files: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()


def _imread(cv2: Any, image_path: Path) -> Any:
    np = _numpy()
    raw = Path(image_path).read_bytes()
    buffer = np.frombuffer(raw, dtype=np.uint8)
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def _imwrite(cv2: Any, target: Path, image: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"cv2.imencode 失败: {target.name}")
    buffer.tofile(str(target))


def _numpy() -> Any:
    import numpy as np  # noqa: PLC0415 - 延迟导入，缺 numpy 时由调用方捕获

    return np


def _op_deskew(cv2: Any, image: Any) -> Any:
    np = _numpy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    coordinates = np.column_stack(np.where(binary > 0))
    if coordinates.shape[0] < 16:
        raise RuntimeError("前景像素不足，无法估计倾斜角")
    angle = float(cv2.minAreaRect(coordinates.astype("float32"))[-1])
    if angle < -45.0:
        angle = 90.0 + angle
    if abs(angle) < 0.1:
        return image
    height, width = image.shape[:2]
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _op_denoise(cv2: Any, image: Any) -> Any:
    return cv2.fastNlMeansDenoisingColored(image, None, 10, 10, 7, 21)


def _op_binarize(cv2: Any, image: Any) -> Any:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    thresholded = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 10
    )
    return cv2.cvtColor(thresholded, cv2.COLOR_GRAY2BGR)


_OPS = {
    OP_DESKEW: _op_deskew,
    OP_DENOISE: _op_denoise,
    OP_BINARIZE: _op_binarize,
}


def _apply_op(cv2: Any, image: Any, op: str) -> Any:
    """执行单个 op。测试会 monkeypatch 本函数模拟「全部失败」。"""

    handler = _OPS.get(op)
    if handler is None:
        raise ValueError(f"未知预处理 op: {op!r}")
    return handler(cv2, image)


def _unsupported_output(
    image_path: Path,
    ops: Sequence[str],
    reason: str,
    warning: str,
) -> PreprocessOutput:
    return PreprocessOutput(
        output_path=image_path,
        applied_ops=(),
        skipped_ops=tuple(f"{op}:{reason}" for op in ops),
        ops_detail=tuple(
            {"op": op, "status": "skipped", "reason": reason} for op in ops
        ),
        asset_files=(),
        warnings=(warning,),
    )


def preprocess_image(
    image_path: Path | str,
    staging_dir: Path | str,
    ops: Iterable[str] | None,
) -> PreprocessOutput:
    """按 `ops` 顺序预处理 `image_path`，产物落 `<staging_dir>/preprocess/`。"""

    source = Path(image_path)
    staging = Path(staging_dir)
    requested = [op for op in (ops or []) if op]

    if not requested:
        return PreprocessOutput(output_path=source)

    cv2 = import_cv2()
    if cv2 is None:
        logger.warning("opencv 不可用，跳过全部预处理 op: %s", ",".join(requested))
        return _unsupported_output(
            source,
            requested,
            SKIP_OPENCV_MISSING,
            "opencv 不可用：预处理全部跳过，使用原图继续解析",
        )

    try:
        image = _imread(cv2, source)
    except Exception as exc:  # noqa: BLE001 - 读取失败一律降级
        logger.warning("源图读取失败 %s: %s", source, exc)
        image = None
    if image is None:
        return _unsupported_output(
            source,
            requested,
            SKIP_UNREADABLE_SOURCE,
            "源图不可解码：预处理全部跳过，使用原图继续解析",
        )

    out_dir = staging / PREPROCESS_SUBDIR
    applied: list[str] = []
    skipped: list[str] = []
    detail: list[dict[str, str]] = []
    assets: list[Path] = []
    warnings: list[str] = []

    current = image
    for op in requested:
        try:
            current = _apply_op(cv2, current, op)
        except Exception as exc:  # noqa: BLE001 - 单 op 失败只跳过
            skipped.append(f"{op}:{SKIP_ERROR}")
            detail.append(
                {"op": op, "status": "skipped", "reason": SKIP_ERROR, "detail": str(exc)}
            )
            warnings.append(f"预处理 {op} 失败已跳过: {exc}")
            logger.warning("预处理 op 失败已跳过 op=%s err=%s", op, exc)
            continue

        target = out_dir / f"{op}.png"
        try:
            _imwrite(cv2, target, current)
        except Exception as exc:  # noqa: BLE001 - 落盘失败等同该 op 未执行
            skipped.append(f"{op}:{SKIP_ERROR}")
            detail.append(
                {"op": op, "status": "skipped", "reason": SKIP_ERROR, "detail": str(exc)}
            )
            warnings.append(f"预处理 {op} 产物写入失败已跳过: {exc}")
            continue

        applied.append(op)
        detail.append({"op": op, "status": "applied"})
        assets.append(target)

    output_path = assets[-1] if assets else source
    return PreprocessOutput(
        output_path=output_path,
        applied_ops=tuple(applied),
        skipped_ops=tuple(skipped),
        ops_detail=tuple(detail),
        asset_files=tuple(assets),
        warnings=tuple(warnings),
    )
