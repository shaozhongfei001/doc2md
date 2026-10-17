"""Grid matrix tests (L03 / T303).

Why these assertions look the way they do
-----------------------------------------
Every negative case below pins **the reason** a table is refused, not merely
that something was raised. Two failure modes motivate that style:

* a span validator that "repairs" ``rowspan=0`` to ``1`` produces a table that
  still passes a naive ``pytest.raises`` check, so the span tests also assert
  the message says the span is *never* repaired;
* a hole check that fires for the wrong reason (e.g. shape mismatch instead of an
  unexplained hole) would let a scanner miss through, so the hole tests assert
  the coordinate and the reason are both named.

The final integration pair is deliberately two-sided: the generated table must
be **accepted** and the same table with one slot changed must be **rejected**.
Only the pair proves the ``validate_root`` anchor is live — the accept half alone
would stay green even if ``Table.grid`` stopped comparing the matrices.
"""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from app.extensions import ExtensionError
from app.extensions.contracts import ContractError, validate_root
from app.extensions.tables.grid import (
    ANCHOR_KEYS,
    SPAN_FIELDS,
    TABLE_KEYS,
    GridResult,
    build_slots,
    to_contract_table,
    validate_grid,
)

SHA = "a" * 64


# --------------------------------------------------------------------- helpers
def anchor(
    cell_id: str,
    row: int,
    col: int,
    *,
    rowspan: int = 1,
    colspan: int = 1,
    text: str | None = "x",
    **extra,
) -> dict:
    """An anchor cell shaped like the ``native_tables`` output."""
    cell = {
        "cell_id": cell_id,
        "row": row,
        "col": col,
        "rowspan": rowspan,
        "colspan": colspan,
        "text": text,
        "text_origin": "SOURCE_XML",
        "text_alignment": "NATIVE",
        "engine_confidence": None,
        "token_ids": [],
        "evidence_ids": [],
        "nested_table_ids": [],
    }
    cell.update(extra)
    return cell


def span_error(excinfo: pytest.ExceptionInfo[ExtensionError], *needles: str) -> None:
    """Assert the failure is a span fault naming every ``needle`` in the message."""
    assert excinfo.value.code == "E_TABLE_SPAN_INVALID"
    message = excinfo.value.message
    for needle in needles:
        assert needle in message, f"expected {needle!r} in {message!r}"


def two_by_two() -> list[dict]:
    """Row 0 merged across two columns, row 1 split, right cell genuinely blank."""
    return [
        anchor("c1", 0, 0, colspan=2, text="合计"),
        anchor("c2", 1, 0, text="100"),
        anchor("c3", 1, 1, text=""),  # a real empty cell, not a hole
    ]


def grid_before_cells() -> list[dict]:
    """``w:gridBefore`` shape: row 0 starts at column 1, row 1 spans all three."""
    return [
        anchor("c1", 0, 1, text="项目"),
        anchor("c2", 0, 2, text="金额"),
        anchor("c3", 1, 0, colspan=3, text="说明"),
    ]


def enhancement_with(table: dict, evidence_ids: tuple[str, ...] = ("ce-1",)) -> dict:
    """Minimal ``document-enhancement`` payload carrying exactly one table."""
    evidence = [
        {
            "candidate_evidence_id": evidence_id,
            "source_version_id": "sv-1",
            "locator": {
                "kind": "TEXT_SPAN",
                "source_sha256": SHA,
                "text_file": {
                    "root": "EXTENSION",
                    "relative_path": "parsed/text.md",
                    "sha256": "f" * 64,
                },
                "start_offset": 0,
                "end_offset": 4,
            },
            "raw_engine_ref": None,
            "quote": None,
            "crop_ref": None,
            "observation_class": "SOURCE_CONTENT",
            "review_state": "UNREVIEWED",
            "review_decision_ref": None,
        }
        for evidence_id in evidence_ids
    ]
    return {
        "schema_version": "document-enhancement/1.0",
        "generation_id": "b" * 64,
        "job_key": "c" * 64,
        "content_sha256": SHA,
        "source_version_ids": ["sv-1"],
        "base_manifest_sha256": None,
        "base_ir_sha256": None,
        "raw_output_sha256": "d" * 64,
        "normalizer_version": "candidate-1.0",
        "profile_sha256": "e" * 64,
        "geometries": [],
        "tables": [table],
        "continuations": [],
        "visuals": [],
        "evidence": evidence,
        "issues": [],
        "numeric_estimates_enabled": False,
    }


# ---------------------------------------------------------------- anchor/slots
def test_single_cell_grid_is_one_by_one():
    cells = [anchor("c1", 0, 0, text="100")]
    assert build_slots(cells, n_rows=1, n_cols=1) == [["c1"]]

    result = validate_grid(cells=cells, n_rows=1, n_cols=1, slots=[["c1"]])
    assert isinstance(result, GridResult)
    assert (result.n_rows, result.n_cols) == (1, 1)
    assert result.slots == [["c1"]]
    assert result.omitted_slots == []
    assert [cell["cell_id"] for cell in result.anchor_cells] == ["c1"]


def test_colspan_points_every_covered_slot_at_the_same_anchor():
    slots = build_slots(
        [anchor("c1", 0, 0, colspan=3, text="合并表头")], n_rows=1, n_cols=3
    )
    assert slots == [["c1", "c1", "c1"]]
    # The anchor is the only cell: the covered positions point back, they do not
    # become three cells (that would triple one printed fact).
    assert sum(cell.count("c1") for cell in slots) == 3


def test_rowspan_and_colspan_combination_fills_the_expected_positions():
    cells = [
        anchor("c1", 0, 0, rowspan=2, colspan=2, text="汇总"),
        anchor("c2", 0, 2, text="本期"),
        anchor("c3", 1, 2, text="上期"),
        anchor("c4", 2, 0, colspan=3, text="注"),
    ]
    slots = validate_grid(
        cells=cells,
        n_rows=3,
        n_cols=3,
        slots=[
            ["c1", "c1", "c2"],
            ["c1", "c1", "c3"],
            ["c4", "c4", "c4"],
        ],
    ).slots
    assert slots == [
        ["c1", "c1", "c2"],
        ["c1", "c1", "c3"],
        ["c4", "c4", "c4"],
    ]


def test_blank_cell_is_a_cell_not_a_hole():
    cells = two_by_two()
    result = validate_grid(
        cells=cells,
        n_rows=2,
        n_cols=2,
        slots=[["c1", "c1"], ["c2", "c3"]],
    )
    # The empty string stays a legal occupied slot: only `None` is a hole.
    assert result.slots == [["c1", "c1"], ["c2", "c3"]]
    assert result.omitted_slots == []
    assert result.anchor_cells[2]["text"] == ""


def test_grid_result_is_frozen_and_reports_the_declared_shape():
    result = validate_grid(
        cells=two_by_two(), n_rows=2, n_cols=2, slots=[["c1", "c1"], ["c2", "c3"]]
    )
    with pytest.raises(FrozenInstanceError):
        result.n_rows = 3  # type: ignore[misc]
    assert (result.n_rows, result.n_cols) == (2, 2)


def test_anchor_cells_expose_the_closed_contract_cell_fields():
    result = validate_grid(
        cells=two_by_two(), n_rows=2, n_cols=2, slots=[["c1", "c1"], ["c2", "c3"]]
    )
    expected_keys = {
        "cell_id",
        "row",
        "col",
        "rowspan",
        "colspan",
        "text",
        "text_origin",
        "text_alignment",
        "engine_confidence",
        "token_ids",
        "evidence_ids",
        "nested_table_ids",
    }
    assert set(result.anchor_cells[0]) == expected_keys
    assert set(ANCHOR_KEYS) <= set(result.anchor_cells[0])
    assert set(SPAN_FIELDS) == {"rowspan", "colspan"}


# ----------------------------------------------------------------- span refusals
def test_zero_rowspan_is_refused_and_not_repaired_to_one():
    with pytest.raises(ExtensionError) as excinfo:
        build_slots([anchor("c1", 0, 0, rowspan=0)], n_rows=2, n_cols=1)
    span_error(excinfo, "c1", "rowspan=0", "never repaired to 1")


def test_negative_colspan_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_slots([anchor("c1", 0, 0, colspan=-2)], n_rows=1, n_cols=3)
    span_error(excinfo, "c1", "colspan=-2", "positive integer")


@pytest.mark.parametrize("bad_span", [0.5, 1.5, 2.0, "1", True, None])
def test_non_integer_span_is_refused(bad_span):
    # A 3x3 grid on purpose: a fractional span smaller than the grid can only be
    # caught by the type check, never by the bounds check, so this case pins the
    # integer requirement itself.
    with pytest.raises(ExtensionError) as excinfo:
        build_slots([anchor("c1", 0, 0, rowspan=bad_span)], n_rows=3, n_cols=3)
    span_error(excinfo, "c1", "rowspan")


def test_missing_span_is_refused_instead_of_defaulting_to_one():
    cells = [
        {"cell_id": "c1", "row": 0, "col": 0, "text": "x"},
    ]
    with pytest.raises(ExtensionError) as excinfo:
        build_slots(cells, n_rows=1, n_cols=1)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "rowspan" in excinfo.value.message


def test_anchor_beyond_last_row_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_slots([anchor("c1", 1, 0, rowspan=2)], n_rows=2, n_cols=1)
    span_error(excinfo, "c1", "row=1", "outside the 2x1 grid")


def test_anchor_beyond_last_column_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_slots([anchor("c1", 0, 1, colspan=2)], n_rows=1, n_cols=2)
    span_error(excinfo, "c1", "col=1", "outside the 1x2 grid")


def test_negative_anchor_coordinate_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_slots([anchor("c1", -1, 0)], n_rows=2, n_cols=2)
    span_error(excinfo, "c1", "outside the grid")


def test_overlapping_anchors_are_refused():
    cells = [
        anchor("c1", 0, 0, rowspan=2, colspan=2, text="合并"),
        anchor("c2", 1, 1, text="叠上来"),
    ]
    with pytest.raises(ExtensionError) as excinfo:
        build_slots(cells, n_rows=2, n_cols=2)
    span_error(excinfo, "c2", "row=1", "col=1", "c1", "overlap")


def test_duplicate_cell_ids_are_refused():
    cells = [anchor("c1", 0, 0), anchor("c1", 0, 1)]
    with pytest.raises(ExtensionError) as excinfo:
        build_slots(cells, n_rows=1, n_cols=2)
    span_error(excinfo, "duplicate cell_id", "c1", "unique")


# --------------------------------------------------------------------- holes
def test_unexplained_hole_is_refused_and_named_by_coordinate():
    cells = [anchor("c1", 0, 0, text="100")]
    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=cells,
            n_rows=2,
            n_cols=2,
            slots=[["c1", None], [None, None]],
        )
    span_error(excinfo, "unexplained hole", "row=0", "col=1", "omitted_slots")


def test_hole_without_evidence_is_refused():
    cells = [anchor("c1", 0, 0, text="100")]
    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=cells,
            n_rows=2,
            n_cols=2,
            slots=[["c1", None], [None, None]],
            omitted_slots=[(0, 1), (1, 0), (1, 1)],
        )
    span_error(excinfo, "omitted slot", "omission_evidence_ids is empty")


def test_omitted_slot_that_is_actually_covered_is_refused():
    cells = [anchor("c1", 0, 0, text="a"), anchor("c2", 0, 1, text="b")]
    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=cells,
            n_rows=1,
            n_cols=2,
            slots=[["c1", "c2"]],
            omitted_slots=[(0, 1)],
            omission_evidence_ids=["docx:gridBefore:0"],
        )
    span_error(excinfo, "omitted slot", "row=0", "col=1", "c2", "covered")


def test_omission_evidence_must_be_non_empty_strings():
    cells = [anchor("c1", 0, 1, text="a")]
    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=cells,
            n_rows=1,
            n_cols=2,
            slots=[[None, "c1"]],
            omitted_slots=[(0, 0)],
            omission_evidence_ids=[""],
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "omission_evidence_ids" in excinfo.value.message


def test_omitted_slot_outside_the_grid_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=[anchor("c1", 0, 0)],
            n_rows=1,
            n_cols=1,
            slots=[["c1"]],
            omitted_slots=[(0, 5)],
            omission_evidence_ids=["docx:gridBefore:0"],
        )
    span_error(excinfo, "omitted slot", "row=0", "col=5", "outside the 1x1 grid")


def test_grid_before_omission_with_evidence_is_accepted():
    cells = grid_before_cells()
    result = validate_grid(
        cells=cells,
        n_rows=2,
        n_cols=3,
        slots=[[None, "c1", "c2"], ["c3", "c3", "c3"]],
        omitted_slots=[(0, 0)],
        omission_evidence_ids=["docx:gridBefore:0"],
    )
    assert result.slots == [[None, "c1", "c2"], ["c3", "c3", "c3"]]
    assert result.omitted_slots == [(0, 0)]


def test_a_scan_miss_cannot_pose_as_a_grid_before_omission():
    """Same shape, no XML proof: the hole must be refused, not explained away.

    This is the discriminator the SDD §8.3 asks for — a Word ``gridBefore`` and a
    scanner that never saw the cell produce an identical matrix, so only the
    evidence decides, never the shape.
    """
    cells = grid_before_cells()
    slots = [[None, "c1", "c2"], ["c3", "c3", "c3"]]

    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=cells,
            n_rows=2,
            n_cols=3,
            slots=slots,
            omitted_slots=[(0, 0)],
            omission_evidence_ids=(),
        )
    span_error(excinfo, "omission_evidence_ids is empty", "row=0", "col=0")

    # The very same geometry passes once the omission carries source proof.
    assert validate_grid(
        cells=cells,
        n_rows=2,
        n_cols=3,
        slots=slots,
        omitted_slots=[(0, 0)],
        omission_evidence_ids=["docx:gridBefore:0"],
    ).omitted_slots == [(0, 0)]


# --------------------------------------------------------------- slots mismatch
def test_slot_matrix_pointing_at_the_wrong_anchor_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=two_by_two(),
            n_rows=2,
            n_cols=2,
            slots=[["c1", "c1"], ["c2", "c1"]],
        )
    span_error(excinfo, "slot-to-cell map mismatch", "row=1", "col=1", "c1", "c3")


def test_slot_matrix_with_the_wrong_shape_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=two_by_two(), n_rows=2, n_cols=2, slots=[["c1", "c1"], ["c2"]]
        )
    span_error(excinfo, "shape mismatch", "2x2")


def test_slot_matrix_with_a_bad_value_type_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        validate_grid(
            cells=[anchor("c1", 0, 0)], n_rows=1, n_cols=1, slots=[[0]]
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "slots[0][0]" in excinfo.value.message


def test_non_mapping_cells_are_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_slots(["c1"], n_rows=1, n_cols=1)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "cells[0]" in excinfo.value.message


def test_negative_grid_dimension_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        build_slots([], n_rows=-1, n_cols=0)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "n_rows" in excinfo.value.message


# ------------------------------------------------------------ to_contract_table
def test_to_contract_table_emits_exactly_the_closed_key_set():
    table = to_contract_table("t1", two_by_two())
    assert set(table) == set(TABLE_KEYS)
    assert len(table) == len(TABLE_KEYS)
    assert table["structure_state"] == "VALID_CANDIDATE"
    assert table["review_decision_ref"] is None
    assert table["cell_semantics"] == []
    assert table["notes"] == []


def test_to_contract_table_infers_the_grid_from_the_outermost_anchor_edge():
    wide = to_contract_table("t1", [anchor("c1", 0, 0, colspan=3, text="跨三列")])
    # One anchor of colspan 3 sizes a 1x3 grid: no fourth column, no second row.
    assert (wide["n_rows"], wide["n_cols"]) == (1, 3)
    assert wide["slots"] == [["c1", "c1", "c1"]]

    tall = to_contract_table(
        "t2",
        [
            anchor("c1", 0, 0, colspan=2, text="表头"),
            anchor("c2", 1, 0, text="10"),
            anchor("c3", 1, 1, text="末格"),
        ],
    )
    assert (tall["n_rows"], tall["n_cols"]) == (2, 2)
    assert tall["slots"] == [["c1", "c1"], ["c2", "c3"]]


def test_to_contract_table_keeps_text_on_the_anchor_only():
    table = to_contract_table("t1", two_by_two())
    dumped = json.dumps(table, ensure_ascii=False)
    assert dumped.count("合计") == 1
    assert [cell["cell_id"] for cell in table["cells"]] == ["c1", "c2", "c3"]
    assert table["slots"] == [["c1", "c1"], ["c2", "c3"]]


def test_to_contract_table_passes_parent_and_ir_reference_through():
    table = to_contract_table(
        "t-inner", [anchor("c9", 0, 0)], parent_cell_id="c1", ir_ref="ir:tbl:7"
    )
    assert table["parent_cell_id"] == "c1"
    assert table["ir_ref"] == "ir:tbl:7"


def test_to_contract_table_requires_evidence_for_grid_before_holes():
    with pytest.raises(ExtensionError) as excinfo:
        to_contract_table("t1", grid_before_cells())
    span_error(excinfo, "omitted slot", "omission_evidence_ids is empty")

    table = to_contract_table(
        "t1", grid_before_cells(), omission_evidence_ids=["docx:gridBefore:0"]
    )
    assert table["omitted_slots"] == [(0, 0)]
    assert table["omission_evidence_ids"] == ["docx:gridBefore:0"]


def test_to_contract_table_refuses_a_bad_table_id():
    with pytest.raises(ExtensionError) as excinfo:
        to_contract_table("", [anchor("c1", 0, 0)])
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "table_id" in excinfo.value.message


def test_to_contract_table_handles_an_empty_table():
    table = to_contract_table("t1", [])
    assert (table["n_rows"], table["n_cols"]) == (0, 0)
    assert table["cells"] == []
    assert table["slots"] == []
    # An empty candidate table is structurally legal for the contract; it is
    # simply not useful, and it is not silently dropped.
    assert validate_root("document-enhancement", enhancement_with(table)) is not None


# ------------------------------------------------------- integration with contract
def test_generated_table_is_accepted_by_the_root_contract():
    table = to_contract_table(
        "t1",
        [
            anchor("c1", 0, 0, colspan=2, text="项目", evidence_ids=("ce-1",)),
            anchor("c2", 1, 0, text="日均存款"),
            anchor("c3", 1, 1, text="100"),
        ],
    )
    payload = enhancement_with(table)
    validated = validate_root("document-enhancement", payload)
    assert validated.tables[0].slots == [["c1", "c1"], ["c2", "c3"]]
    assert validated.tables[0].cells[0].evidence_ids == ["ce-1"]

    # Same payload as JSON on disk: tuples become arrays, which the JSON-mode
    # path must also accept.
    from_json = validate_root(
        "document-enhancement", json.dumps(payload, ensure_ascii=False)
    )
    assert from_json.tables[0].omitted_slots == []


def test_generated_grid_before_table_is_accepted_by_the_root_contract():
    table = to_contract_table(
        "t1", grid_before_cells(), omission_evidence_ids=["ce-1"]
    )
    validated = validate_root("document-enhancement", enhancement_with(table))
    assert validated.tables[0].omitted_slots == [(0, 0)]
    assert validated.tables[0].omission_evidence_ids == ["ce-1"]


def corrupt_slot_to_unknown_anchor(table: dict) -> None:
    table["slots"][1][1] = "c-missing"


def corrupt_by_dropping_a_cell(table: dict) -> None:
    """Drop the anchor *and* blank its slot: a genuine undeclared hole."""
    table["cells"] = table["cells"][:-1]
    table["slots"][1][1] = None


@pytest.mark.parametrize(
    "corrupt,needle",
    [
        (corrupt_slot_to_unknown_anchor, "slot-to-cell map mismatch"),
        (corrupt_by_dropping_a_cell, "omitted slots require evidence"),
    ],
    ids=["wrong-anchor", "undeclared-hole"],
)
def test_contract_refuses_a_grid_corrupted_after_generation(corrupt, needle):
    """The accept half above plus this reject half is what proves the anchor."""
    table = to_contract_table("t1", two_by_two())
    payload = enhancement_with(table)
    validate_root("document-enhancement", payload)  # baseline must pass

    corrupted = copy.deepcopy(payload)
    corrupt(corrupted["tables"][0])

    with pytest.raises(ContractError) as excinfo:
        validate_root("document-enhancement", corrupted)
    assert excinfo.value.code == "E_CONTRACT_INVALID"
    messages = " | ".join(err["msg"] for err in excinfo.value.errors)
    assert needle in messages, f"expected {needle!r} in {messages!r}"
