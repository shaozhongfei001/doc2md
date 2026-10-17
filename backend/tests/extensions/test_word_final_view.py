"""``word.final_view`` — FINAL text vs. review trail (L03 / T302).

The point of these cases: a clause that was *deleted* must never appear as
document text, while an *inserted* one must, and the deleted original must still
be inspectable in ``review_context``.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from app.extensions.word.final_view import build_final_view, element_path
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


def write_docx(path: Path, body: str, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return path


def build(tmp_path: Path, body: str, extra: dict[str, bytes] | None = None):
    package = open_docx(write_docx(tmp_path / "view.docx", body, extra))
    return package, build_final_view(package)


def para(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


# ------------------------------------------------------------------ plain text
def test_paragraphs_follow_document_order(tmp_path: Path):
    _, view = build(tmp_path, para("第一段") + para("第二段") + para("第三段"))
    assert view.paragraphs == ("第一段", "第二段", "第三段")
    assert view.text() == "第一段\n第二段\n第三段"
    assert view.unresolved_structural == ()
    assert view.table_count == 0


def test_text_runs_are_concatenated_within_a_paragraph(tmp_path: Path):
    body = (
        "<w:p><w:r><w:t>日</w:t></w:r><w:r><w:t>均</w:t></w:r>"
        "<w:r><w:t>存</w:t></w:r><w:r><w:t>款</w:t></w:r></w:p>"
    )
    _, view = build(tmp_path, body)
    assert view.paragraphs == ("日均存款",)


def test_tab_and_line_break_are_preserved(tmp_path: Path):
    body = (
        "<w:p><w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t></w:r></w:p>"
    )
    _, view = build(tmp_path, body)
    assert view.paragraphs == ("a\tb\nc",)


# --------------------------------------------------------------- text revisions
def test_inserted_text_is_part_of_final(tmp_path: Path):
    body = (
        '<w:p><w:r><w:t>额度</w:t></w:r>'
        '<w:ins w:id="1" w:author="reviewer" w:date="2026-10-01T00:00:00Z">'
        "<w:r><w:t>上限</w:t></w:r></w:ins></w:p>"
    )
    _, view = build(tmp_path, body)
    assert view.paragraphs == ("额度上限",)
    assert [entry["text"] for entry in view.review_context["insertions"]] == ["上限"]
    assert view.review_context["insertions"][0]["author"] == "reviewer"


def test_deleted_text_is_not_final_but_is_kept_for_review(tmp_path: Path):
    body = (
        "<w:p><w:r><w:t>本条款</w:t></w:r>"
        '<w:del w:id="2" w:author="legal" w:date="2026-10-02T00:00:00Z">'
        "<w:r><w:delText>仍然有效</w:delText></w:r></w:del>"
        "<w:r><w:t>已废止</w:t></w:r></w:p>"
    )
    _, view = build(tmp_path, body)

    assert view.paragraphs == ("本条款已废止",)
    assert "仍然有效" not in view.text()
    (deletion,) = view.review_context["deletions"]
    assert deletion["text"] == "仍然有效"
    assert deletion["author"] == "legal"
    assert deletion["part"] == "word/document.xml"
    assert deletion["element_path"].endswith("/w:del[1]")


def test_move_out_is_excluded_and_move_in_is_included(tmp_path: Path):
    body = (
        "<w:p>"
        '<w:moveFrom w:id="3" w:author="editor" w:name="move1">'
        "<w:r><w:t>旧位置</w:t></w:r></w:moveFrom>"
        '<w:moveTo w:id="4" w:author="editor" w:name="move1">'
        "<w:r><w:t>新位置</w:t></w:r></w:moveTo>"
        "</w:p>"
    )
    _, view = build(tmp_path, body)

    assert view.paragraphs == ("新位置",)
    moves = view.review_context["moves"]
    assert [entry["direction"] for entry in moves] == ["FROM", "TO"]
    assert [entry["text"] for entry in moves] == ["旧位置", "新位置"]


def test_revision_in_footnotes_keeps_its_part_name(tmp_path: Path):
    body = para("正文，见脚注")
    footnotes = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:footnotes xmlns:w="{W_NS}"><w:footnote w:id="1"><w:p>'
        '<w:del w:id="9" w:author="legal"><w:r><w:delText>脚注原文</w:delText></w:r></w:del>'
        "</w:p></w:footnote></w:footnotes>"
    ).encode("utf-8")
    _, view = build(tmp_path, body, {"word/footnotes.xml": footnotes})

    assert view.paragraphs == ("正文，见脚注",)
    (deletion,) = view.review_context["deletions"]
    assert deletion["text"] == "脚注原文"
    assert deletion["part"] == "word/footnotes.xml"
    assert deletion["element_path"].startswith("/w:footnotes")


def test_field_instruction_is_not_interpreted_but_result_is(tmp_path: Path):
    body = (
        "<w:p>"
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:t>7</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        "</w:p>"
    )
    _, view = build(tmp_path, body)
    assert view.paragraphs == ("7",)
    assert "PAGE" not in view.text()


# --------------------------------------------------------------------- comments
def test_comments_are_split_into_review_context(tmp_path: Path):
    comments = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:comments xmlns:w="{W_NS}">'
        '<w:comment w:id="0" w:author="风控" w:date="2026-10-03T00:00:00Z">'
        "<w:p><w:r><w:t>请补充口径</w:t></w:r></w:p></w:comment>"
        "</w:comments>"
    ).encode("utf-8")
    body = (
        "<w:p><w:r><w:t>日均存款</w:t></w:r>"
        '<w:commentRangeStart w:id="0"/><w:r><w:t>口径</w:t></w:r>'
        '<w:commentRangeEnd w:id="0"/></w:p>'
    )
    _, view = build(tmp_path, body, {"word/comments.xml": comments})

    assert view.paragraphs == ("日均存款口径",)
    (comment,) = view.review_context["comments"]
    assert comment["comment_id"] == "0"
    assert comment["author"] == "风控"
    assert comment["text"] == "请补充口径"


def test_absent_comments_part_yields_empty_list(tmp_path: Path):
    _, view = build(tmp_path, para("无批注"))
    assert view.review_context["comments"] == []


def test_review_context_always_has_the_four_tracks(tmp_path: Path):
    _, view = build(tmp_path, para("x"))
    assert set(view.review_context) == {"deletions", "insertions", "moves", "comments"}


# ------------------------------------------------------- structure and tables
def test_table_count_includes_nested_tables(tmp_path: Path):
    nested = (
        "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid>"
        "<w:tr><w:tc><w:p><w:r><w:t>子</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    body = (
        "<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>"
        f"<w:tr><w:tc><w:p><w:r><w:t>父</w:t></w:r></w:p></w:tc><w:tc>{nested}</w:tc></w:tr>"
        "</w:tbl>"
    )
    _, view = build(tmp_path, body)
    assert view.table_count == 2


def test_table_cell_paragraphs_are_not_body_paragraphs(tmp_path: Path):
    body = (
        para("正文")
        + "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid>"
        "<w:tr><w:tc><w:p><w:r><w:t>格内文字</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    _, view = build(tmp_path, body)
    assert view.paragraphs == ("正文",)


def test_cell_merge_is_reported_as_unresolved_not_guessed(tmp_path: Path):
    body = (
        "<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid><w:tr>"
        '<w:tc><w:tcPr><w:cellMerge w:vMerge="restart"/><w:gridSpan w:val="2"/></w:tcPr>'
        "<w:p><w:r><w:t>合并</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl>"
    )
    _, view = build(tmp_path, body)

    assert len(view.unresolved_structural) == 1
    note = view.unresolved_structural[0]
    assert "w:cellMerge" in note
    assert "word/document.xml:" in note
    assert "/w:tbl" in note


def test_deleted_row_is_reported_as_unresolved(tmp_path: Path):
    body = (
        "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid>"
        '<w:tr><w:trPr><w:del w:id="5" w:author="legal"/></w:trPr>'
        "<w:tc><w:p><w:r><w:t>整行删除</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
    )
    _, view = build(tmp_path, body)
    assert any("w:trPr/w:del" in note for note in view.unresolved_structural)


def test_property_history_is_reported_as_unresolved(tmp_path: Path):
    body = (
        "<w:tbl><w:tblPr><w:tblPrChange w:id=\"7\" w:author=\"editor\"/></w:tblPr>"
        "<w:tblGrid><w:gridCol/></w:tblGrid>"
        "<w:tr><w:tc><w:p><w:r><w:t>x</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    _, view = build(tmp_path, body)
    assert any("tblPrChange" in note for note in view.unresolved_structural)


def test_final_view_is_frozen(tmp_path: Path):
    _, view = build(tmp_path, para("frozen"))
    with pytest.raises(Exception):
        view.paragraphs = ()  # type: ignore[misc]


def test_element_path_is_reproducible(tmp_path: Path):
    package = open_docx(write_docx(tmp_path / "path.docx", para("a") + para("b")))
    from app.extensions.word.final_view import parse_xml_part

    root = parse_xml_part(package, "word/document.xml")
    body = [child for child in root if child.tag.endswith("}body")][0]
    paragraphs = [child for child in body if child.tag.endswith("}p")]
    assert element_path(paragraphs[0]) == "/w:document/w:body[1]/w:p[1]"
    assert element_path(paragraphs[1]) == "/w:document/w:body[1]/w:p[2]"


def test_reading_the_view_does_not_touch_the_source(tmp_path: Path):
    path = write_docx(tmp_path / "keep.docx", para("零写入"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    mtime = path.stat().st_mtime_ns

    package = open_docx(path)
    build_final_view(package)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest == package.source_sha256
    assert path.stat().st_mtime_ns == mtime
