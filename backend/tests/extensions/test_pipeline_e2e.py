"""T1001/T1002/T1004: one real ``.docx`` through the whole extension path.

The point of this file is not coverage breadth — it is proving that the nine
islands actually compose: a table with a native header band and a footnote must
come out the other end with a header path, a bound note, a rendered packet, and a
committed artifact — while the source file stays byte-identical and ``--dry-run``
writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.io.artifact_store import ArtifactStore
from app.extensions.io.paths import Roots
from app.extensions.pipeline import (
    MISSING_CONTRACT_FIELDS,
    _evidence_candidates,
    assemble_package,
    write_assembly,
)
from app.extensions.runner import WriteGuard
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


def footnotes_xml(entries: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:footnotes xmlns:w="{W_NS}">{entries}</w:footnotes>'
    ).encode("utf-8")


def tc(text: str, *, ref: str | None = None) -> str:
    reference = f'<w:r><w:footnoteReference w:id="{ref}"/></w:r>' if ref else ""
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r>{reference}</w:p></w:tc>"


def tr(cells: str, *, header: bool = False) -> str:
    props = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
    return f"<w:tr>{props}{cells}</w:tr>"


def table(rows: str) -> str:
    grid = '<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="1000"/></w:tblGrid>'
    return f"<w:tbl>{grid}{rows}</w:tbl>"


def note(ref_id: str, text: str) -> str:
    return f'<w:footnote w:id="{ref_id}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:footnote>'


def build_docx(path: Path, *, header: bool = True, with_note: bool = True) -> Path:
    body = table(
        tr(tc("金额") + tc("比例"), header=header)
        + tr(tc("100", ref="1" if with_note else None) + tc("12%"))
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
        if with_note:
            archive.writestr("word/footnotes.xml", footnotes_xml(note("1", "含外币折人民币。")))
    return path


# ------------------------------------------------------------------ assembly
def test_one_document_reaches_a_header_path_and_a_bound_note(tmp_path):
    package = open_docx(build_docx(tmp_path / "doc.docx"))
    result = assemble_package(package, generation_id="GEN-1")

    assert result.stats["tables"] == 1
    assert result.stats["tables_with_header_band"] == 1
    assert result.stats["cells_with_header_path"] >= 1, "header band must produce paths"
    assert result.stats["notes"] == 1
    assert result.stats["notes_publishable"] == 1

    table = result.tables[0]
    assert table["cell_semantics"][0]["column_header_cell_ids"], "path must be non-empty"
    assert table["notes"][0]["state"] == "CANDIDATE"
    assert table["notes"][0]["target_ids"] == ["TBL-0001-R0001C0000"]


def test_packet_contains_the_header_and_the_note(tmp_path):
    package = open_docx(build_docx(tmp_path / "doc.docx"))
    result = assemble_package(package, generation_id="GEN-1")
    markdown = result.markdown
    assert "金额" in markdown
    assert "含外币折人民币。" in markdown
    assert "复核包" in markdown


def test_without_a_native_header_band_no_path_is_claimed(tmp_path):
    package = open_docx(build_docx(tmp_path / "plain.docx", header=False, with_note=False))
    result = assemble_package(package, generation_id="GEN-2")
    table = result.tables[0]
    assert table["cell_semantics"][0]["column_header_cell_ids"] == []
    assert table["cell_semantics"][0]["state"] == "NEEDS_REVIEW"
    assert result.stats["tables_with_header_band"] == 0


def test_artifact_declares_what_it_is_not(tmp_path):
    package = open_docx(build_docx(tmp_path / "doc.docx"))
    payload = assemble_package(package, generation_id="GEN-1").payload()
    # Not a DocumentEnhancement, and the artifact says so explicitly.
    assert payload["schema"] == "doc2md-extension-assembly/0.1"
    assert payload["missing_contract_fields"] == list(MISSING_CONTRACT_FIELDS)
    assert any("geometries" in item for item in payload["missing_contract_fields"])


def test_stats_match_the_payload(tmp_path):
    package = open_docx(build_docx(tmp_path / "doc.docx"))
    result = assemble_package(package, generation_id="GEN-1")
    payload = result.payload()
    assert result.stats["tables"] == len(payload["tables"])
    assert result.stats["notes"] == len(payload["notes"])
    assert result.stats["issues"] == len(payload["issues"])


# ------------------------------------------------------------------ committing
def test_commit_writes_report_and_packet_under_the_extension_root(tmp_path):
    extension = tmp_path / "ext"
    extension.mkdir()
    store = ArtifactStore(Roots(extension=extension))

    package = open_docx(build_docx(tmp_path / "doc.docx"))
    result = assemble_package(package, generation_id="GEN-1")
    receipt = write_assembly(store, result)

    assert set(receipt["files"]) == {"report.json", "review_packet.md", "manifest.json"}
    committed = list(extension.rglob("report.json"))
    assert committed, "report must exist after commit"
    payload = json.loads(committed[0].read_text(encoding="utf-8"))
    assert payload["generation_id"] == "GEN-1"
    assert payload["stats"]["notes"] == 1


def test_source_document_is_never_modified(tmp_path):
    path = build_docx(tmp_path / "doc.docx")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    package = open_docx(path)
    result = assemble_package(package, generation_id="GEN-1")
    extension = tmp_path / "ext"
    extension.mkdir()
    write_assembly(ArtifactStore(Roots(extension=extension)), result)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_dry_run_guard_blocks_every_write(tmp_path):
    extension = tmp_path / "ext-dry"
    guard = WriteGuard(dry_run=True)
    store = ArtifactStore(Roots(extension=extension), write_guard=guard)

    package = open_docx(build_docx(tmp_path / "doc.docx"))
    result = assemble_package(package, generation_id="GEN-1")

    with pytest.raises(ExtensionError) as excinfo:
        write_assembly(store, result)
    assert excinfo.value.code == "E_DRY_RUN_WRITE_BLOCKED"
    assert not extension.exists() or not any(extension.rglob("*")), "dry-run wrote something"


def test_identical_recommit_is_idempotent(tmp_path):
    extension = tmp_path / "ext"
    extension.mkdir()
    store = ArtifactStore(Roots(extension=extension))
    package = open_docx(build_docx(tmp_path / "doc.docx"))
    result = assemble_package(package, generation_id="GEN-1")

    first = write_assembly(store, result)
    second = write_assembly(store, result)

    assert first["files"] == second["files"]
    assert second["receipt"].get("already_committed") is True


def test_different_content_never_overwrites_an_existing_generation(tmp_path):
    """A generation is keyed by (content sha, generation id), not by id alone.

    So a *different* document reusing the same generation id does not collide — it
    lands in its own directory. The property worth pinning is that the first
    generation stays byte-identical, because that is what "immutable" means here.
    (Discovering this required the test to fail first: the store does *not* raise
    ``E_VERSION_CONFLICT`` for that input, and asserting otherwise would have been
    a test that never matched the code.)
    """
    extension = tmp_path / "ext"
    extension.mkdir()
    store = ArtifactStore(Roots(extension=extension))

    first = assemble_package(open_docx(build_docx(tmp_path / "a.docx")), generation_id="GEN-1")
    first_receipt = write_assembly(store, first)
    first_report = next(extension.rglob("report.json"))
    snapshot = first_report.read_bytes()

    second = assemble_package(
        open_docx(build_docx(tmp_path / "b.docx", with_note=False)), generation_id="GEN-1"
    )
    second_receipt = write_assembly(store, second)

    assert first_receipt["files"] != second_receipt["files"]
    assert first_report.read_bytes() == snapshot, "the first generation was rewritten"
    assert len(list(extension.rglob("report.json"))) == 2


# ------------------------------------------------------------------------ CLI
def _base_config(tmp_path: Path) -> Path:
    """Minimal legacy config: the runner only reads its ``storage`` section."""
    for name in ("raw_vault", "parsed", "curated"):
        (tmp_path / name).mkdir(exist_ok=True)
    path = tmp_path / "app.yaml"
    path.write_text(
        "app:\n  pipeline_version: fixture\n"
        "storage:\n"
        "  raw_vault_root: ./raw_vault\n"
        "  parsed_root: ./parsed\n"
        "  curated_root: ./curated\n",
        encoding="utf-8",
    )
    return path


def _cli(tmp_path: Path, extension_config, *rest: str) -> int:
    from app.extensions.cli import main

    return main(
        [
            "--extension-config",
            str(extension_config),
            "--base-config",
            str(_base_config(tmp_path)),
            *rest,
            "--output-json",
        ]
    )


def test_cli_assemble_dry_run_is_the_default_and_writes_nothing(
    tmp_path, extension_config, extension_root, capsys
):
    docx = build_docx(tmp_path / "cli-dry.docx")
    code = _cli(tmp_path, extension_config, "--dry-run", "assemble", "--docx", str(docx), "--generation-id", "GEN-D1")
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["executed"] is False
    assert payload["assembly"]["stats"]["tables"] == 1
    assert not any(extension_root.rglob("*")), "dry-run created files"


def test_cli_assemble_commits_when_dry_run_is_absent(
    tmp_path, extension_config, extension_root, capsys
):
    docx = build_docx(tmp_path / "cli-real.docx")
    code = _cli(tmp_path, extension_config, "assemble", "--docx", str(docx), "--generation-id", "GEN-R1")
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["executed"] is True
    assert payload["commit"]["generation_id"] == "GEN-R1"
    assert list(extension_root.rglob("manifest.json"))
    assert list(extension_root.rglob("review_packet.md"))


def test_document_without_tables_still_gets_an_honest_packet(tmp_path):
    """An empty packet file and "no tables found" are different statements."""
    body = "<w:p><w:r><w:t>本文档只有段落，没有表格。</w:t></w:r></w:p>"
    path = tmp_path / "para-only.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))

    result = assemble_package(open_docx(path), generation_id="GEN-P1")
    assert result.stats["tables"] == 0
    assert "未检出原生 Word 表格" in result.markdown
    assert result.source_sha256 in result.markdown


def test_an_unattached_footnote_binds_once_and_stays_visible(tmp_path):
    """L13-FIND-01: one orphan footnote must not become one note per table.

    The note has no cell reference, so it cannot be attributed to a table. It is
    attached to the first table and marked ``NEEDS_REVIEW``: visible, counted once,
    and not silently deleted.
    """
    body = table(tr(tc("金额") + tc("比例"), header=True) + tr(tc("100") + tc("12%"))) + table(
        tr(tc("条目") + tc("数量"), header=True) + tr(tc("甲") + tc("7"))
    )
    path = tmp_path / "orphan.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
        archive.writestr("word/footnotes.xml", footnotes_xml(note("9", "孤儿脚注。")))

    result = assemble_package(open_docx(path), generation_id="GEN-1")

    assert result.stats["notes"] == 1, "one footnote is one note, not one per table"
    assert [len(table_item.get("notes") or ()) for table_item in result.tables] == [1, 0]
    assert result.notes[0]["state"] == "NEEDS_REVIEW", "it must stay visible, not be dropped"


def test_a_document_with_footnotes_but_no_table_is_refused(tmp_path):
    """L14-FIND-02: a note with nowhere to live must fail closed, not vanish.

    The contract hangs ``notes`` off ``Table``. Assembling anyway produced an
    artifact whose ``notes`` list was empty while the document clearly carried a
    footnote — a quietly incomplete result is worse than a refusal.
    """
    body = '<w:p><w:r><w:t>正文</w:t></w:r><w:r><w:footnoteReference w:id="9"/></w:r></w:p>'
    path = tmp_path / "notable.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
        archive.writestr("word/footnotes.xml", footnotes_xml(note("9", "正文脚注。")))

    with pytest.raises(ExtensionError) as excinfo:
        assemble_package(open_docx(path), generation_id="GEN-1")

    assert excinfo.value.code == "E_REVIEW_REQUIRED"
    assert "no table to carry them" in str(excinfo.value)
    assert excinfo.value.detail["note_ids"], "the refusal must name what would be dropped"
    assert excinfo.value.detail["tables"] == 0


def test_a_document_without_tables_or_footnotes_still_assembles(tmp_path):
    # The guard must not become a blanket refusal: an empty document is a valid
    # input, and "this document has no tables" is a real statement.
    body = "<w:p><w:r><w:t>只有正文</w:t></w:r></w:p>"
    path = tmp_path / "plain.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))

    result = assemble_package(open_docx(path), generation_id="GEN-1")

    assert result.tables == []
    assert result.notes == []
    assert result.stats["tables"] == 0
    assert "未检出原生 Word 表格" in result.markdown


# ------------------- L18/T1802: the staged-file ledger must actually be checked
def test_a_generation_missing_a_declared_file_is_refused(tmp_path, monkeypatch):
    """MB7 (qa-l18): ``expected_files`` was never *false*, so the check never ran.

    ``write_assembly`` stages and immediately commits, so the staging directory is
    always intact and the ledger check is vacuously satisfied. Losing a staged file
    between the two steps is exactly what the ledger exists for; without the check
    the artifact is published as complete while missing ``report.json``.
    """
    doc = build_docx(tmp_path / "doc.docx")
    assembly = assemble_package(open_docx(doc), generation_id="GEN-1")

    extension = tmp_path / "ext"
    extension.mkdir()
    store = ArtifactStore(Roots(extension=extension))
    original = store.write_staged

    def lossy(attempt_id, relative_path, data):
        staged = original(attempt_id, relative_path, data)
        if relative_path == "report.json":
            (store.staging_dir(attempt_id) / relative_path).unlink()
        return staged

    monkeypatch.setattr(store, "write_staged", lossy)

    with pytest.raises(ExtensionError) as excinfo:
        write_assembly(store, assembly)

    assert excinfo.value.code == "E_COMMIT_INCOMPLETE"
    published = [path for path in extension.rglob("manifest.json") if "staging" not in str(path)]
    assert published == [], "a generation missing a declared file must not be published"


# ------------- L18/T1803: two references in one cell must not duplicate evidence
def test_two_references_in_one_cell_do_not_duplicate_the_cell_evidence(tmp_path):
    """MB8 (qa-l18): every fixture had at most one reference per cell.

    Two references in the same cell both yield ``docx:footnoteReference:<cell>``.
    Without de-duplication the packet layer rejects the (valid) document for a
    duplicate id — so a qualified document turned into a hard failure, and no test
    could tell.
    """
    cell = (
        "<w:tc><w:p><w:r><w:t>100</w:t></w:r>"
        '<w:r><w:footnoteReference w:id="1"/></w:r>'
        '<w:r><w:footnoteReference w:id="2"/></w:r></w:p></w:tc>'
    )
    body = table(tr(tc("金额") + tc("比例"), header=True) + tr(cell + tc("12%")))
    path = tmp_path / "two_refs.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
        archive.writestr(
            "word/footnotes.xml",
            footnotes_xml(note("1", "甲。") + note("2", "乙。")),
        )

    # Assert on the candidate list *before* assembling: under the de-duplication
    # mutation the packet layer would refuse the document first, so a post-assembly
    # assertion would never run and the test would go red on the call line instead
    # of on the claim it is making.
    package = open_docx(path)
    table_payload = extract_tables(package)[0]
    candidates = _evidence_candidates(read_notes(package).candidates())
    ids = [candidate["candidate_evidence_id"] for candidate in candidates]
    cell_evidence = [item for item in ids if item.startswith("docx:footnoteReference:")]
    assert len(cell_evidence) == 1, f"cell evidence must appear once, got {cell_evidence}"

    result = assemble_package(package, generation_id="GEN-1")

    assert len(result.notes) == 2, "both references must still bind"
    evidence_ids = result.packets[0].payload["evidence_ids"]
    cell_ids = [item for item in evidence_ids if item.startswith("docx:footnoteReference:")]
    assert len(cell_ids) == 1, f"packet evidence must appear once, got {cell_ids}"


# ------------- L23/T2303: the known-evidence set must be document-level, not self-made
def _table_and_notes(tmp_path):
    from app.extensions.word.native_tables import extract_tables
    from app.extensions.word.notes_extract import read_notes

    path = build_docx(tmp_path / "ev.docx")
    package = open_docx(path)
    return extract_tables(package)[0], read_notes(package).candidates()


def test_an_omission_evidence_id_that_no_note_registered_is_refused(tmp_path):
    """qa-l20 4(e): ``_evidence_candidates`` used to fold the table's own omission ids
    into the "known" set, so ``omission ⊆ known`` was true by construction and the
    contract check could never fire.

    Only the negative half is not enough (a blanket refusal would also make it red),
    so the positive half is asserted in the next test.
    """
    from app.extensions.render.review_packet import build_review_packet

    table, notes = _table_and_notes(tmp_path)
    table = dict(table, omission_evidence_ids=["EV-GHOST"])

    with pytest.raises(ExtensionError) as excinfo:
        build_review_packet(
            table,
            semantics=[],
            notes=[],
            evidence_candidates=_evidence_candidates(notes),
            context={},
        )

    assert excinfo.value.code == "E_REVIEW_PACKET_INVALID"
    assert excinfo.value.detail["missing"] == ["EV-GHOST"]


def test_an_omission_evidence_id_registered_by_a_note_is_accepted(tmp_path):
    # The paired half: a registered id must pass, and the omission list must survive.
    from app.extensions.render.review_packet import build_review_packet

    from app.extensions.tables.notes import bind_notes

    table, candidates = _table_and_notes(tmp_path)
    bound = bind_notes(table, candidates)
    registered = bound.notes[0]["evidence_ids"][0]
    table = dict(table, omission_evidence_ids=[registered])

    packet = build_review_packet(
        table,
        semantics=[],
        notes=bound.notes,
        evidence_candidates=_evidence_candidates(bound.notes),
        context={},
    )

    assert packet.payload["omission_evidence_ids"] == [registered]
