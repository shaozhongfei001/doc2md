"""T702 / ``PI-F04-09``: test-before-replace, field whitelist, CAS, no mutation."""

from __future__ import annotations

import copy

import pytest

from app.extensions import ExtensionError
from app.extensions.review.patches import (
    apply_patch,
    patch_digest,
    preview_patch,
)


def document() -> dict:
    return {
        "tables": [
            {
                "table_id": "T1",
                "cells": [{"cell_id": "c1", "text": "100"}, {"cell_id": "c2", "text": "200"}],
                "cell_semantics": [{"data_cell_id": "c1", "unit_raw": None}],
                "notes": [{"note_id": "N1", "target_ids": ["c1"], "scope": "CELL"}],
            }
        ],
        "continuations": [{"relation_id": "C1", "decision": "JOIN_CANDIDATE"}],
        "visuals": [],
        "source_sha256": "a" * 64,
        "schema_version": "document-enhancement/1.0",
    }


def patch(path: str = "/tables/0/cells/0/text", value: str = "150", old: str = "100") -> list[dict]:
    return [
        {"op": "test", "path": path, "value": old},
        {"op": "replace", "path": path, "value": value},
    ]


def preview(doc=None, ops=None, **overrides):
    kwargs = {
        "base_generation_id": "G1",
        "expected_current_generation_id": "G1",
        "current_generation_id": "G1",
        "reviewer": "local-operator",
        "reason": "核对原文后修正金额",
        "evidence_ids": ["EV-1"],
    }
    kwargs.update(overrides)
    return preview_patch(doc if doc is not None else document(), ops if ops is not None else patch(), **kwargs)


# ------------------------------------------------------------------ happy path
def test_preview_reports_the_change_without_touching_the_input():
    doc = document()
    snapshot = copy.deepcopy(doc)
    result = preview(doc)
    assert result.accepted is True
    assert result.changed_paths == ["/tables/0/cells/0/text"]
    assert result.document["tables"][0]["cells"][0]["text"] == "150"
    assert doc == snapshot  # input untouched


def test_apply_returns_a_new_document():
    doc = document()
    applied = apply_patch(
        doc,
        patch(),
        base_generation_id="G1",
        expected_current_generation_id="G1",
        current_generation_id="G1",
        reviewer="local-operator",
        reason="修正",
    )
    assert applied["tables"][0]["cells"][0]["text"] == "150"
    assert doc["tables"][0]["cells"][0]["text"] == "100"


def test_audit_record_carries_reviewer_reason_and_evidence():
    audit = preview().audit
    assert audit["reviewer"] == "local-operator"
    assert audit["reason"] == "核对原文后修正金额"
    assert audit["evidence_ids"] == ["EV-1"]


# --------------------------------------------------------------- patch safety
def test_replace_without_a_preceding_test_is_refused():
    ops = [{"op": "replace", "path": "/tables/0/cells/0/text", "value": "150"}]
    with pytest.raises(ExtensionError) as excinfo:
        preview(ops=ops)
    assert "immediately preceded by a test" in str(excinfo.value)


def test_only_test_and_replace_are_allowed():
    ops = [{"op": "add", "path": "/tables/0/cells/0/text", "value": "150"}]
    with pytest.raises(ExtensionError) as excinfo:
        preview(ops=ops)
    assert "not allowed" in str(excinfo.value)


def test_path_outside_the_whitelist_is_refused():
    ops = patch(path="/tables/0/table_id", old="T1", value="T2")
    with pytest.raises(ExtensionError) as excinfo:
        preview(ops=ops)
    assert "outside the reviewer field whitelist" in str(excinfo.value)


@pytest.mark.parametrize(
    "path",
    ["/source_sha256", "/schema_version", "/tables/0/cells/0/locator", "/evidence/0/candidate_evidence_id"],
)
def test_source_identity_and_provenance_are_never_patchable(path):
    ops = [{"op": "test", "path": path, "value": "x"}, {"op": "replace", "path": path, "value": "y"}]
    with pytest.raises(ExtensionError) as excinfo:
        preview(ops=ops)
    assert "never patchable" in str(excinfo.value) or "outside the reviewer field whitelist" in str(excinfo.value)


def test_a_failing_test_means_the_document_moved():
    ops = patch(old="999")
    with pytest.raises(ExtensionError) as excinfo:
        preview(ops=ops)
    assert "could not be applied" in str(excinfo.value)


def test_missing_reason_is_refused():
    with pytest.raises(ExtensionError):
        preview(reason="  ")


def test_reviewer_cannot_be_empty():
    with pytest.raises(ExtensionError) as excinfo:
        preview(reviewer="   ")
    assert "reviewer must come from" in str(excinfo.value)


# ---------------------------------------------------------------------- CAS
def test_generation_mismatch_is_a_version_conflict():
    with pytest.raises(ExtensionError) as excinfo:
        preview(expected_current_generation_id="G-old", current_generation_id="G-new")
    assert excinfo.value.code == "E_VERSION_CONFLICT"
    assert excinfo.value.detail["expected"] == "G-old"


def test_validator_failure_aborts_the_patch():
    def reject(_doc: dict) -> None:
        raise ValueError("grid re-validation failed")

    with pytest.raises(ValueError):
        preview(validators=[reject])


# ------------------------------------------------------------------- digest
def test_digest_is_stable_and_content_bound():
    first = patch_digest(patch())
    assert first == patch_digest(patch())
    assert first != patch_digest(patch(value="151"))
    assert len(first) == 64


def test_digest_is_carried_into_the_preview():
    result = preview()
    assert result.digest == patch_digest(patch())
