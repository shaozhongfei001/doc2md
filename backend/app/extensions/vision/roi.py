"""One bounded local re-recognition pass (SDD 11.2, ``PI-F05-05``).

Contract points, each turned into code:

* *"以原图verified region扩大受控边距裁图，保存crop矩阵和hash；无可靠region则回退整页
  人工查看，不能凭猜测裁图"* — the crop is derived from a region that already has
  verified geometry. Without one, the plan is an explicit page-level fallback, not
  a guessed rectangle.
* *"每个对象最多追加一轮ROI识别"* — :class:`RoiBudget` refuses a second pass for the
  same object. Transport retries are counted on a separate counter, so a flaky
  connection cannot be disguised as extra recognition.
* *"新旧候选都保留…不能通过取最高自报分数直接覆盖原结果"* — :func:`merge_rerun`
  never picks a winner. It reports CONSISTENT / CONFLICT / UNKNOWN and leaves both
  candidates in the payload.
* *"相同模型换提示不算独立验证"* — a rerun from the same model is marked
  ``independent=False`` even when it disagrees; disagreement then means NEEDS_REVIEW,
  not "the second one is right".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

__all__ = [
    "RoiBudget",
    "RoiPlan",
    "merge_rerun",
    "plan_roi",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass
class RoiBudget:
    """Pass budget per object plus a separate transport-retry budget."""

    max_passes_per_object: int = 1
    max_objects: int = 50
    max_retries: int = 2
    objects: set[str] = field(default_factory=set)
    retries: int = 0

    def __post_init__(self) -> None:
        if self.max_passes_per_object != 1:
            # The contract says exactly one extra pass; anything else is a new decision.
            raise ValueError(
                "max_passes_per_object is fixed at 1 by SDD 11.2; changing it needs an ADR"
            )
        for name in ("max_objects", "max_retries"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer, got {value!r}")

    def begin_object(self, object_key: str) -> None:
        if not isinstance(object_key, str) or not object_key:
            raise _input_error(f"object_key must be a non-empty string, got {object_key!r}")
        if object_key in self.objects:
            raise _input_error(
                "this object already had its one extra ROI pass; a second pass is a new "
                "decision, not a retry",
                object_key=object_key,
            )
        if len(self.objects) >= self.max_objects:
            raise _input_error(
                "object budget exhausted for this run",
                used=len(self.objects),
                limit=self.max_objects,
            )
        self.objects.add(object_key)

    def retry(self) -> int:
        """Charge one *transport* retry; separate from recognition passes."""
        if self.retries + 1 > self.max_retries:
            raise ExtensionError(
                "E_ENGINE_TIMEOUT",
                "transport retry budget exhausted",
                detail={"retries": self.retries, "limit": self.max_retries},
            )
        self.retries += 1
        return self.retries

    def remaining(self) -> dict[str, int]:
        return {
            "objects": self.max_objects - len(self.objects),
            "retries": self.max_retries - self.retries,
        }


@dataclass(frozen=True)
class RoiPlan:
    object_key: str
    mode: str  # "ROI" | "PAGE_FALLBACK"
    polygon: list[list[float]] | None
    margin: float
    crop_ref: str | None
    crop_matrix: list[list[float]] | None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_key": self.object_key,
            "mode": self.mode,
            "polygon": self.polygon,
            "margin": self.margin,
            "crop_ref": self.crop_ref,
            "crop_matrix": self.crop_matrix,
            "reason": self.reason,
        }


def _check_polygon(polygon: Any) -> list[list[float]]:
    if isinstance(polygon, (str, bytes)) or not isinstance(polygon, Sequence) or len(polygon) != 4:
        raise _input_error(f"region polygon must be four corners, got {polygon!r}")
    points: list[list[float]] = []
    for index, point in enumerate(polygon):
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
            raise _input_error(f"polygon[{index}] must be a pair (x, y), got {point!r}")
        for value in point:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _input_error(f"polygon[{index}] must be numeric, got {point!r}")
        points.append([float(point[0]), float(point[1])])
    return points


def plan_roi(
    region: Mapping[str, Any],
    *,
    object_key: str,
    budget: RoiBudget,
    margin: float = 8.0,
    page_size: tuple[float, float] | None = None,
    crop_matrix: Sequence[Sequence[float]] | None = None,
) -> RoiPlan:
    """Plan the single extra ROI pass for ``region``.

    ``region`` is a mapping from ``geometry.map_to_original``: it must carry
    ``evidence_class == "REGION_VERIFIED"`` and a polygon. A ``PAGE_ONLY`` region
    produces an explicit ``PAGE_FALLBACK`` plan — never a guessed crop.
    """
    if not isinstance(region, Mapping):
        raise _input_error(f"region must be a mapping, got {type(region).__name__}")
    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or margin < 0:
        raise _input_error(f"margin must be a non-negative number, got {margin!r}")

    budget.begin_object(object_key)

    evidence_class = region.get("evidence_class")
    polygon = region.get("polygon")
    if evidence_class != "REGION_VERIFIED" or polygon is None:
        return RoiPlan(
            object_key=object_key,
            mode="PAGE_FALLBACK",
            polygon=None,
            margin=float(margin),
            crop_ref=None,
            crop_matrix=None,
            reason=(
                f"region evidence is {evidence_class!r}, not REGION_VERIFIED; cropping would "
                "be a guess, so the whole page goes to manual review"
            ),
        )

    points = _check_polygon(polygon)
    min_x = min(x for x, _ in points) - float(margin)
    min_y = min(y for _, y in points) - float(margin)
    max_x = max(x for x, _ in points) + float(margin)
    max_y = max(y for _, y in points) + float(margin)

    if page_size is not None:
        width, height = page_size
        if width <= 0 or height <= 0:
            raise _input_error(f"page_size must be positive, got {page_size!r}")
        min_x = max(min_x, 0.0)
        min_y = max(min_y, 0.0)
        max_x = min(max_x, float(width))
        max_y = min(max_y, float(height))

    if max_x <= min_x or max_y <= min_y:
        raise _input_error(
            "expanded crop has no area; margin and page bounds are inconsistent",
            bounds=[min_x, min_y, max_x, max_y],
        )

    crop_polygon = [
        [min_x, min_y],
        [max_x, min_y],
        [max_x, max_y],
        [min_x, max_y],
    ]
    digest = hashlib.sha256(
        repr([round(v, 6) for point in crop_polygon for v in point]).encode("utf-8")
    ).hexdigest()

    matrix = (
        [list(row) for row in crop_matrix] if crop_matrix is not None else None
    )
    if matrix is not None:
        if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
            raise _input_error("crop_matrix must be a 3x3 matrix when given")

    return RoiPlan(
        object_key=object_key,
        mode="ROI",
        polygon=crop_polygon,
        margin=float(margin),
        crop_ref=f"crop:{digest[:16]}",
        crop_matrix=matrix,
    )


def merge_rerun(
    original: Mapping[str, Any],
    rerun: Mapping[str, Any],
    *,
    object_key: str,
    independent: bool,
) -> dict[str, Any]:
    """Keep both candidates and report agreement; never pick by score.

    ``independent=False`` means the rerun used the same model with a different
    prompt — SDD 11.2 says that is not independent verification, so agreement is
    not promoted either.
    """
    for name, candidate in (("original", original), ("rerun", rerun)):
        if not isinstance(candidate, Mapping):
            raise _input_error(f"{name} candidate must be a mapping")
        if "text" not in candidate and "value" not in candidate:
            raise _input_error(
                f"{name} candidate carries neither 'text' nor 'value'; nothing to compare"
            )
    if not isinstance(independent, bool):
        raise _input_error(f"independent must be a bool, got {independent!r}")

    def payload(candidate: Mapping[str, Any]) -> str | None:
        for key in ("text", "value"):
            if candidate.get(key) is not None:
                return str(candidate[key])
        return None

    left, right = payload(original), payload(rerun)
    if left is None or right is None:
        verdict = "UNKNOWN"
    elif left == right:
        verdict = "CONSISTENT"
    else:
        verdict = "CONFLICT"

    return {
        "object_key": object_key,
        "verdict": verdict,
        "independent": independent,
        "original": dict(original),
        "rerun": dict(rerun),
        "resolved": None,
        "state": "NEEDS_REVIEW" if verdict == "CONFLICT" or not independent else "CANDIDATE",
        "reason": (
            "same model with a different prompt is not independent verification"
            if not independent
            else "both candidates kept; a review decision selects one when they disagree"
        ),
    }
