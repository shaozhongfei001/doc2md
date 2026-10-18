"""T903/T904: footnote text reaches ``bind_notes`` with a located target.

Before this producer existed, ``bind_notes`` could only bind notes handed to it by
a caller — real Word footnotes never arrived, so the capability was tested but
unreachable. The tests below assert both directions: text is preserved, and a note
without a reference stays ``UNRESOLVED`` instead of being attached to the nearest
table.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.extensions.tables.notes import bind_notes
from app.extensions.word.native_tables import extract_tables
from app.extensions.word.notes_extract import read_notes
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


def notes_xml(entries: str, *, kind: str = "footnote") -> bytes:
    container = "footnotes" if kind == "footnote" else "endnotes"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:{container} xmlns:w="{W_NS}">{entries}</w:{container}>'
    ).encode("utf-8")


def note(ref_id: str, text: str, *, kind: str = "footnote", note_type: str | None = None) -> str:
    element = "footnote" if kind == "footnote" else "endnote"
    type_attr = f' w:type="{note_type}"' if note_type else ""
    return f'<w:{element}{type_attr} w:id="{ref_id}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:{element}>'


def tc(text: str, *, ref: str | None = None, kind: str = "footnote") -> str:
    reference = ""
    if ref is not None:
        element = "footnoteReference" if kind == "footnote" else "endnoteReference"
        reference = f'<w:r><w:{element} w:id="{ref}"/></w:r>'
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r>{reference}</w:p></w:tc>"


def table(rows: str) -> str:
    grid = '<w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid>'
    return f"<w:tbl>{grid}{rows}</w:tbl>"


def write_docx(path: Path, body: str, *, footnotes: str | None = None, endnotes: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
        if footnotes is not None:
            archive.writestr("word/footnotes.xml", notes_xml(footnotes))
        if endnotes is not None:
            archive.writestr("word/endnotes.xml", notes_xml(endnotes, kind="endnote"))
    return path


BODY_WITH_REF = table(f"<w:tr>{tc('金额', ref='1')}{tc('比例')}</w:tr>")


# ----------------------------------------------------------------- extraction
def test_footnote_text_and_reference_are_located(tmp_path):
    package = open_docx(
        write_docx(
            tmp_path / "fn.docx",
            BODY_WITH_REF,
            footnotes=note("1", "含外币折人民币，按期末汇率折算。"),
        )
    )
    result = read_notes(package)
    assert len(result.notes) == 1
    extracted = result.notes[0]
    assert extracted.text == "含外币折人民币，按期末汇率折算。"
    assert extracted.kind == "FOOTNOTE"
    assert extracted.target_cell_id == "TBL-0001-R0000C0000"
    assert extracted.anchor == (0, 0, 0)


def test_separator_entries_are_not_content(tmp_path):
    package = open_docx(
        write_docx(
            tmp_path / "sep.docx",
            BODY_WITH_REF,
            footnotes=note("-1", "", note_type="separator") + note("1", "真正的内容"),
        )
    )
    result = read_notes(package)
    assert [n.text for n in result.notes] == ["真正的内容"]


def test_empty_separator_produces_no_issue(tmp_path):
    """QA-TEST-02: a separator is not content, so it must not be reported as empty.

    The sibling test above only asserts the note *text* list, and an empty separator
    is filtered by the empty-text branch anyway — so dropping the separator check
    entirely would still pass it. This asserts the *absence of an issue*, which is
    the part that actually distinguishes "skipped on purpose" from "skipped by
    accident".
    """
    package = open_docx(
        write_docx(
            tmp_path / "sep-empty.docx",
            BODY_WITH_REF,
            footnotes=note("-1", "", note_type="separator") + note("1", "真正的内容"),
        )
    )
    result = read_notes(package)
    assert result.issues == []


def test_separator_with_text_is_still_not_content(tmp_path):
    """QA-TEST-02: Word and some generators emit separators carrying text.

    Those carry text, so the empty-text branch cannot hide a missing separator
    check: without the type filter the note leaks out as a candidate.
    """
    package = open_docx(
        write_docx(
            tmp_path / "sep-text.docx",
            BODY_WITH_REF,
            footnotes=note("-1", "SEPARATOR-LINE", note_type="separator") + note("1", "真正的内容"),
        )
    )
    result = read_notes(package)
    assert [n.text for n in result.notes] == ["真正的内容"]
    assert result.issues == []
    assert all(candidate["text"] != "SEPARATOR-LINE" for candidate in result.candidates())


def test_note_without_a_reference_stays_unresolved(tmp_path):
    package = open_docx(
        write_docx(tmp_path / "orphan.docx", table(f"<w:tr>{tc('金额')}{tc('比例')}</w:tr>"),
                   footnotes=note("7", "没有引用的脚注"))
    )
    result = read_notes(package)
    candidate = result.candidates()[0]
    assert candidate["scope"] == "UNRESOLVED"
    assert candidate["target_ids"] == []
    assert any(issue["code"] == "NOTE_WITHOUT_REFERENCE" for issue in result.issues)


def test_reference_without_text_is_reported(tmp_path):
    package = open_docx(
        write_docx(tmp_path / "missing.docx", BODY_WITH_REF, footnotes=note("1", ""))
    )
    result = read_notes(package)
    assert result.notes == []
    assert any(issue["code"] == "REFERENCE_WITHOUT_NOTE" for issue in result.issues)


def test_endnotes_are_extracted_too(tmp_path):
    body = table(f"<w:tr>{tc('金额', ref='2', kind='endnote')}{tc('比例')}</w:tr>")
    package = open_docx(
        write_docx(tmp_path / "en.docx", body, endnotes=note("2", "尾注内容", kind="endnote"))
    )
    result = read_notes(package)
    assert result.notes[0].kind == "ENDNOTE"
    assert result.notes[0].target_cell_id == "TBL-0001-R0000C0000"


def test_document_without_note_parts_is_not_an_error(tmp_path):
    package = open_docx(write_docx(tmp_path / "none.docx", BODY_WITH_REF))
    result = read_notes(package)
    assert result.notes == []
    assert any(issue["code"] == "REFERENCE_WITHOUT_NOTE" for issue in result.issues)


def test_text_is_preserved_verbatim(tmp_path):
    text = "（1）仅限        小微企业；不含贴现。"
    package = open_docx(write_docx(tmp_path / "v.docx", BODY_WITH_REF, footnotes=note("1", text)))
    assert read_notes(package).notes[0].text == text


# ------------------------------------------------- T904: end-to-end reachability
def test_extracted_notes_bind_to_the_table_cell(tmp_path):
    package = open_docx(
        write_docx(tmp_path / "e2e.docx", BODY_WITH_REF, footnotes=note("1", "含外币折人民币。"))
    )
    table_payload = extract_tables(package)[0]
    extraction = read_notes(package)

    bound = bind_notes(table_payload, extraction.candidates())
    assert bound.notes, "the producer must yield at least one bindable note"
    note_result = bound.notes[0]
    assert note_result["scope"] == "CELL"
    assert note_result["target_ids"] == ["TBL-0001-R0000C0000"]
    assert note_result["state"] == "CANDIDATE"
    assert note_result["text"] == "含外币折人民币。"
    assert bound.publishable, "a resolved, evidenced note must be publishable"


def test_unresolved_producer_output_does_not_become_publishable(tmp_path):
    package = open_docx(
        write_docx(tmp_path / "e2e2.docx", table(f"<w:tr>{tc('金额')}{tc('比例')}</w:tr>"),
                   footnotes=note("9", "孤立的脚注"))
    )
    table_payload = extract_tables(package)[0]
    bound = bind_notes(table_payload, read_notes(package).candidates())
    assert bound.notes[0]["state"] == "NEEDS_REVIEW"
    assert bound.publishable == []


# ------------------------------ L18/T1801: an unresolved note keeps its evidence
def test_an_unresolved_note_carries_its_own_evidence(tmp_path):
    """MB4 (qa-l18): an empty ``evidence_ids`` was invisible to every assertion.

    The only nearby test checked ``state == "NEEDS_REVIEW"``, which is already true
    for any ``scope="UNRESOLVED"`` note — so the missing evidence was masked by the
    fact that the note was blocked for a *different* reason.
    """
    package = open_docx(
        write_docx(tmp_path / "unres.docx", BODY_WITH_REF, footnotes=note("7", "孤儿脚注。"))
    )
    extraction = read_notes(package)
    candidate = extraction.candidates()[0]
    assert candidate["scope"] == "UNRESOLVED"
    assert candidate["evidence_ids"] == ["docx:footnote:7"]

    table_payload = extract_tables(package)[0]
    bound = bind_notes(table_payload, [candidate])
    codes = [issue.get("code") for issue in bound.issues]
    assert "MISSING_EVIDENCE" not in codes, "an unresolved note must not lose its provenance"
