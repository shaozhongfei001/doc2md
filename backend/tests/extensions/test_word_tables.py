"""``word.native_tables`` — gridSpan / vMerge / gridBefore / nested tables (T301).

Each case is a hand-built OOXML body: python-docx can express the *legal* merges
but not the *illegal* ones (continue-without-anchor, gridSpan=0, unexplained
holes), and those are exactly the inputs that must be refused instead of
repaired into something plausible.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.contracts import Cell as ContractCell
from app.extensions.contracts import Table as ContractTable
from app.extensions.word.final_view import FinalView, build_final_view
from app.extensions.tables.grid import to_contract_table
from app.extensions.word.native_tables import CELL_KEYS, TABLE_KEYS, extract_tables
from app.extensions.word.package import open_docx

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)


def document_xml(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    ).encode("utf-8")


def write_docx(path: Path, body: str) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
    return path


def table(
    rows: str,
    *,
    cols: int = 2,
    grid: bool = True,
    props: str = "",
) -> str:
    grid_xml = (
        "<w:tblGrid>" + "<w:gridCol w:w=\"2000\"/>" * cols + "</w:tblGrid>" if grid else ""
    )
    return f"<w:tbl><w:tblPr>{props}</w:tblPr>{grid_xml}{rows}</w:tbl>"


def tc(
    text: str | None = None,
    *,
    span: int | None = None,
    vmerge: str | None = None,
    nested: str = "",
    props: str = "",
) -> str:
    tcpr = ""
    if span is not None or vmerge is not None or props:
        tcpr = "<w:tcPr>"
        if span is not None:
            tcpr += f'<w:gridSpan w:val="{span}"/>'
        if vmerge is not None:
            tcpr += "<w:vMerge/>" if vmerge == "continue" else f'<w:vMerge w:val="{vmerge}"/>'
        tcpr += props + "</w:tcPr>"
    paragraph = "" if text is None else f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    return f"<w:tc>{tcpr}{paragraph}{nested}</w:tc>"


def tr(*cells: str, before: int | None = None, after: int | None = None) -> str:
    props = ""
    if before is not None or after is not None:
        props = "<w:trPr>"
        if before is not None:
            props += f'<w:gridBefore w:val="{before}"/>'
        if after is not None:
            props += f'<w:gridAfter w:val="{after}"/>'
        props += "</w:trPr>"
    return f"<w:tr>{props}{''.join(cells)}</w:tr>"


def extract(tmp_path: Path, body: str):
    package = open_docx(write_docx(tmp_path / "tables.docx", body))
    return extract_tables(package, build_final_view(package))


def one(table_payload: list[dict]) -> dict:
    assert len(table_payload) == 1
    return table_payload[0]


# ------------------------------------------------------------------- plain grid
def test_simple_grid_maps_every_slot_to_its_anchor(tmp_path: Path):
    body = table(tr(tc("A"), tc("B")) + tr(tc("C"), tc("D")))
    payload = one(extract(tmp_path, body))

    assert payload["table_id"] == "TBL-0001"
    assert (payload["n_rows"], payload["n_cols"]) == (2, 2)
    assert payload["parent_cell_id"] is None
    assert payload["ir_ref"] is None
    assert payload["structure_state"] == "VALID_CANDIDATE"
    assert [cell["text"] for cell in payload["cells"]] == ["A", "B", "C", "D"]
    assert [(cell["row"], cell["col"], cell["rowspan"], cell["colspan"]) for cell in payload["cells"]] == [
        (0, 0, 1, 1),
        (0, 1, 1, 1),
        (1, 0, 1, 1),
        (1, 1, 1, 1),
    ]
    ids = [cell["cell_id"] for cell in payload["cells"]]
    assert payload["slots"] == [[ids[0], ids[1]], [ids[2], ids[3]]]
    assert payload["omitted_slots"] == []
    assert payload["omission_evidence_ids"] == []


def test_table_and_cell_key_sets_are_exactly_the_contract(tmp_path: Path):
    body = table(tr(tc("A"), tc("B")))
    payload = one(extract(tmp_path, body))

    assert tuple(payload) == TABLE_KEYS
    for cell in payload["cells"]:
        assert tuple(cell) == CELL_KEYS
        assert cell["text_origin"] == "SOURCE_XML"
        assert cell["text_alignment"] == "NATIVE"
        assert cell["engine_confidence"] is None
        assert cell["token_ids"] == []
        assert cell["evidence_ids"] == []
        # The closed contract must accept the payload directly.
        ContractCell.model_validate(cell)
    ContractTable.model_validate(payload)


def test_two_columns_with_identical_text_are_not_merged(tmp_path: Path):
    body = table(tr(tc("人民币"), tc("人民币")))
    payload = one(extract(tmp_path, body))

    assert len(payload["cells"]) == 2
    first, second = (cell["cell_id"] for cell in payload["cells"])
    assert first != second
    assert payload["slots"] == [[first, second]]


def test_empty_cell_is_a_valid_empty_value(tmp_path: Path):
    body = table(tr(tc(""), tc("有文字")))
    payload = one(extract(tmp_path, body))

    assert [cell["text"] for cell in payload["cells"]] == ["", "有文字"]
    assert payload["omitted_slots"] == []
    assert payload["omission_evidence_ids"] == []


def test_cell_text_maps_tabs_and_line_breaks(tmp_path: Path):
    cell = "<w:tc><w:p><w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t></w:r></w:p></w:tc>"
    payload = one(extract(tmp_path, table(tr(cell, tc("x")))))
    assert payload["cells"][0]["text"] == "a\tb\nc"


# ------------------------------------------------------------------- merges
def test_horizontal_merge_becomes_one_anchor_with_colspan(tmp_path: Path):
    body = table(tr(tc("合并表头", span=2)) + tr(tc("A"), tc("B")))
    payload = one(extract(tmp_path, body))

    header = payload["cells"][0]
    assert (header["colspan"], header["rowspan"], header["text"]) == (2, 1, "合并表头")
    assert len(payload["slots"][0]) == 2
    assert payload["slots"][0] == [header["cell_id"], header["cell_id"]]
    assert len(payload["cells"]) == 3


def test_vertical_merge_extends_the_anchor_rowspan(tmp_path: Path):
    body = table(
        tr(tc("项目"), tc("金额"))
        + tr(tc("日均存款", vmerge="restart"), tc("100"))
        + tr(tc(None, vmerge="continue"), tc("200"))
    )
    payload = one(extract(tmp_path, body))

    texts = [cell["text"] for cell in payload["cells"]]
    assert texts == ["项目", "金额", "日均存款", "100", "200"]
    anchor = payload["cells"][2]
    assert anchor["rowspan"] == 2
    assert anchor["colspan"] == 1
    # The covered position points back at the anchor and stores no second copy.
    assert payload["slots"][2][0] == anchor["cell_id"]
    assert payload["slots"][1][0] == anchor["cell_id"]
    assert [cell["row"] for cell in payload["cells"]] == [0, 0, 1, 1, 2]


def test_combined_vertical_and_horizontal_merge(tmp_path: Path):
    body = table(
        tr(tc("合计", span=2, vmerge="restart"), tc("备注"))
        + tr(tc(None, span=2, vmerge="continue"), tc("说明")),
        cols=3,
    )
    payload = one(extract(tmp_path, body))

    assert payload["n_cols"] == 3
    anchor = payload["cells"][0]
    assert (anchor["rowspan"], anchor["colspan"]) == (2, 2)
    assert payload["slots"] == [
        [anchor["cell_id"], anchor["cell_id"], payload["cells"][1]["cell_id"]],
        [anchor["cell_id"], anchor["cell_id"], payload["cells"][2]["cell_id"]],
    ]


def test_vertical_merge_continue_without_anchor_is_rejected(tmp_path: Path):
    body = table(tr(tc("A"), tc("B")) + tr(tc(None, vmerge="continue"), tc("C")))
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, body)
    error = excinfo.value
    assert error.code == "E_DOCX_MERGE_INVALID"
    assert "no anchor" in error.message
    assert error.detail["row"] == 1
    assert error.detail["col"] == 0


def test_vertical_merge_continue_with_a_different_interval_is_rejected(tmp_path: Path):
    body = table(
        tr(tc("A", span=2, vmerge="restart"), tc("B"))
        + tr(tc(None, span=1, vmerge="continue"), tc("C"), tc("D")),
        cols=3,
    )
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, body)
    error = excinfo.value
    assert error.code == "E_DOCX_MERGE_INVALID"
    assert "anchor above covers" in error.message


def test_vertical_merge_continue_over_part_of_an_anchor_is_rejected(tmp_path: Path):
    body = table(
        tr(tc("A", span=2), tc("B")) + tr(tc(None, span=1, vmerge="continue"), tc("C"), tc("D")),
        cols=3,
    )
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, body)
    assert excinfo.value.code == "E_DOCX_MERGE_INVALID"


# ------------------------------------------------- legacy w:hMerge (fail-closed)
@pytest.mark.parametrize(
    ("props", "state"),
    [
        ('<w:hMerge w:val="restart"/>', "restart"),
        ('<w:hMerge w:val="continue"/>', "continue"),
        ("<w:hMerge/>", "continue"),
        ('<w:hMerge w:val="bogus"/>', "unknown value"),
    ],
)
def test_legacy_hmerge_is_refused_not_read_as_a_plain_cell(tmp_path: Path, props: str, state: str):
    """``w:hMerge`` carries no width: mapping it to a normal tc silently widens the table."""
    body = table(tr(tc("合并", props=props), tc("B")))
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, body)

    error = excinfo.value
    assert error.code == "E_DOCX_MERGE_INVALID"
    assert "hMerge" in error.message
    assert "not supported" in error.message
    assert error.detail["table_id"] == "TBL-0001"
    assert error.detail["row"] == 0
    assert error.detail["col"] == 0
    assert state in error.detail["hmerge"]


def test_hmerge_is_refused_before_any_grid_is_emitted(tmp_path: Path):
    """The refusal must not depend on the table being otherwise well-formed."""
    body = table(tr(tc("A"), tc("B")) + tr(tc("C", props='<w:hMerge w:val="restart"/>')), cols=2)
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, body)
    assert excinfo.value.code == "E_DOCX_MERGE_INVALID"
    assert excinfo.value.detail["row"] == 1


def test_hmerge_and_vmerge_are_not_confused(tmp_path: Path):
    """A cell with ``w:vMerge`` only stays a supported vertical merge."""
    body = table(
        tr(tc("A", vmerge="restart"), tc("B")) + tr(tc(None, vmerge="continue"), tc("C"))
    )
    payload = one(extract(tmp_path, body))
    assert payload["cells"][0]["rowspan"] == 2


# --------------------------------------------------------------- span refusals
@pytest.mark.parametrize("span", ["0", "-1", "abc", "1.5", ""])
def test_invalid_grid_span_is_rejected_not_coerced(tmp_path: Path, span: str):
    cell = f'<w:tc><w:tcPr><w:gridSpan w:val="{span}"/></w:tcPr><w:p/></w:tc>'
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, table(tr(cell, tc("B"))))
    error = excinfo.value
    assert error.code == "E_TABLE_SPAN_INVALID"
    assert "gridSpan" in error.message
    assert error.detail["row"] == 0


def test_grid_span_beyond_the_grid_is_rejected(tmp_path: Path):
    """Anchor-bounds are judged by ``tables.grid``, not re-implemented here."""
    body = table(tr(tc("A", span=3), tc("B")), cols=2)
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, body)
    error = excinfo.value
    assert error.code == "E_TABLE_SPAN_INVALID"
    assert "runs outside" in error.message
    assert error.detail["n_cols"] == 2
    assert error.detail["cell_id"].startswith("TBL-0001-R0000C0000")


def test_unexplained_trailing_columns_are_rejected(tmp_path: Path):
    body = table(tr(tc("A"), tc("B")) + tr(tc("C")), cols=2)
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, body)
    error = excinfo.value
    assert error.code == "E_TABLE_SPAN_INVALID"
    assert "gridAfter" in error.message
    assert error.detail["row"] == 1


# ------------------------------------------------------------------- omissions
def test_grid_before_becomes_evidenced_omitted_slots(tmp_path: Path):
    body = table(
        tr(tc("整行标题", span=4)) + tr(tc("C"), tc("D"), before=2),
        cols=4,
    )
    payload = one(extract(tmp_path, body))

    assert payload["omitted_slots"] == [(1, 0), (1, 1)]
    assert payload["omission_evidence_ids"] == ["docx:gridBefore:1:0", "docx:gridBefore:1:1"]
    assert payload["slots"][1][0] is None
    assert payload["slots"][1][1] is None
    assert payload["slots"][1][2] == payload["cells"][1]["cell_id"]
    ContractTable.model_validate(payload)


def test_grid_after_becomes_evidenced_omitted_slots(tmp_path: Path):
    body = table(tr(tc("整行标题", span=3)) + tr(tc("C"), after=2), cols=3)
    payload = one(extract(tmp_path, body))

    assert payload["omitted_slots"] == [(1, 1), (1, 2)]
    assert payload["omission_evidence_ids"] == ["docx:gridAfter:1:1", "docx:gridAfter:1:2"]
    assert payload["slots"][1] == [payload["cells"][1]["cell_id"], None, None]
    ContractTable.model_validate(payload)


def test_omission_evidence_is_one_to_one_with_omitted_slots(tmp_path: Path):
    body = table(tr(tc("A"), before=1, after=1), cols=3)
    payload = one(extract(tmp_path, body))

    assert len(payload["omitted_slots"]) == len(payload["omission_evidence_ids"]) == 2
    assert len(set(payload["omission_evidence_ids"])) == 2


# ------------------------------------------------- grid-module integration
def test_every_cell_carries_explicit_spans_and_coordinates(tmp_path: Path):
    """grid refuses a cell without row/col/rowspan/colspan — never defaulted to 1."""
    body = table(
        tr(tc("A", span=2), tc("B")) + tr(tc("C"), tc("D"), tc("E")),
        cols=3,
    )
    payload = one(extract(tmp_path, body))

    for cell in payload["cells"]:
        for key in ("row", "col", "rowspan", "colspan"):
            assert key in cell
            assert isinstance(cell[key], int) and not isinstance(cell[key], bool)
            assert cell[key] >= 0 if key in ("row", "col") else cell[key] > 0


def test_omitted_slots_are_tuples_not_lists(tmp_path: Path):
    """pydantic strict + dict mode only accepts ``(row, col)`` tuples."""
    body = table(tr(tc("整行", span=3)) + tr(tc("C"), after=2), cols=3)
    payload = one(extract(tmp_path, body))

    assert payload["omitted_slots"], "fixture must produce holes"
    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in payload["omitted_slots"])
    assert len(payload["omission_evidence_ids"]) == len(payload["omitted_slots"])
    # The whole payload still passes the closed contract in dict mode.
    ContractTable.model_validate(payload)


def test_trailing_grid_after_keeps_the_xml_grid_width(tmp_path: Path):
    """``w:tblGrid`` is wider than the anchors: do not shrink the table."""
    body = table(tr(tc("A"), after=2), cols=3)
    payload = one(extract(tmp_path, body))

    assert payload["n_cols"] == 3
    assert payload["n_rows"] == 1
    assert payload["omitted_slots"] == [(0, 1), (0, 2)]
    assert payload["omission_evidence_ids"] == ["docx:gridAfter:0:1", "docx:gridAfter:0:2"]
    assert payload["slots"] == [[payload["cells"][0]["cell_id"], None, None]]
    ContractTable.model_validate(payload)


def test_fully_omitted_trailing_row_keeps_the_row_count(tmp_path: Path):
    body = table(tr(tc("A"), tc("B")) + tr(before=2), cols=2)
    payload = one(extract(tmp_path, body))

    assert payload["n_rows"] == 2
    assert payload["omitted_slots"] == [(1, 0), (1, 1)]
    assert payload["omission_evidence_ids"] == ["docx:gridBefore:1:0", "docx:gridBefore:1:1"]
    assert payload["slots"][1] == [None, None]
    ContractTable.model_validate(payload)


def test_grid_module_is_the_only_matrix_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If extraction rebuilt slots itself, breaking ``build_slots`` would not show."""
    import app.extensions.word.native_tables as native_tables

    calls: list[str] = []
    real = native_tables.build_slots

    def spy(cells, *, n_rows, n_cols):
        calls.append("build_slots")
        return real(cells, n_rows=n_rows, n_cols=n_cols)

    monkeypatch.setattr(native_tables, "build_slots", spy)
    one(extract(tmp_path, table(tr(tc("A"), tc("B")))))
    assert calls == ["build_slots"]


# ------------------------------------------------- differential vs. tables.grid
DIFFERENTIAL_SHAPES = {
    "horizontal_merge": table(tr(tc("表头", span=2)) + tr(tc("A"), tc("B"))),
    "vertical_merge": table(
        tr(tc("项目"), tc("金额"))
        + tr(tc("日均", vmerge="restart"), tc("100"))
        + tr(tc(None, vmerge="continue"), tc("200"))
    ),
    "combined_merge": table(
        tr(tc("合计", span=2, vmerge="restart"), tc("备注"))
        + tr(tc(None, span=2, vmerge="continue"), tc("说明")),
        cols=3,
    ),
    "grid_before_hole": table(tr(tc("整行", span=4)) + tr(tc("C"), tc("D"), before=2), cols=4),
    "nested_table": table(tr(tc("父格", nested=table(tr(tc("子格")), cols=1)), tc("旁格"))),
    "one_by_one": table(tr(tc("唯一格")), cols=1),
    "all_blank_cells": table(tr(tc(""), tc(None))),
}


@pytest.mark.parametrize("shape", sorted(DIFFERENTIAL_SHAPES))
def test_payload_is_identical_to_grid_to_contract_table(tmp_path: Path, shape: str):
    """Single source of truth: extraction must not post-process grid's payload.

    For every shape whose anchors span the full declared grid, re-deriving the
    table from its own anchors through ``to_contract_table`` must reproduce the
    payload key-for-key (cells order, slots, omitted_slots, n_rows, n_cols).
    """
    for payload in extract(tmp_path, DIFFERENTIAL_SHAPES[shape]):
        rebuilt = to_contract_table(
            payload["table_id"],
            payload["cells"],
            parent_cell_id=payload["parent_cell_id"],
            ir_ref=payload["ir_ref"],
            omission_evidence_ids=payload["omission_evidence_ids"],
        )
        assert rebuilt == payload


def test_the_only_divergence_is_a_grid_wider_than_its_anchors(tmp_path: Path):
    """Documented exception: trailing ``w:gridAfter`` has no anchor to derive from."""
    payload = one(extract(tmp_path, table(tr(tc("A"), after=2), cols=3)))
    rebuilt = to_contract_table(
        payload["table_id"],
        payload["cells"],
        omission_evidence_ids=payload["omission_evidence_ids"],
    )

    assert (rebuilt["n_rows"], rebuilt["n_cols"]) == (1, 1)
    assert (payload["n_rows"], payload["n_cols"]) == (1, 3)
    assert rebuilt["omitted_slots"] == []
    assert payload["omitted_slots"] == [(0, 1), (0, 2)]
    # Everything that is *not* grid geometry is still byte-identical to grid's output.
    for key in ("table_id", "ir_ref", "parent_cell_id", "cells", "cell_semantics",
                "notes", "source_html_ref", "structure_state", "review_decision_ref"):
        assert rebuilt[key] == payload[key]


# --------------------------------------------------------------- nested tables
def test_nested_table_is_a_separate_object_with_a_parent_link(tmp_path: Path):
    child = table(tr(tc("子格")), cols=1)
    body = table(tr(tc("父格", nested=child), tc("旁格")))
    payloads = extract(tmp_path, body)

    parent = [item for item in payloads if item["parent_cell_id"] is None]
    nested = [item for item in payloads if item["parent_cell_id"] is not None]
    assert len(parent) == 1 and len(nested) == 1
    parent_table, child_table = parent[0], nested[0]

    parent_cell = parent_table["cells"][0]
    assert parent_cell["nested_table_ids"] == [child_table["table_id"]]
    assert child_table["parent_cell_id"] == parent_cell["cell_id"]
    # The child's text stays in the child; the parent cell keeps only its own text.
    assert parent_cell["text"] == "父格"
    assert [cell["text"] for cell in child_table["cells"]] == ["子格"]
    assert parent_table["table_id"] != child_table["table_id"]


def test_no_tables_means_empty_result(tmp_path: Path):
    package = open_docx(write_docx(tmp_path / "none.docx", "<w:p><w:r><w:t>x</w:t></w:r></w:p>"))
    assert extract_tables(package, build_final_view(package)) == []


def test_missing_tbl_grid_is_derived_and_flagged_for_review(tmp_path: Path):
    body = table(tr(tc("A"), tc("B")) + tr(tc("C"), tc("D")), grid=False)
    payload = one(extract(tmp_path, body))

    assert (payload["n_rows"], payload["n_cols"]) == (2, 2)
    assert len(payload["notes"]) == 1
    note = payload["notes"][0]
    assert note["state"] == "NEEDS_REVIEW"
    assert note["scope"] == "TABLE"
    assert note["evidence_ids"] == [f"docx:tblGrid:derived:{payload['table_id']}"]


# ------------------------------------------------------- structural revisions
def test_cell_merge_revision_is_refused(tmp_path: Path):
    merged = (
        '<w:tc><w:tcPr><w:cellMerge w:vMerge="restart"/><w:gridSpan w:val="2"/></w:tcPr>'
        "<w:p><w:r><w:t>合并</w:t></w:r></w:p></w:tc>"
    )
    body = table(f"<w:tr>{merged}</w:tr>")
    with pytest.raises(ExtensionError) as excinfo:
        extract(tmp_path, body)
    error = excinfo.value
    assert error.code == "E_DOCX_REVISION_UNRESOLVED"
    assert any("cellMerge" in item for item in error.detail)


def test_structural_revision_is_refused_even_without_a_view(tmp_path: Path):
    """The guard must live in extraction, not only in the view hand-off."""
    merged = (
        '<w:tc><w:tcPr><w:cellMerge w:vMerge="restart"/><w:gridSpan w:val="2"/></w:tcPr>'
        "<w:p><w:r><w:t>合并</w:t></w:r></w:p></w:tc>"
    )
    package = open_docx(write_docx(tmp_path / "no-view.docx", table(f"<w:tr>{merged}</w:tr>")))
    with pytest.raises(ExtensionError) as excinfo:
        extract_tables(package)
    assert excinfo.value.code == "E_DOCX_REVISION_UNRESOLVED"
    assert any("cellMerge" in item for item in excinfo.value.detail)


def test_view_with_unresolved_structure_stops_extraction(tmp_path: Path):
    package = open_docx(write_docx(tmp_path / "ok.docx", table(tr(tc("A"), tc("B")))))
    view = FinalView(
        paragraphs=(),
        table_count=1,
        review_context={"deletions": [], "insertions": [], "moves": [], "comments": []},
        unresolved_structural=("word/document.xml:/x: w:cellMerge",),
    )
    with pytest.raises(ExtensionError) as excinfo:
        extract_tables(package, view)
    assert excinfo.value.code == "E_DOCX_REVISION_UNRESOLVED"


# ---------------------------------------------------------- revision-resolved text
def test_deleted_cell_text_is_excluded_from_the_table(tmp_path: Path):
    cell = (
        "<w:tc><w:p><w:r><w:t>保留</w:t></w:r>"
        '<w:del w:id="1" w:author="legal"><w:r><w:delText>撤掉</w:delText></w:r></w:del>'
        "</w:p></w:tc>"
    )
    payload = one(extract(tmp_path, table(tr(cell, tc("B")))))
    assert payload["cells"][0]["text"] == "保留"


# --------------------------------------------------------------- zero-write proof
def test_extraction_does_not_modify_the_source(tmp_path: Path):
    path = write_docx(tmp_path / "keep.docx", table(tr(tc("A"), tc("B"))))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    mtime = path.stat().st_mtime_ns

    package = open_docx(path)
    extract_tables(package, build_final_view(package))

    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest == package.source_sha256
    assert path.stat().st_mtime_ns == mtime
