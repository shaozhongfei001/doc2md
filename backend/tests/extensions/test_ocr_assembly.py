"""L50: an OCR assembly entry that says what it does not know."""

from __future__ import annotations

import json
import pathlib

import pytest

from app.extensions import ExtensionError
from app.extensions.adapters.ocr_assembly import (
    EVIDENCE_UNAVAILABLE,
    NOTES_UNAVAILABLE,
    OMISSION_UNAVAILABLE,
    assemble_from_ocr,
)
from app.extensions.adapters.ocr_tables import build_ocr_table, declare_header_rows
from app.extensions.adapters.paddle_structure import normalise_response

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ppstructure_v370_table_cjk.json"


def real_table():
    response = json.loads(FIXTURE.read_text(encoding="utf-8"))
    table = normalise_response(response, cell_box_order="GEOMETRY").tables[0]
    declaration = declare_header_rows(
        table.cells, rows=[0], reviewer="E12345", review_decision_ref="RV-1"
    )
    return build_ocr_table("T-REAL", table.cells, declaration=declaration)


def assembly(tables=None):
    return assemble_from_ocr(
        [real_table()] if tables is None else tables,
        generation_id="GEN-1",
        source_sha256="a" * 64,
    )


def codes(result):
    return [issue["code"] for issue in result.issues]


def test_the_ocr_table_reaches_contract_shape():
    result = assembly()
    table = result.tables[0]

    assert table["table_id"] == "T-REAL"
    assert (table["n_rows"], table["n_cols"]) == (5, 4)
    assert len(table["slots"]) == 5 and all(len(row) == 4 for row in table["slots"])
    assert len(table["cells"]) == 20
    assert result.stats["header_rows_by_table"] == {"T-REAL": [0]}


def test_an_empty_note_list_always_comes_with_an_issue():
    """Empty alone would read as "no footnotes"; the issue says "not extracted"."""
    result = assembly()
    assert result.notes == []
    assert NOTES_UNAVAILABLE in codes(result)
    assert result.stats["notes_unavailable"] is True


def test_omission_and_evidence_are_declared_missing_not_faked():
    result = assembly()
    assert OMISSION_UNAVAILABLE in codes(result)
    assert EVIDENCE_UNAVAILABLE in codes(result)
    assert all(table["omission_evidence_ids"] == [] for table in result.tables)


def test_no_tables_is_refused_rather_than_returning_an_empty_document():
    with pytest.raises(ExtensionError) as excinfo:
        assembly(tables=[])
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "empty document" in str(excinfo.value)


def test_a_plain_dict_is_refused_so_an_undeclared_header_cannot_slip_in():
    with pytest.raises(ExtensionError) as excinfo:
        assembly(tables=[{"table_id": "T-X", "cells": [], "slots": []}])
    assert "declared header is not optional" in str(excinfo.value)


def test_the_generation_and_source_are_carried_through():
    result = assembly()
    assert result.generation_id == "GEN-1"
    assert result.source_sha256 == "a" * 64
    assert result.to_mapping()["stats"]["tables"] == 1
