"""T804: the seams between loops, exercised in one chain.

This is not a substitute for bank joint testing — it only proves that the modules
built in L03–L08 hand data to each other without a translation layer and without
violating the invariants each loop established (no guessing, no dangling ids, no
writes outside the extension root).
"""

from __future__ import annotations

import copy
import os

import pytest

from app.extensions.evidence.candidates import AccessGuard, read_candidate, register_candidate
from app.extensions.identity import source_version_id
from app.extensions.render.review_packet import build_review_packet
from app.extensions.review.dependencies import DependencyGraph, reverse_patch
from app.extensions.review.patches import apply_patch, patch_digest
from app.extensions.review.decisions import record_decision, is_decision_valid
from app.extensions.release.consumers import Outbox
from app.extensions.release.reconcile import reconcile
from app.extensions.release.switch import activate, prepare_release
from app.extensions.tables.grid import to_contract_table
from app.extensions.tables.notes import bind_notes, link_cell_note_ids
from app.extensions.tables.semantics import derive_header_paths

DIGEST = "a" * 64
EVIDENCE = [
    {"candidate_evidence_id": "EV-CELL"},
    {"candidate_evidence_id": "EV-NOTE"},
]


def table() -> dict:
    cells = [
        {"cell_id": "h0", "row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "金额", "text_origin": "SOURCE_XML"},
        {"cell_id": "d0", "row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "100", "text_origin": "SOURCE_XML"},
    ]
    return to_contract_table("T1", cells)


def test_table_to_semantics_to_notes_to_packet_stays_consistent(monkeypatch, tmp_path):
    built = table()
    semantics = derive_header_paths(built, header_rows=[0], context={"caption": "表1", "unit_raw": "万元"})
    assert semantics.cell_semantics, "semantics must cover the data cell"

    noted = bind_notes(
        built,
        [
            {
                "note_id": "N1",
                "text": "含外币折人民币。",
                "scope": "CELL",
                "target_ids": ["d0"],
                "evidence_ids": ["EV-NOTE"],
                "evidence_kind": "DOCX_FOOTNOTE",
            }
        ],
    )
    linked = link_cell_note_ids(semantics.cell_semantics, noted.notes)

    monkeypatch.chdir(tmp_path)
    packet = build_review_packet(
        built,
        semantics=linked,
        notes=noted.notes,
        evidence_candidates=EVIDENCE,
        context={"caption": "表1", "unit_raw": "万元"},
    )
    assert "N1" in packet.markdown
    assert packet.payload["rows"][0]["column_header_cell_ids"] == ["h0"]
    # Nothing was written: the packet is returned, not persisted.
    assert os.listdir(tmp_path) == []


def test_correction_flows_into_impact_and_a_new_decision():
    # A reviewer sees the enhancement document, so the table sits under /tables/0.
    document = {"tables": [table()]}
    ops = [
        {"op": "test", "path": "/tables/0/cells/1/text", "value": "100"},
        {"op": "replace", "path": "/tables/0/cells/1/text", "value": "150"},
    ]
    document = copy.deepcopy(document)
    corrected = apply_patch(
        document,
        ops,
        base_generation_id="G1",
        expected_current_generation_id="G1",
        current_generation_id="G1",
        reviewer="local-operator",
        reason="核对原件",
    )
    assert corrected["tables"][0]["cells"][1]["text"] == "150"
    assert document["tables"][0]["cells"][1]["text"] == "100"

    graph = DependencyGraph()
    graph.add_edge(
        parent_id="d0",
        parent_version="v1",
        child_id="out-1",
        child_version="v1",
        edge_type="CELL_TEXT",
        producer="chart",
    )
    assert graph.impact("d0")["affected_object_ids"] == ["out-1"]

    digest = patch_digest(ops)
    decision = record_decision(
        decision_id="D1",
        patch_digest=digest,
        base_generation_id="G1",
        access_revision="R1",
        scope="table:T1",
        reviewer="local-operator",
        outcome="LOCAL_OPERATOR",
    )
    assert is_decision_valid(
        decision, patch_digest=digest, base_generation_id="G1", access_revision="R1", scope="table:T1"
    )


def test_reverse_patch_restores_the_previous_text():
    document = {"cells": [{"text": "150"}]}
    ops = reverse_patch(document, ["/cells/0/text"], previous_values={"/cells/0/text": "100"})
    import jsonpatch

    restored = jsonpatch.JsonPatch(ops).apply(document, in_place=False)
    assert restored["cells"][0]["text"] == "100"


def test_release_lifecycle_never_reaches_active_without_acks():
    report = reconcile(
        scope_id="S1",
        scope_objects=[{"object_id": "O1", "source_version_id": "SV1"}],
        processed={"O1": {"status": "REUSED"}},
    )
    assert report.clean
    release = prepare_release(
        release_id="REL-1",
        generation_id="G1",
        scope="table:T1",
        read_set=["SV1"],
        reconcile_clean=report.clean,
    )
    assert release.status == "LOCAL_READY"

    from app.extensions import ExtensionError

    with pytest.raises(ExtensionError):
        activate(release, required_consumers=["search-index"], acks={})

    active = activate(
        release,
        required_consumers=["search-index"],
        acks={"search-index": {"status": "ACKED", "generation_id": "G1"}},
    )
    assert active.status == "ACTIVE"


def test_outbox_event_for_the_new_generation_is_acked_once():
    outbox = Outbox()
    outbox.enqueue(
        event_id="E1", generation_id="G1", event_type="REBUILD", payload={"o": "O1"}, target="search-index"
    )
    outbox.ack("E1", generation_id="G1", consumer_version="idx-1", ack_token="t1")
    report = outbox.report(connected={"search-index"})
    assert report["acked"] == 1 and report["not_connected"] == 0


def test_candidate_evidence_is_readable_only_with_current_permission():
    entry = register_candidate(
        candidate_evidence_id="EV-CELL",
        source_version_id="SV1",
        locator={
            "kind": "PAGE_REGION",
            "source_sha256": DIGEST,
            "geometry_id": "G1",
            "page_no": 1,
            "level": "PAGE_ONLY",
            "normalized_polygon": None,
        },
        observation_class="SOURCE_CONTENT",
    )
    allowed = AccessGuard(source_version_id="SV1", allowed=lambda: True)
    assert read_candidate(entry, guard=allowed)["candidate_evidence_id"] == "EV-CELL"
    denied = AccessGuard(source_version_id="SV1", allowed=lambda: False)
    with pytest.raises(Exception):
        read_candidate(entry, guard=denied)


def test_source_version_id_is_stable_for_the_legacy_uid_contract():
    payload = {"source_id": "demo_bank_policy", "object_path": "docs/a.docx", "sha256": DIGEST}
    assert source_version_id(payload) == source_version_id(dict(payload))
