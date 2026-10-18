"""PP-Chart2Table adapter (SDD 11.1, ``PI-F05-04``).

    "PP-Chart2Table的框架能力是图转表；它不是逐点证据、轴单位正确性或银行准确率保证。
     adapter读取其原始result，再分成axis/series/period/value候选，回到原图证据交叉核对。"

So this module only *splits* the engine result into candidates. It never mints a
``ChartPoint`` with an exact value: a number is promoted only when the caller says
which evidence verified it (:func:`to_chart_points`), and promotion happens in
``vision.chart`` where the value-type rules live. A model number that nobody
checked stays a candidate with ``exact_value = None``.

Field names are taken from the locked-version description and have **not** been
confirmed against a real response in this repository; every output carries
``engine_lock_note`` saying so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

CHART_LOCK_NOTE = (
    "candidate field names follow the locked PP-Chart2Table description and have NOT "
    "been confirmed against a golden raw response in this repository (L06/T606 NOT_RUN)"
)

__all__ = [
    "CHART_LOCK_NOTE",
    "ChartCandidate",
    "ChartCandidateResult",
    "read_chart_paddle",
    "to_chart_points",
]


def _drift(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_ENGINE_SCHEMA_DRIFT", message, detail=detail or None)


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class ChartCandidate:
    """One axis/series/period/value cell, kept as evidence rather than as truth."""

    point_id: str
    series: str | None
    period: str | None
    axis_id: str | None
    unit_raw: str | None
    raw_value: str | None
    numeric_coercion: bool
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "series": self.series,
            "period": self.period,
            "axis_id": self.axis_id,
            "unit_raw": self.unit_raw,
            "raw_value": self.raw_value,
            "numeric_coercion": self.numeric_coercion,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ChartCandidateResult:
    chart_type: str | None
    axes: list[dict[str, Any]]
    candidates: list[ChartCandidate]
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_type": self.chart_type,
            "axes": [dict(axis) for axis in self.axes],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "issues": [dict(issue) for issue in self.issues],
            "engine_lock_note": CHART_LOCK_NOTE,
        }


def _text(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _drift(f"{name} must be a string or null, got {type(value).__name__}")
    return value


def _sequence(value: Any, *, name: str) -> Sequence:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _drift(f"{name} must be a list, got {type(value).__name__}")
    return value


def read_chart_paddle(raw: Mapping[str, Any], *, page_no: int = 1) -> ChartCandidateResult:
    """Split a captured PP-Chart2Table result into axis/series/period/value candidates."""
    if not isinstance(raw, Mapping):
        raise _input_error(f"raw result must be a mapping, got {type(raw).__name__}")
    if isinstance(page_no, bool) or not isinstance(page_no, int) or page_no < 1:
        raise _input_error(f"page_no must be a 1-based integer, got {page_no!r}")
    if "series" not in raw and "y_axes" not in raw:
        raise _drift(
            "result has neither 'series' nor 'y_axes'; an empty chart and a renamed "
            "schema look identical"
        )

    chart_type = _text(raw.get("chart_type"), name="chart_type")
    units_by_axis: dict[str, str | None] = {}
    axes: list[dict[str, Any]] = []
    for index, axis in enumerate(_sequence(raw.get("y_axes"), name="y_axes")):
        if not isinstance(axis, Mapping):
            raise _drift(f"y_axes[{index}] must be an object")
        axis_id = axis.get("axis_id") or f"AX-Y{index}"
        if not isinstance(axis_id, str) or not axis_id:
            raise _drift(f"y_axes[{index}].axis_id must be a non-empty string")
        unit = _text(axis.get("unit"), name=f"y_axes[{index}].unit")
        units_by_axis[axis_id] = unit
        axes.append(
            {
                "axis_id": axis_id,
                "label": _text(axis.get("label"), name=f"y_axes[{index}].label"),
                "unit_raw": unit,
                "scale_type": axis.get("scale_type") or "UNKNOWN",
                "tick_labels": [
                    str(tick) for tick in _sequence(axis.get("ticks"), name=f"y_axes[{index}].ticks")
                ],
                "evidence_ids": [],
            }
        )

    x_axis = raw.get("x_axis")
    periods: list[str] = []
    if x_axis is not None:
        if not isinstance(x_axis, Mapping):
            raise _drift("x_axis must be an object")
        periods = [
            str(tick) for tick in _sequence(x_axis.get("ticks"), name="x_axis.ticks")
        ]

    issues: list[dict[str, Any]] = []
    candidates: list[ChartCandidate] = []
    for series_index, series in enumerate(_sequence(raw.get("series"), name="series")):
        if not isinstance(series, Mapping):
            raise _drift(f"series[{series_index}] must be an object")
        name = _text(series.get("name"), name=f"series[{series_index}].name")
        axis_id = series.get("axis_id")
        if axis_id is not None:
            if not isinstance(axis_id, str) or axis_id not in units_by_axis:
                raise _drift(
                    f"series[{series_index}].axis_id = {axis_id!r} names no declared axis"
                )
        values = _sequence(series.get("values"), name=f"series[{series_index}].values")
        series_periods = [
            str(tick)
            for tick in _sequence(
                series.get("periods"), name=f"series[{series_index}].periods"
            )
        ] or periods
        if series_periods and len(series_periods) != len(values):
            raise _drift(
                f"series[{series_index}] has {len(values)} values for "
                f"{len(series_periods)} periods; padding or truncating would invent data"
            )

        for position, value in enumerate(values):
            period = series_periods[position] if position < len(series_periods) else None
            coercion = False
            if value is None:
                raw_value = None
            elif isinstance(value, str):
                raw_value = value
            elif isinstance(value, bool):
                raise _drift(f"series[{series_index}].values[{position}] is a bool")
            elif isinstance(value, (int, float)):
                raw_value = str(value)
                coercion = True
            else:
                raise _drift(
                    f"series[{series_index}].values[{position}] has unsupported type "
                    f"{type(value).__name__}"
                )
            if coercion:
                issues.append(
                    {
                        "code": "NUMERIC_COERCION",
                        "point_id": f"CH-{series_index}-{position}",
                        "message": (
                            "engine returned a JSON number; it is kept as text and may never "
                            "be promoted to an exact value without label/table verification"
                        ),
                    }
                )
            candidates.append(
                ChartCandidate(
                    point_id=f"CH-{series_index}-{position}",
                    series=name,
                    period=period,
                    axis_id=axis_id if isinstance(axis_id, str) else None,
                    unit_raw=units_by_axis.get(axis_id) if isinstance(axis_id, str) else None,
                    raw_value=raw_value,
                    numeric_coercion=coercion,
                )
            )

    return ChartCandidateResult(
        chart_type=chart_type, axes=axes, candidates=candidates, issues=issues
    )


def to_chart_points(
    result: ChartCandidateResult,
    *,
    verified_labels: Mapping[str, str] | None = None,
    source_table_cells: Mapping[str, str] | None = None,
    label_evidence: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """Promote candidates **only** where the caller supplies verification.

    ``verified_labels`` maps ``point_id -> decimal string`` the OCR/human check
    actually read off the image. ``source_table_cells`` maps
    ``point_id -> source cell id``. Everything else becomes ``UNKNOWN`` with
    ``exact_value = None`` — the number stays in the candidate pool, not in the
    chart.
    """
    verified_labels = dict(verified_labels or {})
    source_table_cells = dict(source_table_cells or {})
    label_evidence = dict(label_evidence or {})

    points: list[dict[str, Any]] = []
    for candidate in result.candidates:
        point_id = candidate.point_id
        evidence = list(label_evidence.get(point_id, ()))
        if point_id in source_table_cells:
            cell = source_table_cells[point_id]
            if not isinstance(cell, str) or not cell:
                raise _input_error(
                    f"point {point_id}: source_table_cells must map to a cell id"
                )
            exact = verified_labels.get(point_id, candidate.raw_value)
            points.append(
                {
                    "point_id": point_id,
                    "series": candidate.series,
                    "period": candidate.period,
                    "axis_id": candidate.axis_id,
                    "unit_raw": candidate.unit_raw,
                    "value_type": "SOURCE_TABLE",
                    "exact_value": exact,
                    "estimated_value": None,
                    "evidence_ids": evidence,
                    "source_table_cell_id": cell,
                }
            )
            continue
        if point_id in verified_labels:
            points.append(
                {
                    "point_id": point_id,
                    "series": candidate.series,
                    "period": candidate.period,
                    "axis_id": candidate.axis_id,
                    "unit_raw": candidate.unit_raw,
                    "value_type": "PRINTED_LABEL",
                    "exact_value": verified_labels[point_id],
                    "estimated_value": None,
                    "evidence_ids": evidence,
                    "source_table_cell_id": None,
                    "label_verified": True,
                }
            )
            continue
        points.append(
            {
                "point_id": point_id,
                "series": candidate.series,
                "period": candidate.period,
                "axis_id": candidate.axis_id,
                "unit_raw": candidate.unit_raw,
                "value_type": "UNKNOWN",
                "exact_value": None,
                "estimated_value": None,
                "evidence_ids": evidence,
                "source_table_cell_id": None,
            }
        )
    return points
