"""T503 / ``PI-F04-08``: a footnote binds to an explicit scope, or it does not bind.

The failure this file guards against is a footnote silently widening to the whole
table ("nearest text applies to everything"), which would attach an exception
clause to rows that never carried it.
"""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.tables.notes import (
    NOTES_CODE,
    bind_notes,
    link_cell_note_ids,
)


def table() -> dict:
    return {
        "table_id": "T1",
        "n_rows": 2,
        "n_cols": 2,
        "cells": [
            {"cell_id": "c00", "row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "项目"},
            {"cell_id": "c01", "row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "金额"},
            {"cell_id": "c10", "row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "本金"},
            {"cell_id": "c11", "row": 1, "col": 1, "rowspan": 1, "colspan": 1, "text": "100"},
        ],
    }


def note(note_id: str = "N1", **overrides) -> dict:
    payload = {
        "note_id": note_id,
        "text": "含外币折人民币，按期末汇率折算。",
        "marker": "*",
        "scope": "CELL",
        "target_ids": ["c11"],
        "evidence_ids": ["docx:footnote:3"],
        "evidence_kind": "DOCX_FOOTNOTE",
    }
    payload.update(overrides)
    return payload


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


# ------------------------------------------------------------------ happy path
def test_native_cell_footnote_binds_and_keeps_original_text():
    text = "  含外币折人民币，  按期末汇率折算。 "
    result = bind_notes(table(), [note(text=text)])
    bound = result.notes[0]
    assert bound["scope"] == "CELL"
    assert bound["target_ids"] == ["c11"]
    assert bound["state"] == "CANDIDATE"
    assert bound["review_decision_ref"] is None
    # Outer whitespace goes; inner spacing is part of the clause and stays.
    assert bound["text"] == "含外币折人民币，  按期末汇率折算。"
    assert bound["text"].endswith("。")


def test_output_is_the_closed_note_payload():
    result = bind_notes(table(), [note()])
    assert set(result.notes[0]) == {
        "note_id",
        "text",
        "scope",
        "target_ids",
        "evidence_ids",
        "state",
        "review_decision_ref",
    }


def test_table_scope_is_allowed_for_a_native_reference():
    result = bind_notes(
        table(),
        [note(scope="TABLE", target_ids=["T1"])],
    )
    assert result.notes[0]["scope"] == "TABLE"
    assert result.notes[0]["state"] == "CANDIDATE"
    assert result.publishable


def test_review_decision_promotes_a_bound_note():
    result = bind_notes(table(), [note(review_decision_ref="RV-0003")])
    assert result.notes[0]["state"] == "CONFIRMED"
    assert result.notes[0]["review_decision_ref"] == "RV-0003"


# ------------------------------------------------------------ ambiguity blocks
def test_scope_without_targets_becomes_unresolved_and_unpublishable():
    result = bind_notes(table(), [note(scope="CELL", target_ids=[])])
    bound = result.notes[0]
    assert bound["scope"] == "UNRESOLVED"
    assert bound["state"] == "NEEDS_REVIEW"
    assert "SCOPE_WITHOUT_TARGETS" in codes(result)
    assert result.publishable == []


def test_adjacency_evidence_may_not_cover_the_whole_table():
    result = bind_notes(
        table(),
        [note(scope="TABLE", target_ids=["T1"], evidence_kind="PADDLE_LAYOUT_BLOCK")],
    )
    bound = result.notes[0]
    assert bound["scope"] == "UNRESOLVED"
    assert bound["state"] == "NEEDS_REVIEW"
    assert "ADJACENCY_MAY_NOT_SCOPE_TABLE" in codes(result)
    # The note itself is kept: it is still source evidence, just not a condition.
    assert bound["text"].startswith("含外币")


def test_adjacency_evidence_may_still_bind_a_specific_cell():
    result = bind_notes(
        table(),
        [note(scope="CELL", target_ids=["c11"], evidence_kind="PADDLE_LAYOUT_BLOCK")],
    )
    assert result.notes[0]["scope"] == "CELL"
    assert result.notes[0]["state"] == "CANDIDATE"


def test_dangling_target_is_reported_not_dropped():
    result = bind_notes(table(), [note(target_ids=["c99"])])
    bound = result.notes[0]
    assert bound["state"] == "NEEDS_REVIEW"
    assert "DANGLING_TARGET" in codes(result)
    assert bound["target_ids"] == ["c99"]


def test_note_without_evidence_cannot_be_published():
    result = bind_notes(table(), [note(evidence_ids=[])])
    assert result.notes[0]["state"] == "NEEDS_REVIEW"
    assert "MISSING_EVIDENCE" in codes(result)
    assert result.publishable == []


def test_review_ref_cannot_rescue_an_unresolved_scope():
    result = bind_notes(
        table(),
        [note(scope="TABLE", target_ids=["T1"], evidence_kind="PADDLE_LAYOUT_BLOCK", review_decision_ref="RV-9")],
    )
    bound = result.notes[0]
    assert bound["state"] == "NEEDS_REVIEW"
    assert bound["review_decision_ref"] is None
    assert "REVIEW_REF_IGNORED" in codes(result)


def test_conflicting_text_on_same_target_blocks_both():
    result = bind_notes(
        table(),
        [
            note("N1", text="含外币折人民币。"),
            note("N2", text="不含外币。"),
        ],
    )
    assert "CONFLICTING_TEXT" in codes(result)
    assert {n["state"] for n in result.notes} == {"NEEDS_REVIEW"}
    assert result.publishable == []


def test_identical_text_on_same_target_is_not_a_conflict():
    result = bind_notes(
        table(),
        [note("N1", text="含外币折人民币。"), note("N2", text="含外币折人民币。")],
    )
    assert "CONFLICTING_TEXT" not in codes(result)
    assert all(n["state"] == "CANDIDATE" for n in result.notes)


# ------------------------------------------------------------ cell linkage
def test_cell_note_ids_are_linked_only_for_resolved_cell_scope():
    notes = bind_notes(
        table(),
        [
            note("N1", scope="CELL", target_ids=["c11"]),
            note("N2", scope="CELL", target_ids=["c10"], evidence_ids=[]),
        ],
    ).notes
    semantics = [
        {"data_cell_id": "c10", "note_ids": []},
        {"data_cell_id": "c11", "note_ids": []},
    ]
    linked = {item["data_cell_id"]: item["note_ids"] for item in link_cell_note_ids(semantics, notes)}
    assert linked["c11"] == ["N1"]
    assert linked["c10"] == []  # NEEDS_REVIEW notes are not attached to a cell


def test_link_rejects_a_note_id_that_the_table_does_not_own():
    with pytest.raises(ExtensionError) as excinfo:
        link_cell_note_ids([{"data_cell_id": "c11", "note_ids": ["N404"]}], [])
    assert excinfo.value.code == NOTES_CODE
    assert "unknown note" in str(excinfo.value)


# -------------------------------------------------------------- input refusals
@pytest.mark.parametrize(
    "bad_note, match",
    [
        ({"note_id": "", "text": "x"}, "note_id"),
        ({"note_id": "N1", "text": "   "}, "text must be non-empty"),
        ({"note_id": "N1", "text": "x", "scope": "PARAGRAPH"}, "scope must be one of"),
        ({"note_id": "N1", "text": "x", "evidence_kind": "OCR_GUESS"}, "evidence_kind"),
        ({"note_id": "N1", "text": "x", "target_ids": "c11"}, "target_ids must be a sequence"),
        ({"note_id": "N1", "text": "x", "evidence_ids": "docx:1"}, "evidence_ids must be a sequence"),
        ({"note_id": "N1", "text": "x", "review_decision_ref": "  "}, "review_decision_ref"),
    ],
)
def test_malformed_notes_are_refused(bad_note, match):
    with pytest.raises(ExtensionError) as excinfo:
        bind_notes(table(), [bad_note])
    assert excinfo.value.code == NOTES_CODE
    assert match in str(excinfo.value)


def test_duplicate_note_ids_are_refused():
    with pytest.raises(ExtensionError) as excinfo:
        bind_notes(table(), [note("N1"), note("N1")])
    assert "duplicate note_id" in str(excinfo.value)
