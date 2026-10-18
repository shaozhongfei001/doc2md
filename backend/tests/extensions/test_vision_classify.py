"""T601 / ``PI-F05-01``: routing, not summarising.

The two headline rules under test: a framework ``image`` label never proves FLOW,
and overlapping regions get exactly one fact owner so pixels are not counted twice.
"""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.vision.classify import route_regions


def region(region_id: str = "R1", polygon=None, **overrides) -> dict:
    payload = {
        "region_id": region_id,
        "polygon": polygon or [[0, 0], [100, 0], [100, 100], [0, 100]],
        "engine_label": "image",
        "engine_confidence": None,
        "asset_ref": "assets/r1.png",
        "evidence_id": f"EV-{region_id}",
    }
    payload.update(overrides)
    return payload


def codes(result) -> list[str]:
    return [issue["code"] for issue in result.issues]


def test_framework_table_routes_back_to_the_table_adapter():
    result = route_regions([region(engine_label="table")])
    assert result.visuals[0]["classification"] == "TABLE"
    assert result.routes[0]["route"] == "TABLE_ADAPTER"
    assert result.visuals[0]["classification_state"] == "CANDIDATE"


def test_framework_chart_routes_to_the_chart_adapter():
    result = route_regions([region(engine_label="chart")])
    assert result.routes[0]["route"] == "CHART_ADAPTER"


def test_framework_image_never_proves_flow():
    result = route_regions([region(engine_label="image")])
    visual = result.visuals[0]
    assert visual["classification"] == "UNKNOWN"
    assert visual["classification_state"] == "NEEDS_REVIEW"
    assert "FRAMEWORK_IMAGE_IS_NOT_FLOW" in codes(result)
    assert result.routes[0]["route"] == "NEEDS_HUMAN"


def test_vlm_may_propose_flow_but_only_as_a_candidate():
    result = route_regions(
        [region(engine_label="image")],
        vlm_candidates={"R1": {"classification": "FLOW"}},
    )
    visual = result.visuals[0]
    assert visual["classification"] == "FLOW"
    assert visual["classification_state"] == "CANDIDATE"
    assert result.routes[0]["route"] == "FLOW_CANDIDATE"


def test_vlm_self_reported_confidence_is_not_written_as_engine_confidence():
    result = route_regions(
        [region(engine_label="image")],
        vlm_candidates={"R1": {"classification": "FLOW", "confidence": 0.99}},
    )
    assert result.visuals[0]["engine_confidence"] is None


def test_engine_confidence_is_passed_through():
    result = route_regions([region(engine_label="table", engine_confidence=0.63)])
    assert result.visuals[0]["engine_confidence"] == 0.63


def test_decorative_without_a_reason_needs_review():
    result = route_regions(
        [region()], vlm_candidates={"R1": {"classification": "DECORATIVE"}}
    )
    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"
    assert "DECORATIVE_WITHOUT_REASON" in codes(result)


def test_decorative_keeps_the_asset_and_the_reason():
    result = route_regions(
        [region()],
        vlm_candidates={"R1": {"classification": "DECORATIVE"}},
        decorative_reasons={"R1": "页眉装饰线"},
    )
    route = result.routes[0]
    assert route["route"] == "FILTERED"
    assert route["asset_ref"] == "assets/r1.png"
    assert route["filter_reason"] == "页眉装饰线"


def test_overlapping_regions_get_exactly_one_fact_owner():
    outer = [[0, 0], [200, 0], [200, 200], [0, 200]]
    inner = [[10, 10], [90, 10], [90, 90], [10, 90]]
    result = route_regions([region("BIG", outer), region("SMALL", inner)])
    owners = {route["region_id"]: route["fact_owner"] for route in result.routes}
    assert owners == {"BIG": False, "SMALL": True}
    parent = {route["region_id"]: route["parent_region_id"] for route in result.routes}
    assert parent["SMALL"] == "BIG"
    assert "OVERLAPPING_REGION" in codes(result)


def test_equal_area_overlap_leaves_the_group_without_an_owner():
    left = [[0, 0], [100, 0], [100, 100], [0, 100]]
    right = [[0, 0], [100, 0], [100, 100], [0, 100]]
    result = route_regions([region("A", left), region("B", right)])
    assert all(route["fact_owner"] is False for route in result.routes)
    assert "OVERLAP_OWNER_UNDECIDED" in codes(result)


def test_region_without_any_evidence_stays_unknown():
    result = route_regions([region(engine_label=None)])
    assert result.visuals[0]["classification"] == "UNKNOWN"
    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"


def test_review_decision_confirms_a_candidate():
    result = route_regions([region(engine_label="table")], review_decisions={"R1": "RV-9"})
    assert result.visuals[0]["classification_state"] == "CONFIRMED"
    assert result.visuals[0]["review_decision_ref"] == "RV-9"


def test_review_decision_cannot_confirm_an_unknown_region():
    result = route_regions([region(engine_label=None)], review_decisions={"R1": "RV-9"})
    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"
    assert result.visuals[0]["review_decision_ref"] is None
    assert "REVIEW_REF_IGNORED" in codes(result)


def test_visual_payload_matches_the_published_contract():
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    contract_dir = repo / "contracts" / "doc2md-19cap" / "v1.0.0"
    if str(contract_dir) not in sys.path:
        sys.path.insert(0, str(contract_dir))
    import contract_models

    result = route_regions([region(engine_label="chart")])
    contract_models.Visual.model_validate(result.visuals[0])


def test_duplicate_region_ids_are_refused():
    with pytest.raises(ExtensionError):
        route_regions([region("R1"), region("R1")])
