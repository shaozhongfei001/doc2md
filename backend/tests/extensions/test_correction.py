"""T1201–T1204: one correction must produce a new version, not a new claim.

Every test here starts from a *real* ``.docx`` through ``assemble_package`` — a
hand-built ``AssemblyResult`` would prove the wiring works only against the shape
I imagined, which is the mistake L10 existed to avoid.
"""

from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.io.artifact_store import ArtifactStore
from app.extensions.io.paths import Roots
from app.extensions.pipeline import assemble_package, write_assembly
from app.extensions.review.correction import Correction, apply_correction, require_publishable
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


def tc(text: str, *, ref: str | None = None) -> str:
    reference = f'<w:r><w:footnoteReference w:id="{ref}"/></w:r>' if ref else ""
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r>{reference}</w:p></w:tc>"


def tr(cells: str, *, header: bool = False) -> str:
    props = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
    return f"<w:tr>{props}{cells}</w:tr>"


def table(rows: str) -> str:
    grid = '<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="1000"/></w:tblGrid>'
    return f"<w:tbl>{grid}{rows}</w:tbl>"


TABLE_ONE = table(tr(tc("金额") + tc("比例"), header=True) + tr(tc("100") + tc("12%")))
TABLE_TWO = table(tr(tc("条目") + tc("数量"), header=True) + tr(tc("甲") + tc("7")))
TABLE_NO_HEADER = table(tr(tc("金额") + tc("比例")) + tr(tc("100") + tc("12%")))


def build_docx(
    path: Path,
    *,
    body: str = TABLE_ONE + TABLE_TWO,
    footnotes: dict[str, str] | None = None,
) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
        if footnotes:
            entries = "".join(
                f'<w:footnote w:id="{ref_id}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:footnote>'
                for ref_id, text in footnotes.items()
            )
            archive.writestr(
                "word/footnotes.xml",
                (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<w:footnotes xmlns:w="{W_NS}">{entries}</w:footnotes>'
                ).encode("utf-8"),
            )
    return path


@pytest.fixture()
def assembly(tmp_path):
    path = build_docx(tmp_path / "doc.docx")
    return assemble_package(open_docx(path), generation_id="GEN-1")


def header_rows(assembly) -> dict[str, list[int]]:
    # The pipeline read the band from ``w:tblHeader``; the correction path must be
    # given the same declaration rather than re-deriving it.
    return {table["table_id"]: [0] for table in assembly.tables}


def first_data_cell_id(assembly, table_offset: int = 0) -> str:
    table = assembly.tables[table_offset]
    data_cells = [cell for cell in table["cells"] if cell["row"] > 0]
    return data_cells[0]["cell_id"]


def payload_digests(assembly) -> dict[str, str]:
    return {
        table["table_id"]: hashlib.sha256(
            json.dumps(table, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        for table in assembly.tables
    }


# --------------------------------------------------------------------- T1201
def test_correction_derives_new_semantics_for_the_touched_table(assembly):
    cell_id = first_data_cell_id(assembly)
    correction = Correction(
        object_id=cell_id,
        path=("text",),
        value="150",
        reason="1601 原始凭证为 150",
        review_decision_ref="RV-1",
    )

    outcome = apply_correction(
        assembly,
        correction,
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )

    table = outcome.assembly.tables[0]
    corrected = [cell for cell in table["cells"] if cell["cell_id"] == cell_id][0]
    assert corrected["text"] == "150"
    assert outcome.assembly.generation_id == "GEN-2"
    assert outcome.affected_tables == (table["table_id"],)
    assert outcome.publishable is True
    assert "150" in outcome.assembly.packets[0].markdown


def test_correction_without_a_review_decision_is_refused(assembly):
    cell_id = first_data_cell_id(assembly)
    correction = Correction(
        object_id=cell_id, path=("text",), value="150", reason="x", review_decision_ref=""
    )
    with pytest.raises(ExtensionError) as excinfo:
        apply_correction(
            assembly,
            correction,
            generation_id="GEN-2",
            header_rows_by_table=header_rows(assembly),
        )
    assert excinfo.value.code == "E_REVIEW_REQUIRED"
    assert "review_decision_ref" in str(excinfo.value)


def test_correction_without_a_reason_is_refused(assembly):
    correction = Correction(
        object_id=first_data_cell_id(assembly),
        path=("text",),
        value="150",
        reason="   ",
        review_decision_ref="RV-1",
    )
    with pytest.raises(ExtensionError) as excinfo:
        apply_correction(
            assembly,
            correction,
            generation_id="GEN-2",
            header_rows_by_table=header_rows(assembly),
        )
    assert excinfo.value.code == "E_REVIEW_REQUIRED"


def test_unknown_field_is_refused_instead_of_created(assembly):
    correction = Correction(
        object_id=first_data_cell_id(assembly),
        path=("txet",),  # typo
        value="150",
        reason="x",
        review_decision_ref="RV-1",
    )
    with pytest.raises(ExtensionError) as excinfo:
        apply_correction(
            assembly,
            correction,
            generation_id="GEN-2",
            header_rows_by_table=header_rows(assembly),
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "unknown field" in str(excinfo.value)
    assert "text" in str(excinfo.value.detail)


def test_retyping_a_field_is_refused(assembly):
    # Found by a TL probe: ``text = 150`` (int) was accepted and stayed publishable.
    # It renders without quotes, so the failure surfaces at the contract boundary
    # and looks like someone else's bug.
    correction = Correction(
        object_id=first_data_cell_id(assembly),
        path=("text",),
        value=150,
        reason="x",
        review_decision_ref="RV-1",
    )
    with pytest.raises(ExtensionError) as excinfo:
        apply_correction(
            assembly,
            correction,
            generation_id="GEN-2",
            header_rows_by_table=header_rows(assembly),
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "changes the field type" in str(excinfo.value)


def test_unknown_table_and_unknown_cell_are_refused(assembly):
    for object_id in ("table:T404", "cell-does-not-exist"):
        with pytest.raises(ExtensionError) as excinfo:
            apply_correction(
                assembly,
                Correction(
                    object_id=object_id,
                    path=("text",),
                    value="1",
                    reason="x",
                    review_decision_ref="RV-1",
                ),
                generation_id="GEN-2",
                header_rows_by_table=header_rows(assembly),
            )
        assert excinfo.value.code == "E_INPUT_INVALID"


def test_unknown_change_kind_is_refused(assembly):
    correction = Correction(
        object_id=first_data_cell_id(assembly),
        path=("text",),
        value="1",
        reason="x",
        review_decision_ref="RV-1",
        change_kind="MAGIC",
    )
    with pytest.raises(ExtensionError) as excinfo:
        apply_correction(
            assembly,
            correction,
            generation_id="GEN-2",
            header_rows_by_table=header_rows(assembly),
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "unknown change kind" in str(excinfo.value)


def test_reusing_the_generation_id_is_refused(assembly):
    correction = Correction(
        object_id=first_data_cell_id(assembly),
        path=("text",),
        value="1",
        reason="x",
        review_decision_ref="RV-1",
    )
    with pytest.raises(ExtensionError) as excinfo:
        apply_correction(
            assembly,
            correction,
            generation_id=assembly.generation_id,
            header_rows_by_table=header_rows(assembly),
        )
    assert "new generation_id" in str(excinfo.value)


# --------------------------------------------------------------------- T1202
def test_missing_header_rows_refuses_instead_of_guessing(tmp_path):
    path = build_docx(tmp_path / "noheader.docx", body=TABLE_NO_HEADER)
    assembly = assemble_package(open_docx(path), generation_id="GEN-1")
    correction = Correction(
        object_id=first_data_cell_id(assembly),
        path=("text",),
        value="150",
        reason="x",
        review_decision_ref="RV-1",
    )
    with pytest.raises(ExtensionError) as excinfo:
        apply_correction(
            assembly,
            correction,
            generation_id="GEN-2",
            header_rows_by_table={},  # nothing declared
        )
    assert excinfo.value.code == "E_REVIEW_REQUIRED"
    assert "guessing them from formatting" in str(excinfo.value)


# --------------------------------------------------------------------- T1203
def test_unaffected_tables_are_byte_identical(assembly):
    before = payload_digests(assembly)
    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )
    after = payload_digests(outcome.assembly)

    touched = outcome.affected_tables[0]
    assert after[touched] != before[touched], "the corrected table must actually change"
    for table_id, digest in before.items():
        if table_id == touched:
            continue
        assert after[table_id] == digest, f"table {table_id} was rebuilt but should not be"


def test_the_original_assembly_is_not_mutated(assembly):
    snapshot = copy.deepcopy([dict(table) for table in assembly.tables])
    packets_before = list(assembly.packets)

    apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )

    assert [dict(table) for table in assembly.tables] == snapshot
    assert assembly.packets == packets_before
    assert assembly.generation_id == "GEN-1"


def test_notes_are_carried_over_and_flagged_as_not_rebound(assembly):
    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )
    assert outcome.notes_rebound is False
    assert outcome.assembly.stats["notes_rebound"] is False
    assert outcome.assembly.notes == list(assembly.notes)
    assert outcome.assembly.stats["corrections"][-1]["review_decision_ref"] == "RV-1"


def test_table_level_correction_writes_a_table_field(assembly):
    table_id = assembly.tables[0]["table_id"]
    outcome = apply_correction(
        assembly,
        Correction(
            object_id=f"table:{table_id}",
            path=("structure_state",),
            value="VALID_CANDIDATE",
            reason="复核确认网格完整",
            review_decision_ref="RV-2",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )
    assert outcome.assembly.tables[0]["structure_state"] == "VALID_CANDIDATE"


def test_applying_the_same_correction_twice_yields_the_same_digest(assembly):
    correction = Correction(
        object_id=first_data_cell_id(assembly),
        path=("text",),
        value="150",
        reason="x",
        review_decision_ref="RV-1",
    )
    kwargs = {
        "generation_id": "GEN-2",
        "header_rows_by_table": header_rows(assembly),
    }
    first = apply_correction(assembly, correction, **kwargs)
    second = apply_correction(assembly, correction, **kwargs)
    assert first.rebuild.digest() == second.rebuild.digest()


# ------------------------------------------------------------ publish guard
def test_a_failed_rebuild_is_not_publishable(assembly, monkeypatch):
    from app.extensions.review import correction as module

    def explode(*args, **kwargs):
        raise ExtensionError("E_TABLE_SPAN_INVALID", "grid is broken")

    monkeypatch.setattr(module, "derive_header_paths", explode)

    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )
    assert outcome.publishable is False
    assert outcome.rebuild.failed[0].error["code"] == "E_TABLE_SPAN_INVALID"

    with pytest.raises(ExtensionError) as excinfo:
        require_publishable(outcome)
    assert excinfo.value.code == "E_REVIEW_REQUIRED"
    assert "pre-correction" in str(excinfo.value)


# ---------------------------------------------------------------- T1204 E2E
def test_correcting_one_cell_writes_a_new_generation_and_leaves_the_old_one(tmp_path):
    doc = build_docx(tmp_path / "doc.docx")
    source_digest = hashlib.sha256(doc.read_bytes()).hexdigest()
    assembly = assemble_package(open_docx(doc), generation_id="GEN-1")

    extension = tmp_path / "ext"
    extension.mkdir()
    store = ArtifactStore(Roots(extension=extension))
    write_assembly(store, assembly)
    old_reports = sorted(extension.rglob("report.json"))
    assert len(old_reports) == 1
    old_bytes = old_reports[0].read_bytes()

    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="1601 原始凭证为 150",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )
    require_publishable(outcome)
    write_assembly(store, outcome.assembly)

    reports = sorted(extension.rglob("report.json"))
    assert len(reports) == 2, "the corrected generation must be a new directory"
    assert old_bytes in [path.read_bytes() for path in reports]
    new_bytes = [path.read_bytes() for path in reports if path.read_bytes() != old_bytes]
    assert new_bytes and b"150" in new_bytes[0]
    assert hashlib.sha256(doc.read_bytes()).hexdigest() == source_digest


# ------------------------------------------------- L13: note re-binding (T1301)
TABLE_NOTE_ONE = table(
    tr(tc("金额") + tc("比例"), header=True) + tr(tc("100", ref="1") + tc("12%"))
)
TABLE_NOTE_TWO = table(
    tr(tc("条目") + tc("数量"), header=True) + tr(tc("甲", ref="2") + tc("7"))
)


@pytest.fixture()
def noted(tmp_path):
    """Two tables, one footnote each: enough to prove re-binding is table-scoped."""
    path = build_docx(
        tmp_path / "noted.docx",
        body=TABLE_NOTE_ONE + TABLE_NOTE_TWO,
        footnotes={"1": "含外币折人民币。", "2": "含税。"},
    )
    return assemble_package(open_docx(path), generation_id="GEN-1"), path


def candidates_of(path) -> list[dict]:
    from app.extensions.word.notes_extract import read_notes

    return read_notes(open_docx(path)).candidates()


def test_supplying_candidates_rebinds_the_notes(noted):
    assembly, path = noted
    assert len(assembly.notes) == 2, "the fixture must actually carry two notes"

    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="1601 原始凭证为 150",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
        note_candidates=candidates_of(path),
    )

    assert outcome.notes_rebound is True
    assert outcome.assembly.stats["notes_rebound"] is True
    assert len(outcome.assembly.notes) == 2
    assert outcome.assembly.stats["notes"] == 2
    codes = [issue.get("code") for issue in outcome.assembly.tables[0]["assembly_issues"]]
    assert "NOTES_NOT_REBOUND" not in codes


def test_without_candidates_the_flag_is_still_false(noted):
    assembly, _ = noted
    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )
    assert outcome.notes_rebound is False
    assert outcome.assembly.stats["notes_rebound"] is False
    assert outcome.assembly.notes == list(assembly.notes)


def test_rebinding_does_not_touch_the_other_table_s_notes(noted):
    assembly, path = noted
    before = [dict(note) for note in assembly.tables[1].get("notes") or ()]
    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
        note_candidates=candidates_of(path),
    )
    after = [dict(note) for note in outcome.assembly.tables[1].get("notes") or ()]
    assert after == before


def test_flat_note_list_matches_table_order(noted):
    assembly, path = noted
    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
        note_candidates=candidates_of(path),
    )
    per_table = [len(table.get("notes") or ()) for table in outcome.assembly.tables]
    assert sum(per_table) == len(outcome.assembly.notes)
    assert per_table == [1, 1]


def test_notes_dropped_on_rebind_are_visible(noted):
    # An empty candidate list is a legitimate input (notes were deleted upstream),
    # but the resulting loss must not be silent.
    assembly, _ = noted
    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
        note_candidates=[],
    )
    assert outcome.notes_rebound is True
    issues = outcome.assembly.tables[0]["assembly_issues"]
    dropped = [issue for issue in issues if issue.get("code") == "NOTES_DROPPED_ON_REBIND"]
    assert dropped, "losing a footnote must produce an issue, not a quiet success"
    assert dropped[0]["before"] and not dropped[0]["after"]


def test_an_unattached_note_is_not_duplicated_across_tables(tmp_path):
    """TL probe P5: one orphan footnote came out as *two* bound notes.

    ``pipeline._note_candidates_for`` appends a target-less note to every table, so
    a footnote referenced by no cell showed up once per table. The count said two
    conditions where the document has one. Fixed here; the same defect remains in
    ``pipeline`` and is registered as ``L13-FIND-01``.
    """
    path = build_docx(
        tmp_path / "orphan.docx",
        body=TABLE_ONE + TABLE_TWO,
        footnotes={"9": "孤儿脚注。"},
    )
    assembly = assemble_package(open_docx(path), generation_id="GEN-1")

    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
        note_candidates=candidates_of(path),
    )

    ids = [note["note_id"] for note in outcome.assembly.notes]
    assert len(ids) == len(set(ids)), f"one footnote must not become several notes: {ids}"
    assert len(ids) == 1


def test_the_remove_duplicate_defence_does_not_fire_in_normal_flow(tmp_path):
    """After L14 the duplicate is prevented upstream, so this path must stay quiet."""
    path = build_docx(
        tmp_path / "orphan3.docx", body=TABLE_ONE + TABLE_TWO, footnotes={"9": "孤儿脚注。"}
    )
    assembly = assemble_package(open_docx(path), generation_id="GEN-1")
    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
        note_candidates=candidates_of(path),
    )
    codes = [issue.get("code") for issue in outcome.assembly.issues]
    assert "DUPLICATE_NOTE_ID_DEDUPED" not in codes


def test_the_duplicate_defence_still_reports_when_it_does_fire(tmp_path):
    """Defensive: an assembly that already carries a duplicate must not be counted twice."""
    path = build_docx(
        tmp_path / "orphan4.docx", body=TABLE_ONE + TABLE_TWO, footnotes={"9": "孤儿脚注。"}
    )
    assembly = assemble_package(open_docx(path), generation_id="GEN-1")
    # Simulate a caller/older build that copied the note into a second table.
    assembly.tables[1]["notes"] = [dict(note) for note in assembly.tables[0]["notes"]]

    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly),
            path=("text",),
            value="150",
            reason="x",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
        note_candidates=candidates_of(path),
    )
    ids = [note["note_id"] for note in outcome.assembly.notes]
    assert len(ids) == len(set(ids))
    deduped = [i for i in outcome.assembly.issues if i.get("code") == "DUPLICATE_NOTE_ID_DEDUPED"]
    assert deduped and deduped[0]["note_id"] == "FN-9"


# ------------------------------------------- L16: RFC6902 patch chain (T1601+)
def corrected(assembly, **overrides):
    kwargs = {
        "object_id": first_data_cell_id(assembly),
        "path": ("text",),
        "value": "150",
        "reason": "1601 原始凭证为 150",
        "review_decision_ref": "RV-1",
    }
    kwargs.update(overrides)
    return apply_correction(
        assembly,
        Correction(**kwargs),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
    )


def test_a_cell_correction_yields_a_test_then_replace_pair(assembly):
    outcome = corrected(assembly)
    paths = [op["path"] for op in outcome.patch]
    assert [op["op"] for op in outcome.patch] == ["test", "replace"]
    assert len(set(paths)) == 1 and paths[0].endswith("/text")
    assert outcome.patch[0]["value"] != outcome.patch[1]["value"]
    assert outcome.fully_patchable is True


def test_the_patch_reconstructs_the_corrected_document(assembly):
    # The strong claim: old artifact + patch == new data, so "this patch is the
    # change" is verifiable rather than asserted.
    from app.extensions.review.patches import preview_patch

    outcome = corrected(assembly)
    old_document = {
        "tables": [dict(table) for table in assembly.tables],
        "notes": [dict(note) for note in assembly.notes],
        "issues": [dict(issue) for issue in assembly.issues],
    }
    preview = preview_patch(
        old_document,
        outcome.patch,
        base_generation_id="GEN-1",
        expected_current_generation_id="GEN-1",
        current_generation_id="GEN-1",
        reviewer="RV-1",
        reason="replay",
    )
    assert preview.document["tables"] == [dict(table) for table in outcome.assembly.tables]
    assert preview.document["notes"] == [dict(note) for note in outcome.assembly.notes]


def test_an_unpatchable_change_is_reported_not_dropped(assembly):
    # ``row`` is a real field but is not in PATCHABLE_PATHS; the correction still
    # happens, and the fact that no patch can describe it must be visible.
    outcome = corrected(assembly, path=("row",), value=9)
    assert outcome.patch == ()
    assert outcome.fully_patchable is False
    paths = [item["path"] for item in outcome.unpatchable]
    assert any(path.endswith("/row") for path in paths)
    assert outcome.assembly.stats["patch"]["fully_patchable"] is False


def test_evidence_fields_are_never_patchable(assembly):
    # PATCHABLE_PATHS does not list evidence, and FORBIDDEN_FRAGMENTS covers it.
    cell_id = first_data_cell_id(assembly)
    current = [
        cell["evidence_ids"]
        for table in assembly.tables
        for cell in table["cells"]
        if cell["cell_id"] == cell_id
    ][0]
    outcome = corrected(assembly, object_id=cell_id, path=("evidence_ids",), value=[*current, "E-X"])
    assert outcome.patch == ()
    assert any("evidence" in item["path"].lower() for item in outcome.unpatchable)


def test_a_no_op_correction_produces_no_patch(assembly):
    cell_id = first_data_cell_id(assembly)
    current = [
        cell["text"]
        for table in assembly.tables
        for cell in table["cells"]
        if cell["cell_id"] == cell_id
    ][0]
    outcome = corrected(assembly, object_id=cell_id, value=current)
    assert outcome.patch == ()
    assert outcome.patch_digest is None
    assert outcome.fully_patchable is True


def test_the_patch_digest_is_recorded_and_stable(assembly):
    first = corrected(assembly)
    second = corrected(assembly)
    assert first.patch_digest == second.patch_digest
    assert first.assembly.stats["patch"]["digest"] == first.patch_digest
    assert first.assembly.stats["patch"]["ops"] == len(first.patch)


def test_patch_paths_stay_inside_the_reviewer_whitelist(assembly):
    from app.extensions.review.patches import FORBIDDEN_FRAGMENTS, PATCHABLE_PATHS

    outcome = corrected(assembly)
    for op in outcome.patch:
        path = op["path"]
        assert not any(fragment in path.lower() for fragment in FORBIDDEN_FRAGMENTS), path
        assert any(__import__("re").match(pattern, path) for pattern in PATCHABLE_PATHS), path
        assert "/generation_id" not in path and "/stats" not in path


# --------------------------- L17/T1702: the non-first table branch of the L13 fix
def test_an_orphan_note_stays_out_of_a_non_first_table(tmp_path):
    """MB5 (qa-l17): every earlier case corrected the *first* table.

    ``_note_candidates_for`` gates unbound notes on ``table_index == 0``; with every
    test hitting table 0, the ``False`` branch — the whole point of the L13 fix —
    never ran, so ``if table_index == 0`` could be replaced by ``if True`` and all
    1365 tests stayed green while one footnote was counted twice.
    """
    path = build_docx(
        tmp_path / "orphan_second.docx",
        body=TABLE_NOTE_ONE + TABLE_NOTE_TWO,
        footnotes={"9": "孤儿脚注。"},
    )
    assembly = assemble_package(open_docx(path), generation_id="GEN-1")
    assert [len(table.get("notes") or ()) for table in assembly.tables] == [1, 0]

    outcome = apply_correction(
        assembly,
        Correction(
            object_id=first_data_cell_id(assembly, table_offset=1),
            path=("text",),
            value="8",
            reason="第二张表订正",
            review_decision_ref="RV-1",
        ),
        generation_id="GEN-2",
        header_rows_by_table=header_rows(assembly),
        note_candidates=candidates_of(path),
    )

    assert [len(table.get("notes") or ()) for table in outcome.assembly.tables] == [1, 0]
    codes = [issue.get("code") for issue in outcome.assembly.issues]
    assert "DUPLICATE_NOTE_ID_DEDUPED" not in codes
    ids = [note["note_id"] for note in outcome.assembly.notes]
    assert len(ids) == len(set(ids))


# ----------------------- L17/T1703: the equivalence self-check must be reachable
def test_a_patch_that_fails_to_reconstruct_the_document_is_refused(assembly, monkeypatch):
    """MB10 (qa-l17): the fail-closed check existed but nothing ever reached it.

    A guard that is declared and never executed is worse than none: the code reads
    safer than it is. This forces ``_build_patch`` to see a residual difference by
    degrading the preview to "no change applied".
    """
    import types

    from app.extensions.review import correction as module

    def lossy_preview(document, patch, **kwargs):
        return types.SimpleNamespace(document=copy.deepcopy(dict(document)))

    monkeypatch.setattr(module, "preview_patch", lossy_preview)

    with pytest.raises(ExtensionError) as excinfo:
        corrected(assembly)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "does not reconstruct" in str(excinfo.value)


# --------------- L19/T1901: attribution is checked even when nothing changes
def test_a_no_op_correction_still_needs_an_attributable_decision(assembly):
    """MB1 (qa-l19): the existing test only covered a *value-changing* correction.

    That path happens to be caught twice — the top-level guard and, further down,
    ``preview_patch``'s own "reviewer must come from the local auth context" check.
    A no-op correction produces no ops, so ``_build_patch`` returns early and never
    reaches ``preview_patch``: with the top-level guard removed it was accepted and
    recorded with ``review_decision_ref: ''``.
    """
    cell_id = first_data_cell_id(assembly)
    current = [
        cell["text"]
        for table in assembly.tables
        for cell in table["cells"]
        if cell["cell_id"] == cell_id
    ][0]

    with pytest.raises(ExtensionError) as excinfo:
        apply_correction(
            assembly,
            Correction(
                object_id=cell_id,
                path=("text",),
                value=current,  # same value: no data change, no patch
                reason="x",
                review_decision_ref="",
            ),
            generation_id="GEN-2",
            header_rows_by_table=header_rows(assembly),
        )

    assert excinfo.value.code == "E_REVIEW_REQUIRED"
    assert "review_decision_ref" in str(excinfo.value)


# ---------------- L19/T1902: the two attribution implementations must agree
def test_the_two_note_attribution_implementations_agree():
    """MB5's fix exists twice — in ``pipeline`` and in ``correction``.

    Duplication is deliberate (reaching into another module's private helper makes a
    rename silently change behaviour), but deliberate duplication still drifts. This
    pins that both classify the same candidates identically for every table index,
    including the ``table_index == 0`` gate that a previous round proved untested.
    """
    from app.extensions.pipeline import _note_candidates_for as from_pipeline
    from app.extensions.review.correction import _note_candidates_for as from_correction

    candidates = [
        {"note_id": "N-T1", "target_ids": ["TBL-0001-R0000C0000"]},
        {"note_id": "N-T2", "target_ids": ["TBL-0002-R0000C0000"]},
        {"note_id": "N-ORPHAN", "target_ids": []},
    ]
    for index in range(3):
        table_id = f"TBL-000{index + 1}"
        assert from_pipeline(index, table_id, candidates) == from_correction(
            index, table_id, candidates
        ), f"attribution differs between the two implementations at table index {index}"
