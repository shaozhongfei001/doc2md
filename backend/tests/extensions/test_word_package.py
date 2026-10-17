"""``word.package`` — read-only OOXML container guards (L03 / T301).

Every case builds its own ``.docx`` inside ``tmp_path``. No repository fixture is
read, so the tests cannot pass because of some real business file.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.word import package as package_module
from app.extensions.word.package import (
    DOCX_MAX_MEMBER_BYTES,
    DOCX_MAX_MEMBERS,
    DOCX_MAX_TOTAL_BYTES,
    open_docx,
    parse_xml_part,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)


def document_xml(body: str = "<w:p/>") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    ).encode("utf-8")


def write_docx(
    path: Path,
    *,
    body: str = "<w:p/>",
    extra: dict[str, bytes] | None = None,
    document: bytes | None = None,
) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document if document is not None else document_xml(body))
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ happy path
def test_open_exposes_members_and_hashes(tmp_path: Path):
    path = write_docx(tmp_path / "a.docx", body="<w:p><w:r><w:t>x</w:t></w:r></w:p>")
    package = open_docx(path)

    assert package.member_names == ("[Content_Types].xml", "word/document.xml")
    assert package.has_part("word/document.xml") is True
    assert package.has_part("word/comments.xml") is False
    assert package.source_sha256 == sha256_file(path)
    assert len(package.source_sha256) == 64


def test_read_part_and_part_sha256_agree(tmp_path: Path):
    path = write_docx(tmp_path / "a.docx")
    package = open_docx(path)

    data = package.read_part("word/document.xml")
    assert data == document_xml()
    assert package.part_sha256("word/document.xml") == hashlib.sha256(data).hexdigest()
    assert package.part_sha256("word/document.xml") != package.source_sha256


def test_accepts_string_path(tmp_path: Path):
    path = write_docx(tmp_path / "a.docx")
    assert open_docx(str(path)).has_part("word/document.xml") is True


def test_missing_part_is_rejected(tmp_path: Path):
    package = open_docx(write_docx(tmp_path / "a.docx"))
    for call in (
        lambda: package.read_part("word/comments.xml"),
        lambda: package.part_sha256("word/comments.xml"),
        lambda: package.member_size("word/comments.xml"),
    ):
        with pytest.raises(ExtensionError) as excinfo:
            call()
        assert excinfo.value.code == "E_INPUT_INVALID"
        assert excinfo.value.detail["part"] == "word/comments.xml"


def test_parse_xml_part_returns_root(tmp_path: Path):
    package = open_docx(write_docx(tmp_path / "a.docx"))
    root = parse_xml_part(package, "word/document.xml")
    assert root.tag.endswith("}document")


# ------------------------------------------------------------- container guards
def test_missing_file_is_rejected(tmp_path: Path):
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(tmp_path / "absent.docx")
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_directory_is_rejected(tmp_path: Path):
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(tmp_path)
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_non_zip_is_rejected(tmp_path: Path):
    path = tmp_path / "plain.docx"
    path.write_bytes(b"this is not a zip container")
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "zip" in excinfo.value.message


def test_zip_without_main_part_is_rejected(tmp_path: Path):
    path = tmp_path / "notdocx.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/styles.xml", b"<styles/>")
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert excinfo.value.detail["expected"] == "word/document.xml"


def test_empty_zip_is_rejected(tmp_path: Path):
    path = tmp_path / "empty.docx"
    with zipfile.ZipFile(path, "w"):
        pass
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"


@pytest.mark.parametrize(
    ("member", "needle"),
    [
        ("../evil.xml", "escap"),
        ("word/../../evil.xml", "escap"),
        ("/evil.xml", "absolute"),
        ("word\\evil.xml", "backslash"),
        ("C:/evil.xml", "drive"),
    ],
)
def test_unsafe_member_names_are_rejected(tmp_path: Path, member: str, needle: str):
    path = write_docx(tmp_path / "bad.docx", extra={member: b"<x/>"})
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert needle in excinfo.value.message
    assert excinfo.value.detail["member"] == member


def test_duplicate_member_names_are_rejected(tmp_path: Path):
    path = tmp_path / "dup.docx"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES)
            archive.writestr("word/document.xml", document_xml())
            archive.writestr("word/document.xml", document_xml("<w:p><w:r><w:t>2</w:t></w:r></w:p>"))
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "duplicate" in excinfo.value.message


def test_member_count_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(package_module, "DOCX_MAX_MEMBERS", 8)
    extra = {f"word/media/f{index}.xml": b"<x/>" for index in range(10)}
    path = write_docx(tmp_path / "many.docx", extra=extra)
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "member count" in excinfo.value.message


def test_single_member_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(package_module, "DOCX_MAX_MEMBER_BYTES", 2048)
    path = write_docx(tmp_path / "big.docx", extra={"word/media/big.bin": b"a" * 4096})
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "per-member limit" in excinfo.value.message


def test_total_bytes_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(package_module, "DOCX_MAX_TOTAL_BYTES", 4096)
    monkeypatch.setattr(package_module, "DOCX_MAX_MEMBER_BYTES", 4096)
    extra = {f"word/media/{index}.bin": os.urandom(2048) for index in range(4)}
    path = write_docx(tmp_path / "total.docx", extra=extra)
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "total byte limit" in excinfo.value.message


def test_compression_ratio_bomb_is_rejected(tmp_path: Path):
    path = write_docx(tmp_path / "bomb.docx", extra={"word/media/zeros.bin": b"\0" * 8_000_000})
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "compression ratio" in excinfo.value.message
    assert excinfo.value.detail["limit"] == package_module.DOCX_MAX_COMPRESSION_RATIO


def test_doctype_is_rejected(tmp_path: Path):
    payload = (
        '<?xml version="1.0"?><!DOCTYPE w:document SYSTEM "file:///etc/passwd">'
        f'<w:document xmlns:w="{W_NS}"><w:body/></w:document>'
    ).encode("utf-8")
    path = write_docx(tmp_path / "dtd.docx", document=payload)
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "DTD" in excinfo.value.message


def test_entity_declaration_is_rejected(tmp_path: Path):
    payload = (
        '<?xml version="1.0"?><!ENTITY xxe SYSTEM "file:///etc/passwd">'
        f'<w:document xmlns:w="{W_NS}"><w:body/></w:document>'
    ).encode("utf-8")
    path = write_docx(tmp_path / "ent.docx", document=payload)
    with pytest.raises(ExtensionError) as excinfo:
        open_docx(path)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "DTD" in excinfo.value.message


def test_malformed_xml_part_is_rejected(tmp_path: Path):
    path = write_docx(tmp_path / "broken.docx", document=b"<w:document><w:body></w:document>")
    package = open_docx(path)
    with pytest.raises(ExtensionError) as excinfo:
        parse_xml_part(package, "word/document.xml")
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "well-formed" in excinfo.value.message


# -------------------------------------------------------------- zero-write proof
def test_source_is_never_modified(tmp_path: Path):
    path = write_docx(
        tmp_path / "readonly.docx",
        body="<w:p><w:r><w:t>原文不可变</w:t></w:r></w:p>",
        extra={"word/comments.xml": f'<w:comments xmlns:w="{W_NS}"/>'.encode()},
    )
    path.chmod(0o444)
    digest_before = sha256_file(path)
    stat_before = path.stat()
    listing_before = sorted(item.name for item in tmp_path.iterdir())

    package = open_docx(path)
    assert package.read_part("word/document.xml")
    assert package.part_sha256("word/comments.xml")
    assert parse_xml_part(package, "word/comments.xml") is not None

    stat_after = path.stat()
    assert sha256_file(path) == digest_before == package.source_sha256
    assert stat_after.st_mtime_ns == stat_before.st_mtime_ns
    assert stat_after.st_size == stat_before.st_size
    assert sorted(item.name for item in tmp_path.iterdir()) == listing_before


def test_part_swap_after_open_is_detected(tmp_path: Path):
    path = write_docx(tmp_path / "swap.docx")
    package = open_docx(path)
    write_docx(path, body="<w:p><w:r><w:t>swapped</w:t></w:r></w:p>")
    with pytest.raises(ExtensionError) as excinfo:
        package.read_part("word/document.xml")
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "changed after it was opened" in excinfo.value.message


def test_limits_are_the_published_defaults():
    """Members/total mirror ``limits.*`` in the extension config example."""
    assert (DOCX_MAX_MEMBERS, DOCX_MAX_TOTAL_BYTES) == (10_000, 512 * 1024 * 1024)
    assert DOCX_MAX_MEMBER_BYTES == 100 * 1024 * 1024
    assert package_module.DOCX_MAX_COMPRESSION_RATIO == 200.0
