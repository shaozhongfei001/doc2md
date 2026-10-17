"""Reconciliation and shadow-route tests (L02 / T205).

The denominator rule is the whole test surface here: a failed or pending object must
stay in the denominator, and a missing object may only become a delete candidate when
the enumeration is provably complete.
"""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.sources.reconcile import (
    RECONCILE_CATEGORIES,
    reconcile_inventory,
    shadow_route_decision,
)

SHA = "a" * 64


def _inventory_ten() -> tuple[list[dict], dict[str, dict]]:
    """3 reused + 4 reprocessed + 1 excluded + 1 failed + 1 pending = 10."""
    inventory: list[dict] = []
    ledger: dict[str, dict] = {}

    for index in range(3):
        object_id = f"reuse_{index}"
        inventory.append(
            {
                "external_object_id": object_id,
                "processing_route": "REUSE_QUERY",
                "legacy_query_ref": f"query-{index}",
                "content_sha256": SHA,
            }
        )
        ledger[object_id] = {
            "processing_route": "REUSE_QUERY",
            "legacy_query_ref": f"query-{index}",
            "source_version_id": f"sv-{index}",
        }

    for index in range(4):
        object_id = f"reproc_{index}"
        inventory.append(
            {"external_object_id": object_id, "processing_route": "REPROCESS", "content_sha256": SHA}
        )
        ledger[object_id] = {
            "processing_route": "REPROCESS",
            "generation_ids": [f"gen-{index}"],
            "source_version_id": f"sv-r{index}",
        }

    inventory.append(
        {
            "external_object_id": "excluded_1",
            "processing_route": "METADATA_ONLY",
            "excluded_reason": "licence missing",
            "content_sha256": SHA,
        }
    )
    ledger["excluded_1"] = {"processing_route": "METADATA_ONLY"}

    inventory.append(
        {
            "external_object_id": "failed_1",
            "processing_route": "REPROCESS",
            "failed_reason": "engine timeout",
            "content_sha256": SHA,
        }
    )
    ledger["failed_1"] = {"processing_route": "REPROCESS"}

    inventory.append(
        {"external_object_id": "pending_1", "processing_route": "REPROCESS", "content_sha256": SHA}
    )
    ledger["pending_1"] = {"processing_route": "REPROCESS"}

    return inventory, ledger


def test_denominator_keeps_failures_and_pending_objects():
    inventory, ledger = _inventory_ten()
    report = reconcile_inventory(
        source_id="demo",
        inventory=inventory,
        ledger=ledger,
        enumeration_complete=True,
    )

    assert report.denominator == 10
    assert report.counts == {
        "REUSED": 3,
        "REPROCESSED": 4,
        "EXCLUDED_WITH_REASON": 1,
        "FAILED": 1,
        "PENDING": 1,
    }
    assert report.sums_to_denominator is True
    assert set(report.counts) == set(RECONCILE_CATEGORIES)
    assert report.state == "RECONCILED"
    assert report.delete_candidates == ()


def test_a_failed_object_is_never_dropped_from_the_denominator():
    inventory, ledger = _inventory_ten()
    report = reconcile_inventory(
        source_id="demo", inventory=inventory, ledger=ledger, enumeration_complete=True
    )
    failed = [item for item in report.items if item.category == "FAILED"]
    assert len(failed) == 1
    assert failed[0].external_object_id == "failed_1"
    assert failed[0].reason == "engine timeout"


def test_incomplete_enumeration_produces_no_delete_candidates():
    inventory, ledger = _inventory_ten()
    ledger["object_that_disappeared"] = {"processing_route": "REPROCESS"}
    report = reconcile_inventory(
        source_id="demo",
        inventory=inventory,
        ledger=ledger,
        enumeration_complete=False,
    )
    assert report.delete_candidates == ()
    assert "ENUMERATION_INCOMPLETE" in report.blockers
    assert report.state == "INCOMPLETE"
    assert report.ready_for_release is False


def test_complete_enumeration_marks_a_missing_object_as_delete_candidate():
    inventory, ledger = _inventory_ten()
    ledger["object_that_disappeared"] = {"processing_route": "REPROCESS"}
    report = reconcile_inventory(
        source_id="demo", inventory=inventory, ledger=ledger, enumeration_complete=True
    )
    assert report.delete_candidates == ("object_that_disappeared",)
    assert report.denominator == 10


def test_not_connected_source_reports_nothing_reconciled():
    inventory, ledger = _inventory_ten()
    report = reconcile_inventory(
        source_id="tianquan_bank",
        inventory=inventory,
        ledger=ledger,
        enumeration_complete=False,
        connected=False,
    )
    assert report.state == "NOT_CONNECTED"
    assert report.delete_candidates == ()
    assert report.blockers == ("SOURCE_NOT_CONNECTED",)
    assert report.ready_for_release is False
    assert "no interface" in report.detail["note"]


def test_inventory_shorter_than_expected_is_incomplete():
    inventory, ledger = _inventory_ten()
    report = reconcile_inventory(
        source_id="demo",
        inventory=inventory[:8],
        ledger=ledger,
        enumeration_complete=True,
        expected_total=10,
    )
    assert report.state == "INCOMPLETE"
    assert "INVENTORY_MISMATCH" in report.blockers
    assert report.detail == {"expected_total": 10, "received": 8}


def test_duplicate_inventory_entry_is_refused():
    inventory, ledger = _inventory_ten()
    inventory.append(dict(inventory[0]))
    with pytest.raises(ExtensionError) as excinfo:
        reconcile_inventory(
            source_id="demo", inventory=inventory, ledger=ledger, enumeration_complete=True
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "twice" in excinfo.value.message


def test_inventory_entry_without_object_id_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        reconcile_inventory(
            source_id="demo",
            inventory=[{"processing_route": "REPROCESS"}],
            ledger={},
            enumeration_complete=True,
        )
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_reuse_route_without_a_query_reference_is_excluded_with_reason():
    inventory = [
        {"external_object_id": "o1", "processing_route": "REUSE_QUERY", "content_sha256": SHA}
    ]
    report = reconcile_inventory(
        source_id="demo",
        inventory=inventory,
        ledger={"o1": {"processing_route": "REUSE_QUERY"}},
        enumeration_complete=True,
    )
    assert report.counts["EXCLUDED_WITH_REASON"] == 1
    assert "authorised query reference" in report.items[0].reason


def test_unprocessed_object_is_pending_not_excluded():
    inventory = [{"external_object_id": "o1", "content_sha256": SHA}]
    report = reconcile_inventory(
        source_id="demo", inventory=inventory, ledger={}, enumeration_complete=True
    )
    assert report.counts["PENDING"] == 1
    assert report.items[0].reason == "not processed yet"


# ------------------------------------------------------------- shadow routing
def test_shadow_route_is_blocked_without_consumer_acknowledgements():
    decision = shadow_route_decision(
        scope="knowledge_index",
        expected_previous="release-0",
        target_release="release-1",
        local_ready=True,
        required_consumers=["graph_index", "wiki"],
        acknowledged_consumers=["graph_index"],
    )
    assert decision["release_state"] == "BLOCKED"
    assert decision["missing_consumer_acks"] == ["wiki"]
    assert "BANK_CUTOVER_NOT_AUTHORISED" in decision["blockers"]
    assert decision["local_state"] == "LOCAL_READY"


def test_shadow_route_never_claims_a_real_cutover_even_when_local_ready():
    decision = shadow_route_decision(
        scope="knowledge_index",
        expected_previous=None,
        target_release="release-1",
        local_ready=True,
        required_consumers=["graph_index"],
        acknowledged_consumers=["graph_index"],
    )
    assert "BANK_CUTOVER_NOT_AUTHORISED" in decision["blockers"]
    assert "OI08" in decision["note"]


def test_shadow_route_reports_local_not_ready():
    decision = shadow_route_decision(
        scope="knowledge_index",
        expected_previous=None,
        target_release="release-1",
        local_ready=False,
        required_consumers=[],
        acknowledged_consumers=[],
    )
    assert decision["local_state"] == "PREPARED"
    assert "LOCAL_NOT_READY" in decision["blockers"]
