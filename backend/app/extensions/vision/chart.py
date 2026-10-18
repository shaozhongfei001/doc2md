"""Statistical chart values with value-type discipline (SDD 11.1, ``PI-F05-04``).

The whole module exists to prevent one specific failure: a number that *looks*
precise entering a financial calculation. SDD says it directly —

    "图转表模型给出的数字即使格式完整，也不能未经可见标签或原表核对就标PRINTED_LABEL"

so every point must declare *why* it is trusted:

* ``SOURCE_TABLE``  — it came from the same-version source table cell (exact)
* ``PRINTED_LABEL`` — a visible label was located and checked (exact)
* ``ESTIMATED``     — allowed by type, but ``exact_value`` **must** be null
* ``TREND_ONLY``    — direction only, ``exact_value`` null
* ``UNKNOWN``       — axis/unit/series undetermined, ``exact_value`` null

Consequences enforced here, not documented elsewhere:

* A ``PRINTED_LABEL`` claim without verification evidence is **demoted to
  UNKNOWN** with an issue. Demotion keeps the point (and its evidence) instead of
  raising, because throwing the chart away hides the problem from review.
* Values are decimal *strings* matching the contract pattern — never floats, so
  ``0.1 + 0.2`` problems cannot be introduced at this layer.
* Money and percentages are never converted into each other; ``unit_raw`` is
  copied verbatim and the two units are compared, not normalised.
* When a chart value disagrees with the source table, **both** are kept and a
  CONFLICT is reported. The table is never overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .. import ExtensionError

VALUE_TYPES = ("PRINTED_LABEL", "SOURCE_TABLE", "ESTIMATED", "TREND_ONLY", "UNKNOWN")
SCALE_TYPES = ("LINEAR", "LOG", "CATEGORY", "UNKNOWN")

_DECIMAL = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")

__all__ = [
    "SCALE_TYPES",
    "VALUE_TYPES",
    "ChartResult",
    "audit_chart",
    "build_chart",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class ChartIssue:
    code: str
    message: str
    point_id: str | None = None
    axis_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.point_id is not None:
            payload["point_id"] = self.point_id
        if self.axis_id is not None:
            payload["axis_id"] = self.axis_id
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload


@dataclass(frozen=True)
class ChartResult:
    chart: dict[str, Any]
    issues: list[ChartIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart": dict(self.chart),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _decimal_text(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise _input_error(
            f"{name} must be a decimal string, got {type(value).__name__}; a float here "
            "would round silently"
        )
    if not _DECIMAL.match(value):
        raise _input_error(
            f"{name} = {value!r} is not a plain decimal string "
            "(-?(0|[1-9][0-9]*)(.[0-9]+)?); thousands separators, exponents and "
            "percent signs belong in unit_raw, not in the value"
        )
    try:
        Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - regex already guarantees this
        raise _input_error(f"{name} = {value!r} is not a decimal") from exc
    return value


def _ids(value: Any, *, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _input_error(f"{name} must be a sequence of ids")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise _input_error(f"{name} contains {item!r}")
        out.append(item)
    return out


def build_chart(
    *,
    axes: Sequence[Mapping[str, Any]],
    points: Sequence[Mapping[str, Any]],
    original_data_ref: str | None = None,
    source_table_values: Mapping[str, str] | None = None,
    flags: Mapping[str, bool] | None = None,
) -> ChartResult:
    """Validate axes/points into a contract ``Chart``, demoting unverified precision.

    ``source_table_values`` maps ``cell_id -> decimal string`` and is used **only**
    to detect disagreement with ``SOURCE_TABLE`` points; it never rewrites them.
    """
    axis_rows: list[dict[str, Any]] = []
    axis_ids: set[str] = set()
    for index, raw in enumerate(axes):
        if not isinstance(raw, Mapping):
            raise _input_error(f"axes[{index}] must be a mapping")
        axis_id = raw.get("axis_id")
        if not isinstance(axis_id, str) or not axis_id:
            raise _input_error(f"axes[{index}] has no usable axis_id")
        if axis_id in axis_ids:
            raise _input_error(f"duplicate axis_id {axis_id!r}")
        axis_ids.add(axis_id)
        scale = raw.get("scale_type", "UNKNOWN")
        if scale not in SCALE_TYPES:
            raise _input_error(
                f"axis {axis_id}: scale_type must be one of {list(SCALE_TYPES)}, got "
                f"{scale!r}; an unknown scale is UNKNOWN, not LINEAR"
            )
        ticks = raw.get("tick_labels") or []
        if isinstance(ticks, (str, bytes)) or not isinstance(ticks, Sequence):
            raise _input_error(f"axis {axis_id}: tick_labels must be a sequence")
        for tick in ticks:
            if not isinstance(tick, str):
                raise _input_error(f"axis {axis_id}: tick_labels contains {tick!r}")
        label = raw.get("label")
        unit = raw.get("unit_raw")
        for name, value in (("label", label), ("unit_raw", unit)):
            if value is not None and not isinstance(value, str):
                raise _input_error(f"axis {axis_id}: {name} must be a string or null")
        axis_rows.append(
            {
                "axis_id": axis_id,
                "label": label,
                "unit_raw": unit,
                "scale_type": scale,
                "tick_labels": list(ticks),
                "evidence_ids": _ids(raw.get("evidence_ids"), name=f"axis {axis_id}.evidence_ids"),
            }
        )

    issues: list[ChartIssue] = []
    point_rows: list[dict[str, Any]] = []
    seen_points: set[str] = set()

    for index, raw in enumerate(points):
        if not isinstance(raw, Mapping):
            raise _input_error(f"points[{index}] must be a mapping")
        point_id = raw.get("point_id")
        if not isinstance(point_id, str) or not point_id:
            raise _input_error(f"points[{index}] has no usable point_id")
        if point_id in seen_points:
            raise _input_error(f"duplicate point_id {point_id!r}")
        seen_points.add(point_id)

        value_type = raw.get("value_type")
        if value_type not in VALUE_TYPES:
            raise _input_error(
                f"point {point_id}: value_type must be one of {list(VALUE_TYPES)}, got "
                f"{value_type!r}"
            )

        axis_id = raw.get("axis_id")
        if axis_id is not None and axis_id not in axis_ids:
            raise _input_error(f"point {point_id}: unknown axis_id {axis_id!r}")

        exact = _decimal_text(raw.get("exact_value"), name=f"point {point_id}.exact_value")
        estimated = _decimal_text(
            raw.get("estimated_value"), name=f"point {point_id}.estimated_value"
        )
        source_cell = raw.get("source_table_cell_id")
        if source_cell is not None and not isinstance(source_cell, str):
            raise _input_error(
                f"point {point_id}: source_table_cell_id must be a string or null"
            )
        evidence = _ids(raw.get("evidence_ids"), name=f"point {point_id}.evidence_ids")
        label_verified = raw.get("label_verified")
        if label_verified is not None and not isinstance(label_verified, bool):
            raise _input_error(
                f"point {point_id}: label_verified must be an explicit bool when given"
            )

        # --- type/value coherence ------------------------------------------
        if value_type in ("ESTIMATED", "TREND_ONLY", "UNKNOWN") and exact is not None:
            raise _input_error(
                f"point {point_id}: {value_type} may not carry exact_value={exact!r}; that "
                "is the whole point of the type"
            )
        if value_type != "ESTIMATED" and estimated is not None:
            raise _input_error(
                f"point {point_id}: estimated_value requires value_type ESTIMATED"
            )
        if value_type == "SOURCE_TABLE" and not source_cell:
            raise _input_error(
                f"point {point_id}: SOURCE_TABLE requires the exact source_table_cell_id"
            )

        if value_type == "PRINTED_LABEL" and label_verified is not True:
            issues.append(
                ChartIssue(
                    code="PRINTED_LABEL_UNVERIFIED",
                    message=(
                        "a chart-to-table number may not be claimed as a printed label "
                        "without a located, checked label; demoted to UNKNOWN"
                    ),
                    point_id=point_id,
                    detail={"claimed_value": exact},
                )
            )
            value_type = "UNKNOWN"
            exact = None

        if value_type == "SOURCE_TABLE" and source_table_values is not None:
            table_value = source_table_values.get(str(source_cell))
            if table_value is not None and table_value != exact:
                issues.append(
                    ChartIssue(
                        code="CHART_TABLE_CONFLICT",
                        message=(
                            "chart value disagrees with the same-version source table; both "
                            "are kept and the table is not overwritten"
                        ),
                        point_id=point_id,
                        detail={
                            "chart_value": exact,
                            "table_value": table_value,
                            "source_table_cell_id": source_cell,
                        },
                    )
                )

        point_rows.append(
            {
                "point_id": point_id,
                "series": raw.get("series"),
                "period": raw.get("period"),
                "axis_id": axis_id,
                "unit_raw": raw.get("unit_raw"),
                "value_type": value_type,
                "exact_value": exact,
                "estimated_value": estimated,
                "evidence_ids": evidence,
                "source_table_cell_id": source_cell,
            }
        )

    chart = {
        "axes": axis_rows,
        "points": point_rows,
        "original_data_ref": original_data_ref,
    }
    issues.extend(audit_chart(chart, flags=flags))
    return ChartResult(chart=chart, issues=issues)


def audit_chart(
    chart: Mapping[str, Any], *, flags: Mapping[str, bool] | None = None
) -> list[ChartIssue]:
    """Axis/unit checks that must be *reported*, never silently normalised.

    SDD 11.1 lists them explicitly: truncated axis, log axis, dual axis, stacked
    series, missing ticks/legend, negatives, mixed units.
    """
    flags = dict(flags or {})
    issues: list[ChartIssue] = []
    axes = list(chart.get("axes") or ())
    points = list(chart.get("points") or ())

    for axis in axes:
        axis_id = axis.get("axis_id")
        if axis.get("scale_type") == "LOG":
            issues.append(
                ChartIssue(
                    code="LOG_AXIS",
                    message="log axis: distances are not proportional to values",
                    axis_id=axis_id,
                )
            )
        if not axis.get("tick_labels"):
            issues.append(
                ChartIssue(
                    code="MISSING_TICKS",
                    message="axis has no tick labels; values cannot be read off reliably",
                    axis_id=axis_id,
                )
            )

    if flags.get("axis_truncated"):
        issues.append(
            ChartIssue(
                code="AXIS_TRUNCATED",
                message="axis does not start at zero; bar heights do not encode ratios",
            )
        )
    if flags.get("dual_axis"):
        issues.append(
            ChartIssue(code="DUAL_AXIS", message="two value axes: series are not comparable")
        )
    if flags.get("stacked"):
        issues.append(
            ChartIssue(
                code="STACKED_SERIES",
                message="stacked series: a segment height is not a standalone value",
            )
        )
    if flags.get("missing_legend"):
        issues.append(
            ChartIssue(code="MISSING_LEGEND", message="no legend: series identity is unproven")
        )

    units = {point.get("unit_raw") for point in points if point.get("unit_raw")}
    if len(units) > 1:
        issues.append(
            ChartIssue(
                code="MIXED_UNITS",
                message="points carry different units; they are not converted here",
                detail={"units": sorted(units)},
            )
        )
    for point in points:
        exact = point.get("exact_value")
        if isinstance(exact, str) and exact.startswith("-"):
            issues.append(
                ChartIssue(
                    code="NEGATIVE_VALUE",
                    message="negative value present; axis handling must be checked by a human",
                    point_id=point.get("point_id"),
                )
            )
    return issues
