"""T901/T902/T904: the header band, column widths and page cache that L05 needs.

The negative case that matters most: a table **without** ``w:tblHeader`` must
report an empty band. "It is the first row and it is bold" is not evidence.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.tables.semantics import derive_header_paths
from app.extensions.word.native_tables import extract_tables
from app.extensions.word.package import open_docx
from app.extensions.word.table_flags import (
    HEADER_SOURCE_ABSENT,
    HEADER_SOURCE_NATIVE,
    PAGE_SOURCE_CACHE,
    PAGE_SOURCE_UNAVAILABLE,
    flags_for,
    header_rows_by_table,
    read_table_flags,
)

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


def tc(text: str) -> str:
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def tr(cells: str, *, header: bool = False) -> str:
    props = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
    return f"<w:tr>{props}{cells}</w:tr>"


def table(rows: str, *, widths: tuple[int, ...] = (2000, 2000)) -> str:
    grid = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{width}"/>' for width in widths) + "</w:tblGrid>"
    return f"<w:tbl>{grid}{rows}</w:tbl>"


def page_break_paragraph() -> str:
    return '<w:p><w:r><w:lastRenderedPageBreak/><w:t>page</w:t></w:r></w:p>'


# ------------------------------------------------------------------ header band
def test_tbl_header_marks_the_header_rows(tmp_path):
    body = table(tr(tc("金额") + tc("比例"), header=True) + tr(tc("100") + tc("12%")))
    package = open_docx(write_docx(tmp_path / "h.docx", body))
    flags = read_table_flags(package)
    assert len(flags) == 1
    assert flags[0].header_rows == (0,)
    assert flags[0].header_source == HEADER_SOURCE_NATIVE
    assert "docx:tblHeader:0:0" in flags[0].evidence_ids


def test_table_without_the_mark_reports_no_band(tmp_path):
    # Same text, same position, no w:tblHeader: nothing may be inferred.
    body = table(tr(tc("金额") + tc("比例")) + tr(tc("100") + tc("12%")))
    package = open_docx(write_docx(tmp_path / "n.docx", body))
    flags = read_table_flags(package)
    assert flags[0].header_rows == ()
    assert flags[0].header_source == HEADER_SOURCE_ABSENT


def test_multiple_header_rows_are_all_reported(tmp_path):
    body = table(
        tr(tc("金额") + tc("金额"), header=True)
        + tr(tc("人民币") + tc("外币"), header=True)
        + tr(tc("100") + tc("20"))
    )
    package = open_docx(write_docx(tmp_path / "m.docx", body))
    assert read_table_flags(package)[0].header_rows == (0, 1)


# --------------------------------------------------------------------- widths
def test_grid_columns_become_normalised_ratios(tmp_path):
    body = table(tr(tc("a") + tc("b")), widths=(3000, 1000))
    package = open_docx(write_docx(tmp_path / "w.docx", body))
    flags = read_table_flags(package)
    assert flags[0].column_widths == pytest.approx((0.75, 0.25))
    assert flags[0].column_width_units == "TWIPS"


def test_missing_grid_leaves_widths_unset(tmp_path):
    body = f"<w:tbl>{tr(tc('a') + tc('b'))}</w:tbl>"
    package = open_docx(write_docx(tmp_path / "nogrid.docx", body))
    assert read_table_flags(package)[0].column_widths is None


@pytest.mark.parametrize("width", ["0", "-5", "abc", ""])
def test_invalid_grid_widths_are_not_fabricated(tmp_path, width):
    body = f'<w:tbl><w:tblGrid><w:gridCol w:w="{width}"/><w:gridCol w:w="2000"/></w:tblGrid>{tr(tc("a") + tc("b"))}</w:tbl>'
    package = open_docx(write_docx(tmp_path / f"bad-{width or 'empty'}.docx", body))
    assert read_table_flags(package)[0].column_widths is None


# ------------------------------------------------------------------ page cache
def test_page_index_comes_from_the_rendered_break_cache(tmp_path):
    body = page_break_paragraph() + table(tr(tc("a") + tc("b")))
    package = open_docx(write_docx(tmp_path / "p.docx", body))
    flags = read_table_flags(package)
    assert flags[0].first_page_index == 1
    assert flags[0].page_source == PAGE_SOURCE_CACHE


def test_no_rendered_break_means_the_page_is_unknown_not_zero(tmp_path):
    body = table(tr(tc("a") + tc("b")))
    package = open_docx(write_docx(tmp_path / "np.docx", body))
    flags = read_table_flags(package)
    assert flags[0].first_page_index is None
    assert flags[0].page_source == PAGE_SOURCE_UNAVAILABLE


# ------------------------------------------------------- ordering / alignment
def test_flags_align_with_extract_tables_including_nested(tmp_path):
    nested = table(tr(tc("n1") + tc("n2")))
    body = table(tr(tc("a") + tc(f"outer{nested}"))) + table(tr(tc("b") + tc("c")))
    package = open_docx(write_docx(tmp_path / "o.docx", body))
    tables = extract_tables(package)
    flags = read_table_flags(package)
    assert len(flags) == len(tables)
    assert [flag.table_id for flag in flags] == [table["table_id"] for table in tables]


def test_lookup_of_an_unknown_index_is_an_error(tmp_path):
    body = table(tr(tc("a") + tc("b")))
    package = open_docx(write_docx(tmp_path / "x.docx", body))
    flags = read_table_flags(package)
    with pytest.raises(ExtensionError):
        flags_for(flags, 9)


def test_mapping_helper_returns_header_rows_per_table(tmp_path):
    body = table(tr(tc("金额") + tc("比例"), header=True) + tr(tc("1") + tc("2")))
    package = open_docx(write_docx(tmp_path / "hdr.docx", body))
    mapping = header_rows_by_table(read_table_flags(package))
    assert mapping == {0: (0,)}


# ------------------------------------------------- T904: end-to-end reachability
def test_header_band_reaches_derive_header_paths(tmp_path):
    """The gap being closed: without a producer, this chain produced no paths."""
    body = table(tr(tc("金额") + tc("比例"), header=True) + tr(tc("100") + tc("12%")))
    package = open_docx(write_docx(tmp_path / "e2e.docx", body))
    tables = extract_tables(package)
    flags = read_table_flags(package)

    result = derive_header_paths(tables[0], header_rows=list(flags[0].header_rows))
    assert result.cell_semantics, "a data cell must be present"
    data = result.cell_semantics[0]
    assert data["column_header_cell_ids"], "header path must be non-empty with a real band"
    assert data["state"] == "CANDIDATE"

    # And the counter-case: no native mark, no path, no guessing.
    plain = open_docx(write_docx(tmp_path / "plain.docx", table(tr(tc("金额") + tc("比例")) + tr(tc("1") + tc("2")))))
    plain_flags = read_table_flags(plain)
    plain_paths = derive_header_paths(extract_tables(plain)[0], header_rows=list(plain_flags[0].header_rows))
    assert plain_paths.cell_semantics[0]["column_header_cell_ids"] == []
    assert plain_paths.cell_semantics[0]["state"] == "NEEDS_REVIEW"
