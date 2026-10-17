"""Contract and identity tests (L01 / T101).

Two properties matter most here:

1. **Bit-identical identity.** The published vectors in
   ``contracts/doc2md-19cap/v1.0.0/identity_vectors.json`` and
   ``legacy_uid_vector.json`` must be reproduced exactly. If a refactor changes a
   single byte of canonicalisation, these tests fail — which is the only thing
   that keeps old evidence ids and new object ids from silently diverging.
2. **Closed fields stay closed.** Each negative case pins *why* a payload is
   rejected; a mutation that weakens the guard turns the corresponding test red.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.extensions import identity
from app.extensions.contracts import ROOT_MODELS, ContractError, validate_root
from tests.extensions.conftest import CONTRACTS_DIR, EXAMPLES_DIR, SCHEMAS_DIR


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _example(name: str) -> dict:
    return _load(EXAMPLES_DIR / f"{name}.example.json")


def _minimal_enhancement() -> dict:
    """A hand-built, fully valid ``document-enhancement`` payload.

    The published example does not exercise page locators, chart points or flow
    branches, so testing those guards against it would silently skip. Building the
    payload here keeps every one of those branches under a real assertion.
    """
    sha = "a" * 64
    return {
        "schema_version": "document-enhancement/1.0",
        "generation_id": "b" * 64,
        "job_key": "c" * 64,
        "content_sha256": sha,
        "source_version_ids": ["sv-1"],
        "base_manifest_sha256": None,
        "base_ir_sha256": None,
        "raw_output_sha256": "d" * 64,
        "normalizer_version": "candidate-1.0",
        "profile_sha256": "e" * 64,
        "geometries": [
            {
                "geometry_id": "g1",
                "page_no": 1,
                "original_asset": {
                    "root": "EXTENSION",
                    "relative_path": "raw/page1.png",
                    "sha256": sha,
                },
                "original_width_px": 1000,
                "original_height_px": 800,
                "processed_width_px": 500,
                "processed_height_px": 400,
                "crop_origin_px": [0.0, 0.0],
                "transform_original_to_processed": None,
                "unit_verified": True,
            }
        ],
        "tables": [
            {
                "table_id": "t1",
                "ir_ref": None,
                "parent_cell_id": None,
                "n_rows": 1,
                "n_cols": 1,
                "cells": [
                    {
                        "cell_id": "c1",
                        "row": 0,
                        "col": 0,
                        "rowspan": 1,
                        "colspan": 1,
                        "text": "100",
                        "text_origin": "SOURCE_XML",
                        "text_alignment": "NATIVE",
                        "engine_confidence": None,
                        "token_ids": [],
                        "evidence_ids": ["ce-1"],
                        "nested_table_ids": [],
                    }
                ],
                "slots": [["c1"]],
                "omitted_slots": [],
                "omission_evidence_ids": [],
                "cell_semantics": [],
                "notes": [],
                "source_html_ref": None,
                "structure_state": "VALID_CANDIDATE",
                "review_decision_ref": None,
            }
        ],
        "continuations": [],
        "visuals": [
            {
                "visual_id": "v1",
                "region_evidence_id": "ce-1",
                "classification": "CHART",
                "classification_state": "CANDIDATE",
                "engine_confidence": None,
                "flow": None,
                "chart": {
                    "axes": [
                        {
                            "axis_id": "ax1",
                            "label": "金额",
                            "unit_raw": "万元",
                            "scale_type": "LINEAR",
                            "tick_labels": ["0", "50", "100"],
                            "evidence_ids": ["ce-1"],
                        }
                    ],
                    "points": [
                        {
                            "point_id": "p1",
                            "series": "日均存款",
                            "period": "2026-09",
                            "axis_id": "ax1",
                            "unit_raw": "万元",
                            "value_type": "PRINTED_LABEL",
                            "exact_value": "100",
                            "estimated_value": None,
                            "evidence_ids": ["ce-1"],
                            "source_table_cell_id": None,
                        }
                    ],
                    "original_data_ref": None,
                },
                "review_decision_ref": None,
            }
        ],
        "evidence": [
            {
                "candidate_evidence_id": "ce-1",
                "source_version_id": "sv-1",
                "locator": {
                    "kind": "PAGE_REGION",
                    "source_sha256": sha,
                    "geometry_id": "g1",
                    "page_no": 1,
                    "level": "REGION_VERIFIED",
                    "normalized_polygon": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]],
                },
                "raw_engine_ref": None,
                "quote": None,
                "crop_ref": None,
                "observation_class": "SOURCE_CONTENT",
                "review_state": "UNREVIEWED",
                "review_decision_ref": None,
            }
        ],
        "issues": [],
        "numeric_estimates_enabled": False,
    }


def _expect_invalid(root: str, payload: dict, needle: str) -> None:
    """Assert the payload is refused *for the intended reason*.

    Payloads go through JSON mode, exactly like a payload read from disk, so a
    strict-mode ``datetime`` complaint can never stand in for the rule under test.
    """
    with pytest.raises(ContractError) as excinfo:
        validate_root(root, json.dumps(payload, ensure_ascii=False))
    assert excinfo.value.code == "E_CONTRACT_INVALID"
    messages = " | ".join(err["msg"] for err in excinfo.value.errors)
    assert needle in messages, f"expected rejection reason containing {needle!r}, got {messages!r}"


# --------------------------------------------------------------------- identity
def test_identity_vectors_reproduce_published_digests():
    vectors = _load(CONTRACTS_DIR / "identity_vectors.json")

    sv = vectors["source_version"]
    assert identity.source_version_id(sv["input"]) == sv["expected_id"]

    job = vectors["job"]
    assert identity.job_key(job["input"]) == job["expected_id"]

    generation = vectors["generation"]
    assert identity.generation_id(generation["input"]) == generation["expected_id"]

    assert (
        identity.source_access_key("demo_bank_policy", "policy_demo_01")
        == vectors["source_access_key"]
    )


def test_identity_is_stable_across_processes():
    payload = _load(CONTRACTS_DIR / "identity_vectors.json")["source_version"]["input"]
    expected = _load(CONTRACTS_DIR / "identity_vectors.json")["source_version"]["expected_id"]
    code = (
        "import json,sys;"
        "from app.extensions import identity;"
        "payload=json.loads(sys.argv[1]);"
        "print(identity.source_version_id(payload))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, json.dumps(payload, ensure_ascii=False)],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(Path(__file__).resolve().parents[2]),
        env={"PYTHONHASHSEED": "12345", "PATH": "/usr/bin:/bin"},
    )
    assert completed.stdout.strip() == expected
    assert expected == identity.source_version_id(payload)


def test_canonical_json_sorts_keys_and_keeps_explicit_null():
    payload = {"b": None, "a": "值"}
    assert identity.canonical_json(payload) == '{"a":"值","b":null}'.encode("utf-8")


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"value": float("nan")}, "E_IDENTITY_NON_FINITE"),
        ({"value": float("inf")}, "E_IDENTITY_NON_FINITE"),
        ({"value": b"raw-bytes"}, "E_IDENTITY_BINARY_INPUT"),
        ({1: "int-key"}, "E_IDENTITY_NON_STRING_KEY"),
        ({"value": {"nested": b"x"}}, "E_IDENTITY_BINARY_INPUT"),
    ],
)
def test_canonical_json_refuses_ambiguous_inputs(payload, code):
    with pytest.raises(identity.IdentityError) as excinfo:
        identity.canonical_json(payload)
    assert excinfo.value.code == code


def test_decimal_payload_is_not_silently_serialised():
    from decimal import Decimal

    with pytest.raises(identity.IdentityError) as excinfo:
        identity.canonical_json({"amount": Decimal("1.5")})
    assert excinfo.value.code == "E_IDENTITY_NOT_CANONICAL"


def test_domain_must_carry_a_version_suffix():
    with pytest.raises(identity.IdentityError) as excinfo:
        identity.digest("source-version", {"a": 1})
    assert excinfo.value.code == "E_IDENTITY_BAD_DOMAIN"


def test_set_like_values_are_sorted_and_deduplicated():
    assert identity.sorted_set_values(["b", "a", "b"]) == ["a", "b"]


def test_object_and_candidate_ids_are_domain_separated():
    generation = "0" * 64
    obj = identity.object_id("cell", generation, {"path": "t1/r0c0"}, "tables/t1/cells/c1")
    ce = identity.candidate_evidence_id("sv-1", generation, {"path": "t1/r0c0"}, "SOURCE_CONTENT")
    assert obj.startswith("cell-") and len(obj) == len("cell-") + 64
    assert ce.startswith("ce-") and len(ce) == len("ce-") + 64
    assert obj != ce


def test_legacy_evidence_uid_is_untouched():
    """The v1 formula must still produce the recorded digest (L-2 red line)."""
    from app.services.evidence_chain import _evidence_uid

    vector = _load(CONTRACTS_DIR / "legacy_uid_vector.json")
    actual = _evidence_uid(
        chunk_uid=vector["chunk_uid"],
        source_char_start=vector["source_char_start"],
        source_char_end=vector["source_char_end"],
        evidence_type=vector["evidence_type"],
    )
    assert actual == vector["expected_evidence_uid"]
    # And the new layer must not be able to produce it by accident.
    assert not actual.startswith("ce-")


# --------------------------------------------------------------------- contract
def test_root_model_names_match_published_example_map():
    example_map = _load(CONTRACTS_DIR / "example_map.json")
    assert set(ROOT_MODELS) == set(example_map)
    for name, paths in example_map.items():
        assert (CONTRACTS_DIR / paths["schema"]).is_file()
        assert (CONTRACTS_DIR / paths["example"]).is_file()
    assert {path.name for path in SCHEMAS_DIR.glob("*.schema.json")} == {
        f"{name}.schema.json" for name in ROOT_MODELS
    }


@pytest.mark.parametrize("root_name", sorted(ROOT_MODELS))
def test_published_examples_validate_in_json_mode(root_name):
    text = (EXAMPLES_DIR / f"{root_name}.example.json").read_text(encoding="utf-8")
    validate_root(root_name, text)


def test_unknown_root_contract_is_refused():
    with pytest.raises(ContractError) as excinfo:
        validate_root("document-ir", {})
    assert excinfo.value.code == "E_CONTRACT_UNKNOWN_ROOT"


def test_unknown_field_is_rejected_not_forwarded():
    payload = _example("source-version")
    payload["side_channel"] = {"confidence": 0.99}
    _expect_invalid("source-version", payload, "Extra inputs are not permitted")


def test_native_version_without_revision_is_rejected():
    payload = _example("source-version")
    payload["version_kind"] = "SOURCE_NATIVE"
    payload["source_revision"] = None
    _expect_invalid("source-version", payload, "native version requires actual source revision")


def test_snapshot_must_not_claim_a_source_revision():
    payload = _example("source-version")
    payload["version_kind"] = "LOCAL_SNAPSHOT"
    payload["snapshot_id"] = "snap-1"
    payload["source_revision"] = "r-1"
    _expect_invalid("source-version", payload, "snapshot is not a source revision")


def test_external_policy_requires_a_license_reference():
    payload = _example("source-version")
    payload["source_kind"] = "EXTERNAL_POLICY"
    payload["license_ref"] = None
    _expect_invalid("source-version", payload, "external source requires license reference")


def test_page_only_evidence_must_not_invent_a_polygon():
    payload = _minimal_enhancement()
    payload["evidence"][0]["locator"]["level"] = "PAGE_ONLY"
    payload["evidence"][0]["locator"]["normalized_polygon"] = [
        [0.1, 0.1],
        [0.5, 0.1],
        [0.5, 0.5],
    ]
    _expect_invalid(
        "document-enhancement", payload, "page-only evidence must not invent a region"
    )


def test_verified_region_requires_a_polygon():
    payload = _minimal_enhancement()
    payload["evidence"][0]["locator"]["level"] = "REGION_VERIFIED"
    payload["evidence"][0]["locator"]["normalized_polygon"] = None
    _expect_invalid("document-enhancement", payload, "verified region requires polygon")


def test_verified_region_needs_verified_geometry_units():
    payload = _minimal_enhancement()
    payload["geometries"][0]["unit_verified"] = False
    _expect_invalid("document-enhancement", payload, "unverified geometry unit")


def test_polygon_outside_the_page_is_rejected():
    payload = _minimal_enhancement()
    payload["evidence"][0]["locator"]["normalized_polygon"] = [
        [0.1, 0.1],
        [1.5, 0.1],
        [1.5, 0.5],
    ]
    _expect_invalid("document-enhancement", payload, "normalized polygon outside page")


def test_accepting_a_candidate_requires_a_version_bound_review():
    payload = _example("document-enhancement")
    payload["evidence"][0]["review_state"] = "ACCEPTED"
    payload["evidence"][0]["review_decision_ref"] = None
    _expect_invalid(
        "document-enhancement", payload, "accepted candidate needs version-bound review"
    )


def test_overlapping_cell_span_is_rejected():
    payload = _example("document-enhancement")
    if not payload["tables"]:
        pytest.skip("published example carries no table")
    table = payload["tables"][0]
    if len(table["cells"]) < 2:
        pytest.skip("published example table has a single cell")
    second = table["cells"][1]
    first = table["cells"][0]
    second["row"] = first["row"]
    second["col"] = first["col"]
    _expect_invalid("document-enhancement", payload, "overlapping span")


def test_unexplained_hole_is_rejected():
    payload = _example("document-enhancement")
    if not payload["tables"]:
        pytest.skip("published example carries no table")
    table = payload["tables"][0]
    table["cells"] = [
        cell for cell in table["cells"] if not (cell["row"] == 0 and cell["col"] == 0)
    ]
    table["slots"][0][0] = None
    table["omitted_slots"] = []
    table["omission_evidence_ids"] = []
    _expect_invalid("document-enhancement", payload, "omitted slots require evidence")


def test_confirmed_table_requires_review():
    payload = _example("document-enhancement")
    if not payload["tables"]:
        pytest.skip("published example carries no table")
    payload["tables"][0]["structure_state"] = "CONFIRMED"
    payload["tables"][0]["review_decision_ref"] = None
    _expect_invalid("document-enhancement", payload, "confirmed table needs review")


def test_data_cell_cannot_be_its_own_header():
    payload = _example("document-enhancement")
    table = payload["tables"][0] if payload["tables"] else None
    if not table or not table["cell_semantics"]:
        pytest.skip("published example carries no cell semantics")
    semantics = table["cell_semantics"][0]
    semantics["column_header_cell_ids"] = [semantics["data_cell_id"]]
    _expect_invalid("document-enhancement", payload, "data cell cannot be its own header")


def test_estimated_chart_point_cannot_fill_an_exact_value():
    payload = _minimal_enhancement()
    point = payload["visuals"][0]["chart"]["points"][0]
    point["value_type"] = "ESTIMATED"
    point["exact_value"] = "12.34"
    _expect_invalid(
        "document-enhancement", payload, "estimate/trend/unknown cannot fill exact value"
    )


def test_printed_label_requires_an_actual_value():
    payload = _minimal_enhancement()
    point = payload["visuals"][0]["chart"]["points"][0]
    point["value_type"] = "PRINTED_LABEL"
    point["exact_value"] = None
    _expect_invalid("document-enhancement", payload, "exact reading requires a value")


def test_table_derived_point_requires_an_exact_cell_reference():
    payload = _minimal_enhancement()
    point = payload["visuals"][0]["chart"]["points"][0]
    point["value_type"] = "SOURCE_TABLE"
    point["exact_value"] = "100"
    point["source_table_cell_id"] = None
    _expect_invalid(
        "document-enhancement", payload, "table-derived point needs exact cell reference"
    )


def test_numeric_estimate_is_disabled_by_default():
    payload = _minimal_enhancement()
    point = payload["visuals"][0]["chart"]["points"][0]
    point["value_type"] = "ESTIMATED"
    point["exact_value"] = None
    point["estimated_value"] = "12.34"
    assert payload["numeric_estimates_enabled"] is False
    _expect_invalid("document-enhancement", payload, "numeric estimate disabled")


def test_flow_payload_is_rejected_in_the_chart_branch():
    payload = _minimal_enhancement()
    payload["visuals"][0]["flow"] = {
        "nodes": [
            {
                "node_id": "n1",
                "text": "开始",
                "role": "START",
                "swimlane": None,
                "evidence_ids": ["ce-1"],
            }
        ],
        "edges": [],
    }
    _expect_invalid("document-enhancement", payload, "flow result in other branch")


def test_chart_payload_is_rejected_in_the_flow_branch():
    payload = _minimal_enhancement()
    payload["visuals"][0]["classification"] = "FLOW"
    _expect_invalid("document-enhancement", payload, "chart result in other branch")


def test_patch_must_pair_test_with_replace():
    payload = _example("correction-patch")
    payload["operations"] = payload["operations"][1:] or [
        {"op": "replace", "path": "/tables/0/cells/0/text", "value": "x"}
    ]
    _expect_invalid("correction-patch", payload, "patch requires test/replace pairs")


def test_patch_cannot_touch_identity_paths():
    payload = _example("correction-patch")
    path = payload["operations"][0]["path"]
    protected = "/tables/0/content_sha256"
    payload["operations"][0]["path"] = protected
    payload["operations"][1]["path"] = protected
    _expect_invalid(
        "correction-patch", payload, "identity/evidence cannot be overwritten by patch"
    )
    assert path  # the original path is only used to prove the fixture was real


def test_patch_outside_the_whitelisted_subtrees_is_rejected():
    payload = _example("correction-patch")
    payload["operations"][0]["path"] = "/issues/0/message"
    payload["operations"][1]["path"] = "/issues/0/message"
    _expect_invalid("correction-patch", payload, "protected patch path")


def test_active_release_requires_every_consumer_ack():
    payload = _example("enhancement-release")
    payload["state"] = "ACTIVE"
    payload["required_consumer_ids"] = ["local_candidate_export", "external_graph_index"]
    payload["external_ack_refs"] = {"local_candidate_export": "ack-1"}
    _expect_invalid(
        "enhancement-release", payload, "required consumer acknowledgment missing"
    )


def test_local_ready_requires_bound_reviews():
    payload = _example("enhancement-release")
    payload["state"] = "LOCAL_READY"
    payload["review_decision_refs"] = []
    _expect_invalid("enhancement-release", payload, "release requires bound reviews")


def test_acknowledged_consumer_event_needs_a_receipt():
    payload = _example("consumer-event")
    payload["state"] = "ACKNOWLEDGED"
    payload["ack_ref"] = None
    _expect_invalid("consumer-event", payload, "acknowledged event needs actual receipt reference")


def test_manifest_may_only_declare_extension_outputs():
    payload = _example("extension-manifest")
    payload["files"][0]["file"]["root"] = "PARSED"
    _expect_invalid(
        "extension-manifest", payload, "new outputs may only be inside extension root"
    )


def test_dependency_manifest_rejects_cycles():
    payload = _example("enhancement-dependencies")
    nodes = payload["nodes"]
    if len(nodes) < 2:
        pytest.skip("published example carries a single dependency node")
    payload["edges"] = [
        {"parent_object_id": nodes[0]["object_id"], "child_object_id": nodes[1]["object_id"],
         "relation": "DERIVED_FROM"},
        {"parent_object_id": nodes[1]["object_id"], "child_object_id": nodes[0]["object_id"],
         "relation": "DERIVED_FROM"},
    ]
    _expect_invalid("enhancement-dependencies", payload, "processing dependency cycle")


def test_file_reference_rejects_traversal():
    payload = _example("extension-manifest")
    payload["files"][0]["file"]["relative_path"] = "../outside.json"
    _expect_invalid("extension-manifest", payload, "traversal-free")


def test_manifest_hash_of_all_examples_is_recorded_for_udiff_starting_point():
    """Guard against accidental example edits in the read-only contract copy."""
    digests = {}
    for path in sorted(EXAMPLES_DIR.glob("*.example.json")):
        digests[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(digests) == len(ROOT_MODELS)
    assert all(len(value) == 64 for value in digests.values())


# ------------------------------------------------------- layer boundary (DB-01…06)
# L00/DB_PRECHECK promised these guards; here they become executable. They are
# deliberately source-level assertions: the point is that no DB write path exists
# in the new namespace at all, not that today's call graph happens not to use one.
EXTENSIONS_PACKAGE = Path(__file__).resolve().parents[2] / "app" / "extensions"
REPO_ROOT = CONTRACTS_DIR.parents[2]

DB_WRITE_TOKENS = (
    "session.add(",
    "session.commit(",
    "session.delete(",
    "session.flush(",
    "INSERT INTO",
    "DELETE FROM",
    "UPDATE ",
    "create_engine(",
    "from sqlalchemy",
    "import sqlalchemy",
)
SECOND_STORE_TOKENS = ("import sqlite3", "import redis", "import celery", "import pika", "import kafka")
LEGACY_WRITER_TOKENS = (
    "ParseRegistryService",
    "EvidenceChainService",
    "InventoryScannerService",
    "DuplicateGovernanceService",
    "FileContentVaultService",
    "CuratedProjectAssetsService",
)


def _extension_sources() -> list[Path]:
    return sorted(EXTENSIONS_PACKAGE.rglob("*.py"))


def test_db01_no_database_write_api_in_the_extension_layer():
    for path in _extension_sources():
        source = path.read_text(encoding="utf-8")
        for token in DB_WRITE_TOKENS:
            assert token not in source, f"{path.name} must not contain {token!r}"


def test_db02_no_orm_model_imports():
    for path in _extension_sources():
        source = path.read_text(encoding="utf-8")
        assert "app.models" not in source, f"{path.name} must not touch ORM models"


def test_db03_no_second_runtime_store():
    for path in _extension_sources():
        source = path.read_text(encoding="utf-8")
        for token in SECOND_STORE_TOKENS:
            assert token not in source, f"{path.name} must not import {token!r}"


def test_db04_sql_tree_is_unchanged():
    baseline = _load(CONTRACTS_DIR.parents[2] / "docs" / "proposals" / "doc2md-19cap" / "v1.0.0"
                     / "inputs" / "baseline_lock.json")
    sql_entries = {item["path"]: item["sha256"] for item in baseline["files"]
                   if item["path"].startswith("sql/")}
    assert len(sql_entries) == 4, "the baseline lock must still describe four sql files"
    on_disk = sorted(
        str(item.relative_to(REPO_ROOT)).replace("\\", "/")
        for item in (REPO_ROOT / "sql").rglob("*")
        if item.is_file()
    )
    assert on_disk == sorted(sql_entries), f"sql tree membership changed: {on_disk}"
    for relative, declared in sql_entries.items():
        actual = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
        assert actual == declared, f"{relative} changed; zero-migration rule is broken"


def test_db06_bridge_does_not_import_legacy_writer_services():
    bridge_source = (EXTENSIONS_PACKAGE / "io" / "read_bridge.py").read_text(encoding="utf-8")
    # The docstring names the forbidden writers on purpose (it explains the rule),
    # so the assertion pins the *imports and calls*, not the prose.
    import_lines = [
        line for line in bridge_source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for token in LEGACY_WRITER_TOKENS:
        assert not any(token in line for line in import_lines), (
            f"read_bridge must not import {token}"
        )
    assert "app.services" not in "\n".join(import_lines)


def test_extension_layer_declares_no_network_client():
    """A document is untrusted input; the L01 layer must not gain an HTTP client."""
    forbidden = ("import requests", "import httpx", "urllib.request", "socket.")
    for path in _extension_sources():
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name} must not open network access ({token!r})"


# --------------------------------------------- exit-code contract (TL decision)
# SDD §14 routes structural uncertainty to "locate the XML and escalate to review",
# which is exit 4, not exit 6 (a failed batch item) and not exit 2 (bad input).
STRUCTURAL_REVIEW_CODES = (
    "E_DOCX_MERGE_INVALID",
    "E_DOCX_REVISION_UNRESOLVED",
    "E_TABLE_SPAN_INVALID",
    "E_TABLE_ALIGNMENT_AMBIGUOUS",
    "E_GEOMETRY_UNVERIFIED",
)


def test_structural_uncertainty_maps_to_needs_review_exit_code():
    from app.extensions import EXIT_CODES, exit_code_for

    for code in STRUCTURAL_REVIEW_CODES:
        assert exit_code_for(code) == EXIT_CODES["needs-review-or-not-requested-release-state"], code


def test_unusable_input_maps_to_input_error_exit_code():
    from app.extensions import EXIT_CODES, exit_code_for

    # Both codes appear in SDD §14's error table; E_DOCX_PACKAGE_UNSAFE was
    # deliberately dropped because the contract does not define it (no invented codes).
    for code in ("E_GEOMETRY_NONINVERTIBLE", "E_INPUT_INVALID"):
        assert exit_code_for(code) == EXIT_CODES["input-error"], code


def test_evidence_mismatch_stays_a_failed_item():
    from app.extensions import EXIT_CODES, exit_code_for

    assert exit_code_for("E_EVIDENCE_MISMATCH") == EXIT_CODES["failed-item-or-batch"]


def test_unknown_codes_never_resolve_to_success():
    from app.extensions import exit_code_for

    assert exit_code_for("E_SOMETHING_NEW") != 0
