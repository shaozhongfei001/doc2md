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


def tc(text: str) -> str:
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def tr(cells: str, *, header: bool = False) -> str:
    props = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
    return f"<w:tr>{props}{cells}</w:tr>"


def table(rows: str) -> str:
    grid = '<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="1000"/></w:tblGrid>'
    return f"<w:tbl>{grid}{rows}</w:tbl>"


TABLE_ONE = table(tr(tc("金额") + tc("比例"), header=True) + tr(tc("100") + tc("12%")))
TABLE_TWO = table(tr(tc("条目") + tc("数量"), header=True) + tr(tc("甲") + tc("7")))
TABLE_NO_HEADER = table(tr(tc("金额") + tc("比例")) + tr(tc("100") + tc("12%")))


def build_docx(path: Path, *, body: str = TABLE_ONE + TABLE_TWO) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
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
