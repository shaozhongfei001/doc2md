"""T501 / ``PI-F04-06``: header paths must be cell references, and ambiguity blocks.

The negative cases are the point of this file. A header implementation that
"usually works" is worse than one that refuses, because a wrong header silently
becomes a business field downstream (SDD 9.1: "不能把未核路径当有效业务字段").
"""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.tables.semantics import SEMANTICS_CODE, derive_header_paths


def cell(
    cell_id: str,
    row: int,
    col: int,
    *,
    rowspan: int = 1,
    colspan: int = 1,
    text: str = "",
) -> dict:
    return {
        "cell_id": cell_id,
        "row": row,
        "col": col,
        "rowspan": rowspan,
        "colspan": colspan,
        "text": text,
    }


def table(cells: list[dict], n_rows: int, n_cols: int) -> dict:
    return {"table_id": "T1", "n_rows": n_rows, "n_cols": n_cols, "cells": cells}


def by_cell(result) -> dict[str, dict]:
    return {item["data_cell_id"]: item for item in result.cell_semantics}


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


# ------------------------------------------------------------------ happy path
def test_single_header_row_yields_cell_reference_paths():
    result = derive_header_paths(
        table(
            [
                cell("h0", 0, 0, text="金额"),
                cell("h1", 0, 1, text="比例"),
                cell("d0", 1, 0, text="1,000"),
                cell("d1", 1, 1, text="12%"),
            ],
            2,
            2,
        ),
        header_rows=[0],
    )
    semantics = by_cell(result)
    assert set(semantics) == {"d0", "d1"}
    assert semantics["d0"]["column_header_cell_ids"] == ["h0"]
    assert semantics["d1"]["column_header_cell_ids"] == ["h1"]
    assert semantics["d0"]["row_header_cell_ids"] == []
    assert semantics["d0"]["state"] == "CANDIDATE"
    assert result.issues == []
    # A path is a reference, never a position: nothing here looks like "row 0".
    for item in result.cell_semantics:
        assert all(isinstance(pid, str) and pid.startswith("h") for pid in item["column_header_cell_ids"])


def test_two_level_header_is_outer_to_inner():
    result = derive_header_paths(
        table(
            [
                cell("g", 0, 0, colspan=2, text="金额"),
                cell("h0", 1, 0, text="人民币"),
                cell("h1", 1, 1, text="外币"),
                cell("d0", 2, 0, text="100"),
                cell("d1", 2, 1, text="20"),
            ],
            3,
            2,
        ),
        header_rows=[0, 1],
    )
    semantics = by_cell(result)
    assert semantics["d0"]["column_header_cell_ids"] == ["g", "h0"]
    assert semantics["d1"]["column_header_cell_ids"] == ["g", "h1"]
    assert semantics["d0"]["state"] == "CANDIDATE"


def test_same_leaf_label_under_different_branches_stays_distinct():
    # SDD 9.1.3: 人民币/期限 and 外币/期限 must not collapse into one "期限".
    result = derive_header_paths(
        table(
            [
                cell("g0", 0, 0, text="人民币"),
                cell("g1", 0, 1, text="外币"),
                cell("h0", 1, 0, text="期限"),
                cell("h1", 1, 1, text="期限"),
                cell("d0", 2, 0, text="3年"),
                cell("d1", 2, 1, text="1年"),
            ],
            3,
            2,
        ),
        header_rows=[0, 1],
    )
    semantics = by_cell(result)
    assert semantics["d0"]["column_header_cell_ids"] == ["g0", "h0"]
    assert semantics["d1"]["column_header_cell_ids"] == ["g1", "h1"]
    assert semantics["d0"]["column_header_cell_ids"] != semantics["d1"]["column_header_cell_ids"]


def test_three_level_header_keeps_full_chain():
    result = derive_header_paths(
        table(
            [
                cell("a", 0, 0, text="一级"),
                cell("b", 1, 0, text="二级"),
                cell("c", 2, 0, text="三级"),
                cell("d", 3, 0, text="值"),
            ],
            4,
            1,
        ),
        header_rows=[0, 1, 2],
    )
    assert by_cell(result)["d"]["column_header_cell_ids"] == ["a", "b", "c"]


def test_data_cell_under_merged_header_uses_the_covering_anchor():
    result = derive_header_paths(
        table(
            [cell("g", 0, 0, colspan=2, text="合计"), cell("d", 1, 0, colspan=2, text="200")],
            2,
            2,
        ),
        header_rows=[0],
    )
    assert by_cell(result)["d"]["column_header_cell_ids"] == ["g"]


def test_row_header_path_is_collected_left_to_right():
    result = derive_header_paths(
        table(
            [
                cell("c00", 0, 0, text="项目"),
                cell("r10", 1, 0, text="本金"),
                cell("d", 1, 1, text="100"),
            ],
            2,
            2,
        ),
        header_cols=[0],
    )
    semantics = by_cell(result)
    assert semantics["d"]["row_header_cell_ids"] == ["r10"]
    assert semantics["d"]["column_header_cell_ids"] == []
    assert semantics["d"]["state"] == "CANDIDATE"


def test_both_axes_are_reported_for_a_corner_data_cell():
    result = derive_header_paths(
        table(
            [
                cell("c00", 0, 0, text="项目"),
                cell("c01", 0, 1, text="金额"),
                cell("r10", 1, 0, text="本金"),
                cell("d", 1, 1, text="100"),
            ],
            2,
            2,
        ),
        header_rows=[0],
        header_cols=[0],
    )
    semantics = by_cell(result)
    assert semantics["d"]["column_header_cell_ids"] == ["c01"]
    assert semantics["d"]["row_header_cell_ids"] == ["r10"]


# ------------------------------------------------- unit / caption stay context
def test_unit_and_caption_live_in_context_not_in_path():
    result = derive_header_paths(
        table([cell("h0", 0, 0, text="金额"), cell("d0", 1, 0, text="100")], 2, 1),
        header_rows=[0],
        context={"caption": "表1 单位：万元", "unit_raw": "万元", "period": "2025H1"},
    )
    item = by_cell(result)["d0"]
    assert item["unit_raw"] == "万元"
    assert item["column_header_cell_ids"] == ["h0"]
    assert result.context["caption"] == "表1 单位：万元"
    # The caption/unit never leaks into the reference path.
    assert "万元" not in str(item["column_header_cell_ids"])


def test_unit_is_null_when_absent_and_never_inferred():
    result = derive_header_paths(
        table([cell("h0", 0, 0, text="金额"), cell("d0", 1, 0, text="100")], 2, 1),
        header_rows=[0],
    )
    assert by_cell(result)["d0"]["unit_raw"] is None


def test_currency_header_branch_is_not_mistaken_for_a_unit():
    # "人民币" is legitimate header evidence (SDD 9.1.3); treating it as the unit
    # would both corrupt the path and invent a unit that was never stated.
    result = derive_header_paths(
        table([cell("h0", 0, 0, text="人民币"), cell("d0", 1, 0, text="100")], 2, 1),
        header_rows=[0],
    )
    item = by_cell(result)["d0"]
    assert item["column_header_cell_ids"] == ["h0"]
    assert item["unit_raw"] is None


# ------------------------------------------------------- fail-closed negatives
def test_without_native_header_band_nothing_is_guessed():
    result = derive_header_paths(
        table([cell("h0", 0, 0, text="金额"), cell("d0", 1, 0, text="100")], 2, 1)
    )
    assert "HEADER_BAND_UNKNOWN" in codes(result)
    item = by_cell(result)["d0"]
    assert item["column_header_cell_ids"] == []
    assert item["state"] == "NEEDS_REVIEW"


def test_partial_header_coverage_is_flagged_not_guessed():
    result = derive_header_paths(
        table(
            [
                cell("h0", 0, 0, colspan=2, text="合计"),
                cell("d", 1, 1, colspan=2, text="200"),
            ],
            2,
            3,
        ),
        header_rows=[0],
    )
    item = by_cell(result)["d"]
    assert "HEADER_COVERAGE_PARTIAL" in codes(result)
    assert item["column_header_cell_ids"] == []
    assert item["state"] == "NEEDS_REVIEW"


def test_missing_header_for_a_column_interval_is_incomplete():
    result = derive_header_paths(
        table(
            [cell("h0", 0, 0, text="金额"), cell("d", 1, 2, text="200")],
            2,
            3,
        ),
        header_rows=[0],
    )
    item = by_cell(result)["d"]
    assert "HEADER_INCOMPLETE" in codes(result)
    assert item["state"] == "NEEDS_REVIEW"


def test_empty_header_without_outer_owner_blocks_the_cell():
    result = derive_header_paths(
        table([cell("h0", 0, 0, text="  "), cell("d0", 1, 0, text="100")], 2, 1),
        header_rows=[0],
    )
    assert "EMPTY_HEADER_ANCHOR" in codes(result)
    assert "EMPTY_HEADER_IN_PATH" in codes(result)
    assert by_cell(result)["d0"]["state"] == "NEEDS_REVIEW"


def test_cell_straddling_band_and_body_is_not_a_header():
    result = derive_header_paths(
        table(
            [
                cell("s", 0, 0, rowspan=2, text="合并"),
                cell("h1", 0, 1, text="比例"),
                cell("d", 1, 1, text="12%"),
            ],
            2,
            2,
        ),
        header_rows=[0],
    )
    assert "HEADER_BAND_STRADDLED" in codes(result)
    assert "s" not in by_cell(result)


def test_repeated_label_on_two_levels_is_ambiguous():
    result = derive_header_paths(
        table(
            [cell("r0", 0, 0, text="金额"), cell("r1", 1, 0, text="金额"), cell("d", 2, 0, text="1")],
            3,
            1,
        ),
        header_rows=[0, 1],
    )
    item = by_cell(result)["d"]
    assert "DUPLICATE_LEVEL_LABEL" in codes(result)
    assert item["state"] == "NEEDS_REVIEW"


# ------------------------------------------------------------ review handoff
def test_review_decision_is_the_only_route_to_confirmed():
    base = table([cell("h0", 0, 0, text="金额"), cell("d0", 1, 0, text="100")], 2, 1)
    without = by_cell(derive_header_paths(base, header_rows=[0]))["d0"]
    with_ref = by_cell(
        derive_header_paths(base, header_rows=[0], review_decision_ref="RV-0001")
    )["d0"]
    assert without["state"] == "CANDIDATE" and without["review_decision_ref"] is None
    assert with_ref["state"] == "CONFIRMED" and with_ref["review_decision_ref"] == "RV-0001"


# -------------------------------------------------------------- input refusals
@pytest.mark.parametrize(
    "payload, match",
    [
        (table([cell("a", 0, 0), cell("b", 0, 0)], 1, 1), "overlap"),
        (table([cell("a", 0, 0, colspan=3)], 1, 2), "leaves the"),
        ({"table_id": "T", "n_rows": 0, "n_cols": 1, "cells": []}, "positive integers"),
        ({"table_id": "T", "n_rows": 1, "n_cols": 1, "cells": ["a"]}, "must be a mapping"),
        ({"table_id": "T", "n_rows": 1, "n_cols": 1, "cells": [{"cell_id": "a", "row": "0", "col": 0, "rowspan": 1, "colspan": 1}]}, "must be integers"),
    ],
)
def test_structurally_impossible_tables_are_refused(payload, match):
    with pytest.raises(ExtensionError) as excinfo:
        derive_header_paths(payload, header_rows=[0])
    assert excinfo.value.code == SEMANTICS_CODE
    assert match in str(excinfo.value)


def test_header_band_outside_the_grid_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        derive_header_paths(
            table([cell("h0", 0, 0, text="金额"), cell("d0", 1, 0, text="1")], 2, 1),
            header_rows=[5],
        )
    assert excinfo.value.code == SEMANTICS_CODE
    assert "outside" in str(excinfo.value)
