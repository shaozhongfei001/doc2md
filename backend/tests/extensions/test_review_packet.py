"""T504: the review packet must be readable, self-contained, and refuse GAP-06.

GAP-06 was a false green: a ``Table`` could satisfy its own closed contract while
the enclosing ``DocumentEnhancement`` rejected it because
``omission_evidence_ids`` cited ids the document never registered. The packet is
the first place both views meet, so the subset rule is enforced here.
"""

from __future__ import annotations

import os

import pytest

from app.extensions import ExtensionError
from app.extensions.render.review_packet import PACKET_CODE, build_review_packet

EVIDENCE = [
    {"candidate_evidence_id": "EV-OMIT-1"},
    {"candidate_evidence_id": "EV-FN-1"},
]


def table(**overrides) -> dict:
    payload = {
        "table_id": "T1",
        "n_rows": 2,
        "n_cols": 2,
        "structure_state": "VALID_CANDIDATE",
        "cells": [
            {"cell_id": "h0", "row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "项目", "text_origin": "SOURCE_XML", "engine_confidence": None},
            {"cell_id": "h1", "row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "金额", "text_origin": "SOURCE_XML", "engine_confidence": None},
            {"cell_id": "c10", "row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "本金", "text_origin": "SOURCE_XML", "engine_confidence": None},
            {"cell_id": "c11", "row": 1, "col": 1, "rowspan": 1, "colspan": 1, "text": "100|200", "text_origin": "SOURCE_XML", "engine_confidence": None},
        ],
        "slots": [["h0", "h1"], ["c10", "c11"]],
        "omitted_slots": [],
        "omission_evidence_ids": [],
    }
    payload.update(overrides)
    return payload


def semantics() -> list[dict]:
    return [
        {
            "data_cell_id": "c11",
            "column_header_cell_ids": ["h1"],
            "row_header_cell_ids": ["c10"],
            "note_ids": ["N1"],
            "unit_raw": "万元",
            "state": "CANDIDATE",
            "review_decision_ref": None,
        }
    ]


def notes() -> list[dict]:
    return [
        {
            "note_id": "N1",
            "text": "含外币折人民币。",
            "scope": "CELL",
            "target_ids": ["c11"],
            "evidence_ids": ["EV-FN-1"],
            "state": "CANDIDATE",
            "review_decision_ref": None,
        }
    ]


def build(**overrides):
    kwargs = {
        "semantics": semantics(),
        "notes": notes(),
        "evidence_candidates": EVIDENCE,
        "context": {"caption": "表1 贷款余额", "period": "2025H1", "unit_raw": "万元"},
    }
    kwargs.update(overrides)
    return build_review_packet(table(**kwargs.pop("table_overrides", {})), **kwargs)


# ------------------------------------------------------------------ rendering
def test_packet_contains_context_and_readable_header_paths():
    packet = build()
    text = packet.markdown
    assert "表1 贷款余额" in text
    assert "2025H1" in text
    assert "万元" in text
    # Header path is rendered as text, resolved from cell ids.
    assert "金额" in text and "本金" in text
    assert "N1" in text and "含外币折人民币。" in text
    assert "EV-FN-1" in text


def test_packet_payload_is_structured_and_self_describing():
    payload = build().payload
    assert payload["table_id"] == "T1"
    assert payload["generated_by"].endswith("L05/T504")
    row = payload["rows"][0]
    assert row["column_header_cell_ids"] == ["h1"]
    assert row["column_header_text"] == ["金额"]
    assert row["row_header_text"] == ["本金"]
    assert row["engine_confidence"] is None  # never invented


def test_pipe_in_cell_text_does_not_break_the_markdown_table():
    text = build().markdown
    assert "100\\|200" in text


def test_missing_unit_is_rendered_as_null_not_guessed():
    packet = build(context={"caption": "表1"})
    assert "`null`" in packet.markdown


def test_blocked_items_are_listed_with_their_code():
    packet = build(
        blocked=[{"code": "HEADER_INCOMPLETE", "message": "缺少表头", "cell_id": "c11"}]
    )
    assert "HEADER_INCOMPLETE" in packet.markdown
    assert packet.payload["blocked"][0]["cell_id"] == "c11"


def test_no_semantics_still_produces_a_reviewable_packet():
    packet = build(semantics=[])
    assert "尚未给出表头路径" in packet.markdown
    assert packet.payload["rows"] == []


# --------------------------------------------------- GAP-06: omission subset
def test_omission_evidence_must_be_registered_in_the_document():
    with pytest.raises(ExtensionError) as excinfo:
        build(table_overrides={"omission_evidence_ids": ["EV-OMIT-404"]})
    assert excinfo.value.code == PACKET_CODE
    assert "EV-OMIT-404" in str(excinfo.value)


def test_registered_omission_evidence_is_accepted_and_listed():
    packet = build(table_overrides={"omission_evidence_ids": ["EV-OMIT-1"]})
    assert packet.payload["omission_evidence_ids"] == ["EV-OMIT-1"]
    assert "省略位证据" in packet.markdown


def test_note_evidence_must_also_be_registered():
    bad_note = dict(notes()[0], evidence_ids=["EV-NOT-REGISTERED"])
    with pytest.raises(ExtensionError) as excinfo:
        build(notes=[bad_note])
    assert excinfo.value.code == PACKET_CODE
    assert "EV-NOT-REGISTERED" in str(excinfo.value)


# ------------------------------------------------------------- reference integrity
def test_dangling_column_header_reference_is_refused():
    bad = [dict(semantics()[0], column_header_cell_ids=["h404"])]
    with pytest.raises(ExtensionError) as excinfo:
        build(semantics=bad)
    assert "dangling column header" in str(excinfo.value)


def test_data_cell_outside_the_table_is_refused():
    bad = [dict(semantics()[0], data_cell_id="c404")]
    with pytest.raises(ExtensionError) as excinfo:
        build(semantics=bad)
    assert "not a cell of table" in str(excinfo.value)


def test_a_cell_cannot_be_its_own_header():
    bad = [dict(semantics()[0], column_header_cell_ids=["c11"])]
    with pytest.raises(ExtensionError) as excinfo:
        build(semantics=bad)
    assert "its own column header" in str(excinfo.value)


def test_cell_semantics_cannot_reference_unknown_notes():
    bad = [dict(semantics()[0], note_ids=["N404"])]
    with pytest.raises(ExtensionError) as excinfo:
        build(semantics=bad, notes=notes())
    assert "notes the table does not own" in str(excinfo.value)


def test_duplicate_candidate_ids_are_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build(evidence_candidates=[{"candidate_evidence_id": "EV-1"}, {"candidate_evidence_id": "EV-1"}])
    assert "duplicate candidate_evidence_id" in str(excinfo.value)


# --------------------------------------------------------------- no side effects
def test_building_a_packet_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build()
    assert os.listdir(tmp_path) == []
