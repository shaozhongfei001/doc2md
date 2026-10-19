"""L49: the engine suggests header rows; only a human declaration makes them real."""

from __future__ import annotations

import json
import pathlib

import pytest

from app.extensions import ExtensionError
from app.extensions.adapters.ocr_tables import (
    HeaderSuggestion,
    build_ocr_table,
    declare_header_rows,
    suggest_header_rows,
)
from app.extensions.adapters.paddle_structure import normalise_response

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ppstructure_v370_table_cjk.json"


def table_cells(tags=("th", "td", "td"), *, cols=2):
    cells = []
    for row, tag in enumerate(tags):
        for col in range(cols):
            cells.append(
                {
                    "cell_id": f"P0-R{row:04d}C{col:04d}",
                    "row": row,
                    "col": col,
                    "rowspan": 1,
                    "colspan": 1,
                    "text": f"{row}-{col}",
                    "text_origin": "ENGINE_OCR",
                    "html_tag": tag,
                }
            )
    return cells


def declare(rows=(0,), **overrides):
    kwargs = {"rows": rows, "reviewer": "E12345", "review_decision_ref": "RV-1"}
    kwargs.update(overrides)
    return declare_header_rows(table_cells(), **kwargs)


def test_a_th_row_becomes_a_suggestion():
    assert suggest_header_rows(table_cells()).rows == (0,)


def test_without_th_there_is_no_suggestion_rather_than_a_guess():
    """No markup means no answer. "First row looks like a header" is the guess L05 banned."""
    suggestion = suggest_header_rows(table_cells(("td", "td", "td")))
    assert suggestion.rows == ()
    assert suggestion.available is False


def test_a_partial_th_row_is_not_a_header_band():
    cells = table_cells(("td", "td", "td"))
    cells[0]["html_tag"] = "th"  # only one cell of row 0
    assert suggest_header_rows(cells).rows == ()


def test_building_without_a_declaration_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_ocr_table("T-1", table_cells(), declaration=None)
    assert excinfo.value.code == "E_REVIEW_REQUIRED"
    assert "human declaration" in str(excinfo.value)


def test_a_declaration_must_be_a_contiguous_prefix():
    for rows in ((1,), (0, 2), (2, 3)):
        with pytest.raises(ExtensionError) as excinfo:
            declare(rows)
        assert excinfo.value.code == "E_INPUT_INVALID", rows


def test_a_declaration_must_leave_a_data_row():
    with pytest.raises(ExtensionError) as excinfo:
        declare((0, 1, 2))
    assert "no data rows" in str(excinfo.value)


def test_the_payload_carries_slots_cells_and_the_source():
    table = build_ocr_table("T-1", table_cells(), declaration=declare())
    payload = table.to_mapping()

    assert payload["header_rows"] == [0]
    assert payload["header_source"] == "HUMAN_DECLARATION"
    assert payload["n_rows"] == 3 and payload["n_cols"] == 2
    assert len(payload["slots"]) == 3 and all(len(row) == 2 for row in payload["slots"])
    assert len(payload["cells"]) == 6
    assert payload["header_overridden"] is False


def test_a_human_override_is_allowed_and_recorded():
    """The human may correct the engine; the audit keeps both answers."""
    cells = table_cells(("td", "td", "td"))  # engine suggests nothing
    table = build_ocr_table(
        "T-1", cells, declaration=declare((0,), reviewer="E12345")
    )
    assert table.overridden is True
    assert [issue["code"] for issue in table.issues] == ["HEADER_OVERRIDES_ENGINE"]
    assert table.issues[0]["suggested"] == [] and table.issues[0]["declared"] == [0]


def test_the_real_fixture_produces_a_payload_with_slots():
    response = json.loads(FIXTURE.read_text(encoding="utf-8"))
    table = normalise_response(response, cell_box_order="GEOMETRY").tables[0]
    declared = declare_header_rows(
        table.cells, rows=[0], reviewer="E12345", review_decision_ref="RV-1"
    )
    built = build_ocr_table("T-REAL", table.cells, declaration=declared)

    assert built.n_rows == 5 and built.n_cols == 4
    assert len(built.slots) == 5
    assert built.header_rows == (0,)
