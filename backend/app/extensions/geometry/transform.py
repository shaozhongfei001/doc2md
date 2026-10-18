"""T403 / ``PI-F04-03``: unit-explicit geometry and reversible mapping.

SDD 8.2 asks for six things, and each maps onto a rule here:

1. *Measurement base is the decoded original page, in pixels.* A geometry whose
   unit was never verified cannot produce a region fact, only ``PAGE_ONLY``.
2. *3×3 homogeneous transform, composed step by step, kept with the page.*
   Composition order is fixed and documented (:func:`compose`).
3. *Mapping back applies the inverse to the four corners* — not a width/height
   rescale, which would silently ignore rotation and crop.
4. *Only numeric clipping inside an explicit epsilon; beyond it, refuse.*
   ``EPSILON`` is in original-image pixels (1px is the SDD starting suggestion,
   to be calibrated on real samples).
5. *Reject non-invertible matrices* — a perspective correction that cannot be
   undone must not yield region-level evidence.
6. *Unknown units are not invented.* ``unit_verified`` is the caller's explicit
   statement that it checked the unit against the source.

Error codes are deliberately kept inside the contract's declared set: boundary
violations reuse ``E_GEOMETRY_UNVERIFIED`` (the region cannot be verified on the
page) rather than inventing a new code that would land in the wrong exit bucket.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

#: Original-image pixels. Clipping is allowed only inside this tolerance.
EPSILON = 1.0

UNIT_PX = "PX"
UNIT_PT = "PT"
UNITS = (UNIT_PX, UNIT_PT)

Matrix = tuple[tuple[float, float, float], ...]
Point = tuple[float, float]

__all__ = [
    "EPSILON",
    "UNIT_PT",
    "UNIT_PX",
    "Matrix",
    "PageGeometry",
    "RegionMapping",
    "apply",
    "compose",
    "identity",
    "inverse",
    "map_or_page_only",
    "map_to_original",
    "page_only",
    "polygon_area",
    "rotate",
    "scale",
    "translate",
]


def _error(code: str, message: str, **detail: Any) -> ExtensionError:
    return ExtensionError(code, message, detail=detail or None)


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return _error("E_INPUT_INVALID", message, **detail)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value: float) -> bool:
    return math.isfinite(value)


# ------------------------------------------------------------------- matrices
def identity() -> Matrix:
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def scale(sx: float, sy: float | None = None) -> Matrix:
    """Uniform or anisotropic scale about the origin."""
    sy = sx if sy is None else sy
    for value in (sx, sy):
        if not _is_number(value) or not _finite(float(value)) or float(value) == 0.0:
            raise _input_error(f"scale factor must be a finite non-zero number, got {value!r}")
    return ((float(sx), 0.0, 0.0), (0.0, float(sy), 0.0), (0.0, 0.0, 1.0))


def translate(tx: float, ty: float) -> Matrix:
    for value in (tx, ty):
        if not _is_number(value) or not _finite(float(value)):
            raise _input_error(f"translation must be finite numbers, got {value!r}")
    return ((1.0, 0.0, float(tx)), (0.0, 1.0, float(ty)), (0.0, 0.0, 1.0))


def rotate(degrees: float) -> Matrix:
    """Rotation in the image plane (positive = clockwise on a y-down axis)."""
    if not _is_number(degrees) or not _finite(float(degrees)):
        raise _input_error(f"rotation must be a finite number, got {degrees!r}")
    quarter = float(degrees) / 90.0
    if abs(quarter - round(quarter)) < 1e-12:
        # Exact values for the quartet, so 90/180/270 round-trips do not drift.
        step = int(round(quarter)) % 4
        table = {
            0: identity(),
            1: ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            2: ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
            3: ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        }
        return table[step]
    radians = math.radians(float(degrees))
    cos, sin = math.cos(radians), math.sin(radians)
    return ((cos, -sin, 0.0), (sin, cos, 0.0), (0.0, 0.0, 1.0))


def _as_matrix(matrix: Any, *, name: str = "matrix") -> Matrix:
    if not isinstance(matrix, Sequence) or isinstance(matrix, (str, bytes)):
        raise _input_error(f"{name} must be a 3x3 sequence, got {type(matrix).__name__}")
    if len(matrix) != 3:
        raise _input_error(f"{name} must have 3 rows, got {len(matrix)}")
    rows: list[tuple[float, float, float]] = []
    for row_index, row in enumerate(matrix):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != 3:
            raise _input_error(f"{name}[{row_index}] must have 3 entries")
        values: list[float] = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _input_error(f"{name}[{row_index}] contains non-numeric {value!r}")
            if not _finite(float(value)):
                raise _input_error(
                    f"{name}[{row_index}] contains a non-finite value {value!r}; a NaN "
                    "transform cannot be verified or inverted"
                )
            values.append(float(value))
        rows.append((values[0], values[1], values[2]))
    return (rows[0], rows[1], rows[2])


def compose(*matrices: Matrix) -> Matrix:
    """Multiply matrices left-to-right: ``compose(A, B)`` applies **B then A**.

    The order is fixed here rather than at each call site, because
    "scale-then-crop" and "crop-then-scale" differ by a factor of the scale and
    the mistake is invisible until coordinates are wrong by a few hundred pixels.
    """
    if not matrices:
        return identity()
    result = _as_matrix(matrices[0], name="matrices[0]")
    for index, matrix in enumerate(matrices[1:], start=1):
        other = _as_matrix(matrix, name=f"matrices[{index}]")
        result = tuple(  # type: ignore[assignment]
            tuple(
                sum(result[r][k] * other[k][c] for k in range(3))
                for c in range(3)
            )
            for r in range(3)
        )
    return result  # type: ignore[return-value]


def determinant(matrix: Matrix) -> float:
    m = _as_matrix(matrix)
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def inverse(matrix: Matrix) -> Matrix:
    """Invert a 3×3 homogeneous transform, refusing near-singular ones."""
    m = _as_matrix(matrix)
    det = determinant(m)
    if not _finite(det) or abs(det) < 1e-12:
        raise _error(
            "E_GEOMETRY_NONINVERTIBLE",
            "transform is singular (or numerically singular); the mapping back to the "
            "original page cannot be reconstructed",
            determinant=det,
        )
    inv_det = 1.0 / det
    cofactors = (
        (
            (m[1][1] * m[2][2] - m[1][2] * m[2][1]),
            (m[0][2] * m[2][1] - m[0][1] * m[2][2]),
            (m[0][1] * m[1][2] - m[0][2] * m[1][1]),
        ),
        (
            (m[1][2] * m[2][0] - m[1][0] * m[2][2]),
            (m[0][0] * m[2][2] - m[0][2] * m[2][0]),
            (m[0][2] * m[1][0] - m[0][0] * m[1][2]),
        ),
        (
            (m[1][0] * m[2][1] - m[1][1] * m[2][0]),
            (m[0][1] * m[2][0] - m[0][0] * m[2][1]),
            (m[0][0] * m[1][1] - m[0][1] * m[1][0]),
        ),
    )
    return tuple(  # type: ignore[return-value]
        tuple(cofactors[r][c] * inv_det for c in range(3)) for r in range(3)
    )


def apply(matrix: Matrix, point: Sequence[float]) -> Point:
    """Apply a homogeneous transform to one point."""
    m = _as_matrix(matrix)
    if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
        raise _input_error(f"point must be a pair (x, y), got {point!r}")
    x, y = point
    for value in (x, y):
        if not _is_number(value) or not _finite(float(value)):
            raise _input_error(f"point must be finite numbers, got {point!r}")
    x, y = float(x), float(y)
    hx = m[0][0] * x + m[0][1] * y + m[0][2]
    hy = m[1][0] * x + m[1][1] * y + m[1][2]
    hw = m[2][0] * x + m[2][1] * y + m[2][2]
    if abs(hw) < 1e-12:
        raise _error(
            "E_GEOMETRY_NONINVERTIBLE",
            "transform projects the point to infinity (w≈0); the mapping is not finite",
            point=[x, y],
        )
    return (hx / hw, hy / hw)


def polygon_area(polygon: Sequence[Sequence[float]]) -> float:
    """Shoelace area (absolute), used to prove a mapped region has extent."""
    points = _as_polygon(polygon)
    total = 0.0
    for index, (x, y) in enumerate(points):
        nx, ny = points[(index + 1) % len(points)]
        total += x * ny - nx * y
    return abs(total) / 2.0


def _as_polygon(polygon: Any) -> list[Point]:
    if isinstance(polygon, (str, bytes)) or not isinstance(polygon, Sequence):
        raise _input_error(f"polygon must be a sequence of points, got {type(polygon).__name__}")
    points: list[Point] = []
    for index, point in enumerate(polygon):
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
            raise _input_error(f"polygon[{index}] must be a pair (x, y), got {point!r}")
        x, y = point
        for value in (x, y):
            if not _is_number(value) or not _finite(float(value)):
                raise _input_error(f"polygon[{index}] must be finite numbers, got {point!r}")
        points.append((float(x), float(y)))
    return points


# ------------------------------------------------------------------- geometry
@dataclass(frozen=True)
class PageGeometry:
    """Page-level measurement context (SDD 8.2).

    ``matrix`` maps **original page pixels → processed (cropped/rotated) pixels**,
    which is the direction every engine reports in. Mapping back therefore uses
    :func:`inverse`.
    """

    geometry_id: str
    source_sha256: str
    page_number: int
    original_width: int
    original_height: int
    processed_width: int
    processed_height: int
    crop_origin: tuple[float, float]
    matrix: Matrix
    unit: str
    unit_verified: bool
    dpi: float | None = None
    renderer_version: str | None = None
    cropbox: tuple[float, float, float, float] | None = None
    rotation: int = 0
    trace: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PageGeometry":
        if not isinstance(raw, Mapping):
            raise _input_error(f"geometry must be a mapping, got {type(raw).__name__}")

        geometry_id = raw.get("geometry_id")
        if not isinstance(geometry_id, str) or not geometry_id:
            raise _input_error(f"geometry_id must be a non-empty string, got {geometry_id!r}")
        digest = raw.get("source_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise _input_error(
                f"source_sha256 must be a 64-char digest of the original page image, got "
                f"{digest!r}"
            )

        page_number = raw.get("page_number")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise _input_error(
                f"page_number must be a 1-based integer, got {page_number!r}"
            )

        dims: dict[str, int] = {}
        for name in ("original_width", "original_height", "processed_width", "processed_height"):
            value = raw.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise _input_error(f"{name} must be a positive integer, got {value!r}")
            dims[name] = value

        crop = raw.get("crop_origin", (0.0, 0.0))
        if isinstance(crop, (str, bytes)) or not isinstance(crop, Sequence) or len(crop) != 2:
            raise _input_error(f"crop_origin must be a pair, got {crop!r}")
        for value in crop:
            if not _is_number(value) or not _finite(float(value)):
                raise _input_error(f"crop_origin must be finite numbers, got {crop!r}")

        unit = raw.get("unit")
        if unit not in UNITS:
            raise _input_error(
                f"unit must be one of {list(UNITS)}; an unknown unit is never treated as "
                f"pixels, got {unit!r}"
            )
        verified = raw.get("unit_verified")
        if not isinstance(verified, bool):
            raise _input_error(
                f"unit_verified must be an explicit bool (the caller checked the unit "
                f"against the source), got {verified!r}"
            )

        dpi = raw.get("dpi")
        if dpi is not None and (not _is_number(dpi) or not _finite(float(dpi)) or float(dpi) <= 0):
            raise _input_error(f"dpi must be a positive number or null, got {dpi!r}")

        rotation = raw.get("rotation", 0)
        if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation % 90 != 0:
            raise _input_error(
                f"rotation must be an integer multiple of 90, got {rotation!r}"
            )

        cropbox = raw.get("cropbox")
        if cropbox is not None:
            if isinstance(cropbox, (str, bytes)) or not isinstance(cropbox, Sequence) or len(cropbox) != 4:
                raise _input_error(f"cropbox must be four numbers, got {cropbox!r}")
            for value in cropbox:
                if not _is_number(value) or not _finite(float(value)):
                    raise _input_error(f"cropbox must be finite numbers, got {cropbox!r}")
            cropbox = tuple(float(v) for v in cropbox)  # type: ignore[assignment]

        trace = raw.get("trace") or ()
        if isinstance(trace, (str, bytes)) or not isinstance(trace, Sequence):
            raise _input_error("trace must be a sequence of step labels")
        for step in trace:
            if not isinstance(step, str) or not step:
                raise _input_error(f"trace contains {step!r}")

        return cls(
            geometry_id=geometry_id,
            source_sha256=digest.lower(),
            page_number=page_number,
            original_width=dims["original_width"],
            original_height=dims["original_height"],
            processed_width=dims["processed_width"],
            processed_height=dims["processed_height"],
            crop_origin=(float(crop[0]), float(crop[1])),
            matrix=_as_matrix(raw.get("matrix")),
            unit=unit,
            unit_verified=verified,
            dpi=None if dpi is None else float(dpi),
            renderer_version=raw.get("renderer_version"),
            cropbox=cropbox,
            rotation=rotation,
            trace=tuple(trace),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "geometry_id": self.geometry_id,
            "source_sha256": self.source_sha256,
            "page_number": self.page_number,
            "original_width": self.original_width,
            "original_height": self.original_height,
            "processed_width": self.processed_width,
            "processed_height": self.processed_height,
            "crop_origin": list(self.crop_origin),
            "matrix": [list(row) for row in self.matrix],
            "unit": self.unit,
            "unit_verified": self.unit_verified,
            "dpi": self.dpi,
            "renderer_version": self.renderer_version,
            "cropbox": None if self.cropbox is None else list(self.cropbox),
            "rotation": self.rotation,
            "trace": list(self.trace),
        }


@dataclass(frozen=True)
class RegionMapping:
    """A region proven to be on the original page, or an explicit PAGE_ONLY."""

    evidence_class: str
    page_number: int
    source_sha256: str
    polygon: list[list[float]] | None = None
    normalized_polygon: list[list[float]] | None = None
    area: float | None = None
    unit: str | None = None
    epsilon: float | None = None
    reason: str | None = None
    trace: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "evidence_class": self.evidence_class,
            "page_number": self.page_number,
            "source_sha256": self.source_sha256,
            "polygon": self.polygon,
            "normalized_polygon": self.normalized_polygon,
            "area": self.area,
            "unit": self.unit,
            "epsilon": self.epsilon,
            "reason": self.reason,
            "trace": list(self.trace),
        }


def map_to_original(
    geometry: PageGeometry | Mapping[str, Any],
    polygon: Sequence[Sequence[float]],
    *,
    epsilon: float = EPSILON,
) -> RegionMapping:
    """Map a processed-space polygon back to original page pixels.

    Refuses (never guesses) when: the unit was not verified, the transform is not
    invertible, the polygon is degenerate, or the result leaves the page by more
    than ``epsilon`` original-image pixels.
    """
    geo = geometry if isinstance(geometry, PageGeometry) else PageGeometry.from_mapping(geometry)
    if not _is_number(epsilon) or not _finite(float(epsilon)) or float(epsilon) < 0:
        raise _input_error(f"epsilon must be a non-negative number, got {epsilon!r}")

    if not geo.unit_verified:
        raise _error(
            "E_GEOMETRY_UNVERIFIED",
            "the page unit was not verified against the source; only PAGE_ONLY evidence "
            "may be produced from this geometry",
            geometry_id=geo.geometry_id,
            unit=geo.unit,
        )

    points = _as_polygon(polygon)
    if len(points) != 4:
        raise _input_error(
            f"a region must be given as its four corners, got {len(points)} points; a "
            "bbox width/height rescale would ignore rotation and crop"
        )

    # The polygon must live in the *processed* canvas the matrix expects. This is
    # not redundant with the page-bounds check below: if an engine returned
    # coordinates in a different space (a common drift), the inverse can still land
    # inside the page and produce a plausible, wrong region.
    for index, (x, y) in enumerate(points):
        if (
            x < -epsilon
            or y < -epsilon
            or x > geo.processed_width + epsilon
            or y > geo.processed_height + epsilon
        ):
            raise _input_error(
                f"polygon[{index}] = ({x}, {y}) lies outside the processed canvas "
                f"{geo.processed_width}x{geo.processed_height}; the region is not in the "
                "space this geometry's matrix maps from",
                geometry_id=geo.geometry_id,
            )

    back = inverse(geo.matrix)
    mapped: list[Point] = [apply(back, point) for point in points]

    for index, (x, y) in enumerate(mapped):
        if not (_finite(x) and _finite(y)):
            raise _error(
                "E_GEOMETRY_UNVERIFIED",
                "mapped corner is not finite",
                corner=index,
            )

    area = polygon_area(mapped)
    if area <= 0.0:
        raise _error(
            "E_GEOMETRY_UNVERIFIED",
            "mapped polygon has no area; a zero-area region cannot be verified",
            area=area,
        )

    min_x = min(x for x, _ in mapped)
    min_y = min(y for _, y in mapped)
    max_x = max(x for x, _ in mapped)
    max_y = max(y for _, y in mapped)
    outside = (
        min_x < -epsilon
        or min_y < -epsilon
        or max_x > geo.original_width + epsilon
        or max_y > geo.original_height + epsilon
    )
    if outside:
        raise _error(
            "E_GEOMETRY_UNVERIFIED",
            "mapped polygon leaves the original page beyond epsilon; the transform or the "
            "unit is wrong, so no region-level fact may be claimed",
            bounds=[min_x, min_y, max_x, max_y],
            page=[geo.original_width, geo.original_height],
            epsilon=epsilon,
        )

    # Clipping is allowed only inside epsilon, and only to the page rectangle.
    clipped: list[list[float]] = [
        [
            min(max(x, 0.0), float(geo.original_width)),
            min(max(y, 0.0), float(geo.original_height)),
        ]
        for x, y in mapped
    ]
    normalized = [
        [x / geo.original_width, y / geo.original_height] for x, y in clipped
    ]

    return RegionMapping(
        evidence_class="REGION_VERIFIED",
        page_number=geo.page_number,
        source_sha256=geo.source_sha256,
        polygon=clipped,
        normalized_polygon=normalized,
        area=polygon_area(clipped),
        unit=geo.unit,
        epsilon=float(epsilon),
        trace=tuple(geo.trace) + ("inverse(matrix)->original_page_px",),
    )


def page_only(
    geometry: PageGeometry | Mapping[str, Any], *, reason: str
) -> RegionMapping:
    """Explicit page-level evidence: the honest answer when mapping is impossible."""
    geo = geometry if isinstance(geometry, PageGeometry) else PageGeometry.from_mapping(geometry)
    if not isinstance(reason, str) or not reason.strip():
        raise _input_error("page_only requires a non-empty reason")
    return RegionMapping(
        evidence_class="PAGE_ONLY",
        page_number=geo.page_number,
        source_sha256=geo.source_sha256,
        reason=reason,
        trace=tuple(geo.trace) + ("page_only",),
    )


def map_or_page_only(
    geometry: PageGeometry | Mapping[str, Any],
    polygon: Sequence[Sequence[float]],
    *,
    epsilon: float = EPSILON,
) -> RegionMapping:
    """Map when provable, otherwise degrade to ``PAGE_ONLY`` with the reason kept.

    This is the shape SDD 8.2 asks for: "无法逆映射时只保留 PAGE_ONLY". The
    failure is not swallowed — its code and message are carried in ``reason``.
    """
    try:
        return map_to_original(geometry, polygon, epsilon=epsilon)
    except ExtensionError as exc:
        if exc.code not in {"E_GEOMETRY_UNVERIFIED", "E_GEOMETRY_NONINVERTIBLE"}:
            raise
        return page_only(geometry, reason=f"{exc.code}: {exc.message}")
