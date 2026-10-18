"""T404 / ``PI-F04-05``: put OCR text back into the cell it belongs to.

SDD 8.3 is explicit about the order of authority:

    "优先采用框架已给出的cell文本和对应证据。仅当框架没给可靠cell文字/发现OCR不一致时，
     用OCR多边形与cell多边形的覆盖率作候选：包含/最大覆盖唯一且领先间隔满足阈值才关联；
     横跨两格、得分接近、跨行折返或文本被截断不强配，不靠阅读顺序分列。"

So this module:

* keeps the engine's own cell text whenever it exists, and records — rather than
  silently resolves — any conflict with tokens;
* binds a token only when its coverage of the cell clears ``coverage_threshold``
  **and** beats the runner-up by ``margin_threshold``; otherwise the cell is
  ambiguous and stays unresolved;
* never binds a token that meaningfully covers two cells;
* never cleans token text (``-1,234``, ``(500)``, ``12%``, units stay verbatim);
* never invents a confidence: engine values pass through, ``None`` stays ``None``.

Thresholds are parameters, not constants: 0.8 / 0.15 are SDD starting points that
must be calibrated on golden samples, so hard-coding them here would hide the
calibration debt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError
from ..geometry.transform import polygon_area

DEFAULT_COVERAGE = 0.8
DEFAULT_MARGIN = 0.15

__all__ = [
    "DEFAULT_COVERAGE",
    "DEFAULT_MARGIN",
    "AlignmentIssue",
    "AlignmentResult",
    "align_tokens",
    "coverage",
    "intersection_area",
]


def _error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


Point = tuple[float, float]


def _polygon(value: Any, *, name: str) -> list[Point]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        raise _error(f"{name} must be a four-corner polygon, got {value!r}")
    points: list[Point] = []
    for index, point in enumerate(value):
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
            raise _error(f"{name}[{index}] must be a pair (x, y), got {point!r}")
        x, y = point
        for coordinate in (x, y):
            if not _is_number(coordinate):
                raise _error(f"{name}[{index}] must be numbers, got {point!r}")
        points.append((float(x), float(y)))
    if polygon_area(points) <= 0.0:
        raise _error(f"{name} has zero area; a degenerate polygon covers nothing")
    return points


def _signed_area(polygon: Sequence[Point]) -> float:
    total = 0.0
    for index, (x, y) in enumerate(polygon):
        nx, ny = polygon[(index + 1) % len(polygon)]
        total += x * ny - nx * y
    return total / 2.0


def _inside(point: Point, a: Point, b: Point) -> bool:
    """Left-of test for a counter-clockwise clip edge."""
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) >= 0.0


def _line_intersection(p1: Point, p2: Point, a: Point, b: Point) -> Point:
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = a
    x4, y4 = b
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-12:  # parallel: return the midpoint, area impact ~0
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denominator
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denominator
    return (px, py)


def _clip(subject: Sequence[Point], clip: Sequence[Point]) -> list[Point]:
    """Sutherland–Hodgman clipping of a convex ``subject`` by a convex ``clip``."""
    oriented = list(clip)
    if _signed_area(oriented) < 0:
        oriented.reverse()
    output = list(subject)
    for index in range(len(oriented)):
        a = oriented[index]
        b = oriented[(index + 1) % len(oriented)]
        if not output:
            return []
        previous = output[-1]
        current_input = output
        output = []
        for point in current_input:
            current_inside = _inside(point, a, b)
            previous_inside = _inside(previous, a, b)
            if current_inside:
                if not previous_inside:
                    output.append(_line_intersection(previous, point, a, b))
                output.append(point)
            elif previous_inside:
                output.append(_line_intersection(previous, point, a, b))
            previous = point
    return output


def intersection_area(left: Sequence[Point], right: Sequence[Point]) -> float:
    """Area shared by two convex polygons (both given as four corners)."""
    clipped = _clip(list(left), list(right))
    if len(clipped) < 3:
        return 0.0
    return polygon_area(clipped)


def coverage(token: Sequence[Point], cell: Sequence[Point]) -> float:
    """Fraction of the token's own area that falls inside the cell."""
    token_area = polygon_area(token)
    if token_area <= 0.0:
        return 0.0
    return intersection_area(token, cell) / token_area


@dataclass(frozen=True)
class AlignmentIssue:
    code: str
    message: str
    cell_id: str | None = None
    token_ids: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.cell_id is not None:
            payload["cell_id"] = self.cell_id
        if self.token_ids:
            payload["token_ids"] = list(self.token_ids)
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload


@dataclass(frozen=True)
class AlignmentResult:
    cells: list[dict[str, Any]]
    issues: list[AlignmentIssue] = field(default_factory=list)

    @property
    def unresolved(self) -> list[dict[str, Any]]:
        return [cell for cell in self.cells if cell["state"] == "AMBIGUOUS"]

    def to_cell_updates(self) -> list[dict[str, Any]]:
        """Contract-shaped ``Cell`` text fields only (no review bookkeeping)."""
        return [
            {
                "cell_id": cell["cell_id"],
                "text": cell["text"],
                "text_origin": cell["text_origin"],
                "text_alignment": cell["text_alignment"],
                "engine_confidence": cell["engine_confidence"],
                "token_ids": list(cell["token_ids"]),
            }
            for cell in self.cells
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cells": [dict(cell) for cell in self.cells],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _confidence(value: Any, *, name: str) -> float | None:
    if value is None:
        return None
    if not _is_number(value) or not 0.0 <= float(value) <= 1.0:
        raise _error(
            f"{name} must be a number in [0, 1] or null; a missing confidence is never "
            f"estimated, got {value!r}"
        )
    return float(value)


def align_tokens(
    cells: Sequence[Mapping[str, Any]],
    tokens: Sequence[Mapping[str, Any]],
    *,
    coverage_threshold: float = DEFAULT_COVERAGE,
    margin_threshold: float = DEFAULT_MARGIN,
) -> AlignmentResult:
    """Assign OCR tokens to cells, preferring the engine's own cell text.

    ``cells`` entries: ``{cell_id, polygon, text?, engine_confidence?}`` where
    ``text`` is the framework's cell text (``None`` when it gave none).
    ``tokens`` entries: ``{token_id, text, polygon, confidence?}``.
    """
    if not _is_number(coverage_threshold) or not 0.0 < float(coverage_threshold) <= 1.0:
        raise _error(
            f"coverage_threshold must be in (0, 1], got {coverage_threshold!r}"
        )
    if not _is_number(margin_threshold) or not 0.0 <= float(margin_threshold) < 1.0:
        raise _error(f"margin_threshold must be in [0, 1), got {margin_threshold!r}")

    cell_rows: list[dict[str, Any]] = []
    cell_polygons: dict[str, list[Point]] = {}
    for index, raw in enumerate(cells):
        if not isinstance(raw, Mapping):
            raise _error(f"cells[{index}] must be a mapping, got {type(raw).__name__}")
        cell_id = raw.get("cell_id")
        if not isinstance(cell_id, str) or not cell_id:
            raise _error(f"cells[{index}] has no usable cell_id: {cell_id!r}")
        if cell_id in cell_polygons:
            raise _error(f"duplicate cell_id {cell_id!r}")
        polygon = _polygon(raw.get("polygon"), name=f"cells[{index}].polygon")
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise _error(
                f"cell {cell_id}: text must be a string or null, got {type(text).__name__}; "
                "an empty engine result is null, not \"\""
            )
        cell_polygons[cell_id] = polygon
        cell_rows.append(
            {
                "cell_id": cell_id,
                "text": text,
                "engine_confidence": _confidence(
                    raw.get("engine_confidence"), name=f"cell {cell_id}.engine_confidence"
                ),
            }
        )

    token_rows: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    for index, raw in enumerate(tokens):
        if not isinstance(raw, Mapping):
            raise _error(f"tokens[{index}] must be a mapping, got {type(raw).__name__}")
        token_id = raw.get("token_id")
        if not isinstance(token_id, str) or not token_id:
            raise _error(f"tokens[{index}] has no usable token_id: {token_id!r}")
        if token_id in seen_tokens:
            raise _error(f"duplicate token_id {token_id!r}")
        seen_tokens.add(token_id)
        text = raw.get("text")
        if not isinstance(text, str) or not text:
            raise _error(f"token {token_id}: text must be a non-empty string")
        token_rows.append(
            {
                "token_id": token_id,
                "text": text,
                "polygon": _polygon(raw.get("polygon"), name=f"token {token_id}.polygon"),
                "confidence": _confidence(
                    raw.get("confidence"), name=f"token {token_id}.confidence"
                ),
            }
        )

    issues: list[AlignmentIssue] = []
    results: list[dict[str, Any]] = []

    # Coverage is computed once per (token, cell) pair so the "spans two cells"
    # rule and the per-cell ranking cannot disagree with each other.
    matrix: dict[str, dict[str, float]] = {
        cell["cell_id"]: {
            token["token_id"]: coverage(token["polygon"], cell_polygons[cell["cell_id"]])
            for token in token_rows
        }
        for cell in cell_rows
    }

    spanning: set[str] = set()
    for token in token_rows:
        covered_cells = [
            cell_id
            for cell_id, values in matrix.items()
            if values[token["token_id"]] >= float(coverage_threshold)
        ]
        if len(covered_cells) > 1:
            spanning.add(token["token_id"])
            issues.append(
                AlignmentIssue(
                    code="TOKEN_SPANS_CELLS",
                    message=(
                        "token covers more than one cell above threshold; it is not bound "
                        "and the cells stay unresolved rather than splitting the text by "
                        "reading order"
                    ),
                    token_ids=(token["token_id"],),
                    detail={"cells": sorted(covered_cells)},
                )
            )

    for cell in cell_rows:
        cell_id = cell["cell_id"]
        scores = matrix[cell_id]
        ranked = sorted(
            (
                (score, token_id)
                for token_id, score in scores.items()
                if score >= float(coverage_threshold) and token_id not in spanning
            ),
            reverse=True,
        )

        if cell["text"] is not None:
            if ranked:
                issues.append(
                    AlignmentIssue(
                        code="ENGINE_TEXT_OVERRIDES_OCR",
                        message=(
                            "the engine supplied cell text and OCR tokens also cover this "
                            "cell; the engine text is kept and the disagreement is recorded"
                        ),
                        cell_id=cell_id,
                        token_ids=tuple(token_id for _, token_id in ranked),
                        detail={"engine_text": cell["text"]},
                    )
                )
            results.append(
                {
                    "cell_id": cell_id,
                    "text": cell["text"],
                    "text_origin": "ENGINE_OCR",
                    "text_alignment": "ENGINE_CELL_TEXT",
                    "engine_confidence": cell["engine_confidence"],
                    "token_ids": [],
                    "state": "RESOLVED_ENGINE",
                    "candidates": [token_id for _, token_id in ranked],
                }
            )
            continue

        if not ranked:
            issues.append(
                AlignmentIssue(
                    code="NO_TOKEN_COVERAGE",
                    message=(
                        "the engine gave no cell text and no token covers this cell above "
                        "threshold; text stays null"
                    ),
                    cell_id=cell_id,
                    detail={"best_coverage": max(scores.values(), default=0.0)},
                )
            )
            results.append(
                {
                    "cell_id": cell_id,
                    "text": None,
                    "text_origin": "ENGINE_OCR",
                    "text_alignment": "UNKNOWN",
                    "engine_confidence": None,
                    "token_ids": [],
                    "state": "EMPTY",
                    "candidates": [],
                }
            )
            continue

        best_score, best_token = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        by_id = {token["token_id"]: token for token in token_rows}

        if best_score - second_score < float(margin_threshold):
            issues.append(
                AlignmentIssue(
                    code="AMBIGUOUS_TOKEN_ASSIGNMENT",
                    message=(
                        "two or more tokens cover this cell with scores too close to "
                        "separate; no text is assigned"
                    ),
                    cell_id=cell_id,
                    token_ids=tuple(token_id for _, token_id in ranked),
                    detail={
                        "scores": {token_id: round(score, 4) for score, token_id in ranked},
                        "margin_threshold": float(margin_threshold),
                    },
                )
            )
            results.append(
                {
                    "cell_id": cell_id,
                    "text": None,
                    "text_origin": "ENGINE_OCR",
                    "text_alignment": "UNKNOWN",
                    "engine_confidence": None,
                    "token_ids": [],
                    "state": "AMBIGUOUS",
                    "candidates": [token_id for _, token_id in ranked],
                }
            )
            continue

        token = by_id[best_token]
        results.append(
            {
                "cell_id": cell_id,
                "text": token["text"],
                "text_origin": "ENGINE_OCR",
                "text_alignment": "GEOMETRIC_CANDIDATE",
                "engine_confidence": token["confidence"],
                "token_ids": [best_token],
                "state": "RESOLVED_TOKEN",
                "candidates": [token_id for _, token_id in ranked],
            }
        )

    # Tokens that ended up bound must never be shared: one token, one cell.
    bound: dict[str, str] = {}
    for cell in results:
        for token_id in cell["token_ids"]:
            if token_id in bound:
                raise _error(
                    f"token {token_id} was bound to both {bound[token_id]} and "
                    f"{cell['cell_id']}; a shared token means the geometry is wrong"
                )
            bound[token_id] = cell["cell_id"]

    return AlignmentResult(cells=results, issues=issues)
