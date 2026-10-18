"""T604 / ``PI-F05-04``: a chart number is only as precise as its verification.

The single failure this file is built around: a chart-to-table model returns a
well-formatted number, someone marks it ``PRINTED_LABEL``, and it enters a
financial calculation with no label behind it.
"""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.adapters.chart_paddle import read_chart_paddle, to_chart_points
from app.extensions.vision.chart import build_chart


def axis(axis_id: str = "AX-Y0", **overrides) -> dict:
    payload = {
        "axis_id": axis_id,
        "label": "余额",
        "unit_raw": "万元",
        "scale_type": "LINEAR",
        "tick_labels": ["0", "100", "200"],
        "evidence_ids": ["EV-AXIS"],
    }
    payload.update(overrides)
    return payload


def point(point_id: str = "P1", **overrides) -> dict:
    payload = {
        "point_id": point_id,
        "series": "贷款",
        "period": "2025H1",
        "axis_id": "AX-Y0",
        "unit_raw": "万元",
        "value_type": "SOURCE_TABLE",
        "exact_value": "1234.5",
        "estimated_value": None,
        "evidence_ids": ["EV-CELL"],
        "source_table_cell_id": "T1-R0001C0001",
    }
    payload.update(overrides)
    return payload


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


# --------------------------------------------------------------- value types
def test_source_table_point_carries_the_cell_reference():
    result = build_chart(axes=[axis()], points=[point()])
    built = result.chart["points"][0]
    assert built["value_type"] == "SOURCE_TABLE"
    assert built["exact_value"] == "1234.5"
    assert built["source_table_cell_id"] == "T1-R0001C0001"
    assert result.issues == []


def test_source_table_without_cell_reference_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_chart(
            axes=[axis()],
            points=[point(source_table_cell_id=None)],
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "SOURCE_TABLE requires" in str(excinfo.value)


def test_unverified_printed_label_is_demoted_not_trusted():
    result = build_chart(
        axes=[axis()],
        points=[
            point(
                value_type="PRINTED_LABEL",
                exact_value="12.5",
                source_table_cell_id=None,
            )
        ],
    )
    built = result.chart["points"][0]
    assert built["value_type"] == "UNKNOWN"
    assert built["exact_value"] is None
    assert "PRINTED_LABEL_UNVERIFIED" in codes(result)


def test_verified_printed_label_keeps_its_value():
    result = build_chart(
        axes=[axis()],
        points=[
            point(
                value_type="PRINTED_LABEL",
                exact_value="12.5",
                source_table_cell_id=None,
                label_verified=True,
            )
        ],
    )
    assert result.chart["points"][0]["value_type"] == "PRINTED_LABEL"
    assert result.chart["points"][0]["exact_value"] == "12.5"


@pytest.mark.parametrize("value_type", ["ESTIMATED", "TREND_ONLY", "UNKNOWN"])
def test_estimate_trend_and_unknown_cannot_carry_an_exact_value(value_type):
    with pytest.raises(ExtensionError) as excinfo:
        build_chart(
            axes=[axis()],
            points=[point(value_type=value_type, exact_value="1.0", source_table_cell_id=None)],
        )
    assert "may not carry exact_value" in str(excinfo.value)


def test_estimated_value_requires_estimated_type():
    with pytest.raises(ExtensionError) as excinfo:
        build_chart(axes=[axis()], points=[point(estimated_value="9.9")])
    assert "requires value_type ESTIMATED" in str(excinfo.value)


@pytest.mark.parametrize("bad", [1.5, "1,234", "12%", "1e3", 5])
def test_values_must_be_plain_decimal_strings(bad):
    with pytest.raises(ExtensionError) as excinfo:
        build_chart(axes=[axis()], points=[point(exact_value=bad)])
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_unknown_axis_reference_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_chart(axes=[axis()], points=[point(axis_id="AX-MISSING")])
    assert "unknown axis_id" in str(excinfo.value)


# ------------------------------------------------------------------- audits
def test_log_axis_is_reported_rather_than_normalised():
    result = build_chart(axes=[axis(scale_type="LOG")], points=[point()])
    assert "LOG_AXIS" in codes(result)


def test_missing_ticks_are_reported():
    result = build_chart(axes=[axis(tick_labels=[])], points=[point()])
    assert "MISSING_TICKS" in codes(result)


def test_mixed_units_are_reported_not_converted():
    result = build_chart(
        axes=[axis()],
        points=[point("P1"), point("P2", unit_raw="元", exact_value="99")],
    )
    issue = next(i for i in result.issues if i.code == "MIXED_UNITS")
    assert issue.detail["units"] == ["万元", "元"]
    # The values themselves are untouched: no conversion happened.
    assert [p["exact_value"] for p in result.chart["points"]] == ["1234.5", "99"]


def test_negative_values_are_flagged_for_axis_check():
    result = build_chart(axes=[axis()], points=[point(exact_value="-5")])
    assert "NEGATIVE_VALUE" in codes(result)


def test_truncated_dual_stacked_and_legendless_charts_are_flagged():
    result = build_chart(
        axes=[axis()],
        points=[point()],
        flags={"axis_truncated": True, "dual_axis": True, "stacked": True, "missing_legend": True},
    )
    assert {"AXIS_TRUNCATED", "DUAL_AXIS", "STACKED_SERIES", "MISSING_LEGEND"} <= set(codes(result))


def test_chart_table_conflict_keeps_both_sides_and_does_not_overwrite():
    result = build_chart(
        axes=[axis()],
        points=[point()],
        source_table_values={"T1-R0001C0001": "1200"},
    )
    built = result.chart["points"][0]
    assert built["exact_value"] == "1234.5"  # chart side untouched
    issue = next(i for i in result.issues if i.code == "CHART_TABLE_CONFLICT")
    assert issue.detail["chart_value"] == "1234.5"
    assert issue.detail["table_value"] == "1200"


# ------------------------------------------------------- chart_paddle adapter
def raw_chart(**overrides) -> dict:
    payload = {
        "chart_type": "bar",
        "x_axis": {"ticks": ["2025H1", "2025H2"]},
        "y_axes": [{"axis_id": "AX-Y0", "label": "余额", "unit": "万元", "ticks": ["0", "100"]}],
        "series": [{"name": "贷款", "axis_id": "AX-Y0", "values": ["100", "120"]}],
    }
    payload.update(overrides)
    return payload


def test_adapter_splits_series_period_and_value():
    result = read_chart_paddle(raw_chart())
    candidates = result.to_dict()["candidates"]
    assert [c["raw_value"] for c in candidates] == ["100", "120"]
    assert [c["period"] for c in candidates] == ["2025H1", "2025H2"]
    assert candidates[0]["unit_raw"] == "万元"


def test_adapter_period_value_length_mismatch_is_drift():
    payload = raw_chart()
    payload["series"][0]["values"] = ["100"]
    with pytest.raises(ExtensionError) as excinfo:
        read_chart_paddle(payload)
    assert excinfo.value.code == "E_ENGINE_SCHEMA_DRIFT"
    assert "invent data" in str(excinfo.value)


def test_adapter_marks_numeric_coercion_instead_of_hiding_it():
    payload = raw_chart()
    payload["series"][0]["values"] = [100, 120]
    result = read_chart_paddle(payload)
    assert all(candidate.numeric_coercion for candidate in result.candidates)
    assert "NUMERIC_COERCION" in [issue["code"] for issue in result.issues]


def test_adapter_rejects_an_undeclared_axis():
    payload = raw_chart()
    payload["series"][0]["axis_id"] = "AX-Y9"
    with pytest.raises(ExtensionError) as excinfo:
        read_chart_paddle(payload)
    assert "names no declared axis" in str(excinfo.value)


def test_adapter_requires_a_series_or_axis_list():
    with pytest.raises(ExtensionError) as excinfo:
        read_chart_paddle({"chart_type": "bar"})
    assert excinfo.value.code == "E_ENGINE_SCHEMA_DRIFT"


def test_unverified_model_numbers_never_become_exact_values():
    result = read_chart_paddle(raw_chart())
    points = to_chart_points(result)
    assert [p["value_type"] for p in points] == ["UNKNOWN", "UNKNOWN"]
    assert all(p["exact_value"] is None for p in points)


def test_verified_label_promotes_to_printed_label():
    result = read_chart_paddle(raw_chart())
    points = to_chart_points(
        result,
        verified_labels={"CH-0-0": "100"},
        label_evidence={"CH-0-0": ["EV-LABEL"]},
    )
    assert points[0]["value_type"] == "PRINTED_LABEL"
    assert points[0]["exact_value"] == "100"
    assert points[0]["evidence_ids"] == ["EV-LABEL"]
    assert points[1]["value_type"] == "UNKNOWN"


def test_source_table_promotion_requires_and_keeps_the_cell():
    result = read_chart_paddle(raw_chart())
    points = to_chart_points(
        result, source_table_cells={"CH-0-1": "T9-R0002C0003"}
    )
    assert points[1]["value_type"] == "SOURCE_TABLE"
    assert points[1]["source_table_cell_id"] == "T9-R0002C0003"
    assert points[1]["exact_value"] == "120"


# ------------------------------------------------- root-contract integration
def _chart_model():
    """The published ``Chart`` contract, loaded from the adopted work copy.

    Table-level tests can pass while the root contract rejects the same payload —
    that was ``L03-GAP-06``. Chart is nested inside ``Visual``, so the only honest
    check is to validate against the published model itself.
    """
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    contract_dir = repo / "contracts" / "doc2md-19cap" / "v1.0.0"
    if str(contract_dir) not in sys.path:
        sys.path.insert(0, str(contract_dir))
    import contract_models

    return contract_models.Chart


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"verified": True},
        {"source_cell": True},
    ],
)
def test_full_chain_output_is_accepted_by_the_published_chart_contract(kwargs):
    chart_model = _chart_model()
    result = read_chart_paddle(raw_chart())
    if kwargs.get("verified"):
        points = to_chart_points(
            result, verified_labels={"CH-0-0": "100"}, label_evidence={"CH-0-0": ["EV-L"]}
        )
    elif kwargs.get("source_cell"):
        points = to_chart_points(result, source_table_cells={"CH-0-0": "T1-R0001C0001"})
    else:
        points = to_chart_points(result)
    built = build_chart(axes=result.axes, points=points)
    chart_model.model_validate(built.chart)  # raises on any drift
