"""Batch intake: one Document shape, explicit issues, resume by content hash."""

from __future__ import annotations

import json
import pathlib

import pytest

from app.extensions.intake import read, route
from app.extensions.intake.batch import main as batch_main
from app.extensions.intake.visual import normalise_page
from app.extensions.intake.writers import write_document

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DOCX = pathlib.Path(__file__).parents[1] / "fixtures" / "sample_dataset" / "office_like" / "最小文档.docx"


def test_an_unknown_suffix_is_unsupported_not_pdf(tmp_path):
    """Regression: the magic-byte loop used to ``return`` on its first miss.

    Every unknown file was silently routed as PDF, then failed in the PDF reader
    with ``PDF_TEXT_LAYER_FAILED`` — a misleading code for "no reader claims this".
    """
    path = tmp_path / "mystery.bin"
    path.write_bytes(b"not a document at all")

    kind, notes = route(path)
    assert kind == "unsupported"
    assert [note["code"] for note in notes] == ["UNKNOWN_FORMAT"]

    doc = read(path)
    assert doc.kind == "unsupported"
    assert "UNSUPPORTED_FORMAT" in [issue.code for issue in doc.issues]


def test_markdown_headings_become_blocks(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Title\n\nbody line\n\n## Sub\n\nmore\n", encoding="utf-8")

    doc = read(path)
    kinds = [(block.kind, block.level, block.text) for block in doc.blocks]
    assert ("heading", 1, "Title") in kinds
    assert ("heading", 2, "Sub") in kinds
    assert any(kind == "paragraph" and text == "body line" for kind, _, text in kinds)


def test_csv_becomes_a_grid_not_a_string(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("name,value\nrevenue,1204\nprofit,318\n", encoding="utf-8")

    doc = read(path)
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert (table.n_rows, table.n_cols) == (3, 2)
    assert [(cell["row"], cell["col"], cell["text"]) for cell in table.cells][:4] == [
        (0, 0, "name"), (0, 1, "value"), (1, 0, "revenue"), (1, 1, "1204"),
    ]


def test_a_json_record_array_also_becomes_a_grid(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}]), encoding="utf-8")

    doc = read(path)
    assert len(doc.tables) == 1
    assert doc.tables[0].n_rows == 3  # header + two records


def test_a_broken_json_keeps_the_text_and_reports_the_failure(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    doc = read(path)
    assert "JSON_PARSE_FAILED" in [issue.code for issue in doc.issues]
    assert "not json" in doc.text


def test_docx_text_and_native_tables_are_read():
    doc = read(DOCX)
    assert doc.kind == "docx"
    assert doc.text.strip(), "a docx with text must not come back empty"
    assert "TEXT_EXTRACTION_FAILED" not in [issue.code for issue in doc.issues]


def test_a_pdf_without_a_text_layer_asks_for_ocr(tmp_path):
    from PIL import Image

    image = Image.new("RGB", (60, 40), "white")
    pdf = tmp_path / "scan.pdf"
    image.save(pdf)

    doc = read(pdf)
    assert doc.meta.get("needs_ocr") is True
    assert "PDF_TEXT_LAYER_ABSENT" in [issue.code for issue in doc.issues]


def test_writing_creates_json_and_markdown_side_by_side(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("hello\n", encoding="utf-8")

    doc = read(source)
    paths = write_document(doc, tmp_path / "out")

    payload = json.loads(pathlib.Path(paths["document_json"]).read_text(encoding="utf-8"))
    assert payload["sha256"] == doc.sha256
    assert payload["schema_version"] == "doc2md-intake-document/1.0"
    assert pathlib.Path(paths["document_md"]).read_text(encoding="utf-8").startswith("# a.txt")


def test_batch_writes_every_file_and_resumes_by_hash(tmp_path):
    source = tmp_path / "docs"
    source.mkdir()
    (source / "a.txt").write_text("alpha\n", encoding="utf-8")
    (source / "b.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (source / "~$lock.docx").write_bytes(b"word lock file")

    out = tmp_path / "out"
    assert batch_main([str(source), "--out", str(out), "--no-visual"]) == 0
    manifest = (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest) == 2, "the Word lock file must be skipped, the rest recorded"

    assert batch_main([str(source), "--out", str(out), "--no-visual"]) == 0
    again = (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(again) == 2, "a second run must skip by content hash, not append duplicates"


def test_a_visual_page_is_converted_with_the_geometry_check():
    """Reuse of L38's fixture: the batch path must not have a second opinion."""
    payload = json.loads(
        (FIXTURES / "ppstructure_v370_table_cjk.json").read_text(encoding="utf-8")
    )

    page = normalise_page(payload, page_no=1)

    assert len(page["tables"]) == 1
    table = page["tables"][0]
    assert (table["n_rows"], table["n_cols"]) == (5, 4)
    assert [cell.get("text") for cell in table["cells"][:4]] == ["项目", "2023年", "2024年", "同比"]
    # The engine's own text wins over OCR, and each override is recorded. That is
    # expected on a clean page; only geometry failures would be a real problem.
    assert not [issue for issue in page["issues"] if issue["code"].startswith("CELL_BOX_")]


def test_a_visual_page_without_cell_geometry_is_marked_not_bound():
    """The grid from HTML is real; the *binding* is not, and must say so."""
    payload = {
        "table_res_list": [{"pred_html": "<table><tr><td>x</td></tr></table>", "cell_box_list": []}],
        "parsing_res_list": [],
    }

    page = normalise_page(payload, page_no=1)

    assert page["tables"], "HTML still describes a grid"
    assert page["tables"][0]["box_association"] == "UNAVAILABLE"
    assert "CELL_BOX_GEOMETRY_MISSING" in [issue["code"] for issue in page["issues"]]


def test_a_tables_only_visual_pass_does_not_shrink_the_document(tmp_path):
    """The visual cap must not rewrite the text layer's page count.

    First real run: a 300-page book reported ``page_count: 2`` because the visual
    pass overwrote the count with the number of pages it had been allowed to read.
    Zero tables is also normal here, so it must not raise an "empty" warning.
    """
    from app.extensions.intake import Document
    from app.extensions.intake.readers import apply_visual

    doc = Document(path="book.pdf", sha256="0" * 64, size=1, kind="pdf",
                   engine="pdftotext", page_count=312)

    apply_visual(
        tmp_path / "book.pdf",
        doc,
        {"pages": [{"page_index": 0, "blocks": [], "tables": [], "issues": []}]},
        tables_only=True,
    )

    assert doc.page_count == 312
    assert "VISUAL_ENGINE_EMPTY" not in [issue.code for issue in doc.issues]
