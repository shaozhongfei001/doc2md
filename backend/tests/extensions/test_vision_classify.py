"""T601 / ``PI-F05-01``: routing, not summarising.

The two headline rules under test: a framework ``image`` label never proves FLOW,
and overlapping regions get exactly one fact owner so pixels are not counted twice.
"""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.vision.classify import DEFAULT_CONFIDENCE_THRESHOLD, route_regions


def region(region_id: str = "R1", polygon=None, **overrides) -> dict:
    payload = {
        "region_id": region_id,
        "polygon": polygon or [[0, 0], [100, 0], [100, 100], [0, 100]],
        "engine_label": "image",
        # Fixtures state a confidence: since L32 "absent" is a distinct, reviewed
        # case, so leaving it None here would silently make every fixture a review.
        "engine_confidence": 0.9,
        "asset_ref": "assets/r1.png",
        "evidence_id": f"EV-{region_id}",
    }
    payload.update(overrides)
    return payload


def codes(result) -> list[str]:
    return [issue["code"] for issue in result.issues]


def test_framework_table_routes_back_to_the_table_adapter():
    result = route_regions([region(engine_label="table")], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    assert result.visuals[0]["classification"] == "TABLE"
    assert result.routes[0]["route"] == "TABLE_ADAPTER"
    assert result.visuals[0]["classification_state"] == "CANDIDATE"


def test_framework_chart_routes_to_the_chart_adapter():
    result = route_regions([region(engine_label="chart")], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    assert result.routes[0]["route"] == "CHART_ADAPTER"


def test_framework_image_never_proves_flow():
    result = route_regions([region(engine_label="image")], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    visual = result.visuals[0]
    assert visual["classification"] == "UNKNOWN"
    assert visual["classification_state"] == "NEEDS_REVIEW"
    assert "FRAMEWORK_IMAGE_IS_NOT_FLOW" in codes(result)
    assert result.routes[0]["route"] == "NEEDS_HUMAN"


def test_vlm_may_propose_flow_but_only_as_a_candidate():
    result = route_regions(
        [region(engine_label="image")],
        vlm_candidates={"R1": {"classification": "FLOW"}},
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    visual = result.visuals[0]
    assert visual["classification"] == "FLOW"
    assert visual["classification_state"] == "CANDIDATE"
    assert result.routes[0]["route"] == "FLOW_CANDIDATE"


def test_vlm_self_reported_confidence_is_not_written_as_engine_confidence():
    result = route_regions(
        [region(engine_label="image", engine_confidence=None)],
        vlm_candidates={"R1": {"classification": "FLOW", "confidence": 0.99}},
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    assert result.visuals[0]["engine_confidence"] is None
    # L32: the model's 0.99 is not a measured probability, so it cannot lift the
    # region out of review either.
    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"


def test_engine_confidence_is_passed_through():
    result = route_regions([region(engine_label="table", engine_confidence=0.63)], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    assert result.visuals[0]["engine_confidence"] == 0.63


def test_decorative_without_a_reason_needs_review():
    result = route_regions(
        [region()], vlm_candidates={"R1": {"classification": "DECORATIVE"}}, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"
    assert "DECORATIVE_WITHOUT_REASON" in codes(result)


def test_decorative_keeps_the_asset_and_the_reason():
    result = route_regions(
        [region()],
        vlm_candidates={"R1": {"classification": "DECORATIVE"}},
        decorative_reasons={"R1": "页眉装饰线"},
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    route = result.routes[0]
    assert route["route"] == "FILTERED"
    assert route["asset_ref"] == "assets/r1.png"
    assert route["filter_reason"] == "页眉装饰线"


def test_overlapping_regions_get_exactly_one_fact_owner():
    outer = [[0, 0], [200, 0], [200, 200], [0, 200]]
    inner = [[10, 10], [90, 10], [90, 90], [10, 90]]
    result = route_regions([region("BIG", outer), region("SMALL", inner)], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    owners = {route["region_id"]: route["fact_owner"] for route in result.routes}
    assert owners == {"BIG": False, "SMALL": True}
    parent = {route["region_id"]: route["parent_region_id"] for route in result.routes}
    assert parent["SMALL"] == "BIG"
    assert "OVERLAPPING_REGION" in codes(result)


def test_equal_area_overlap_leaves_the_group_without_an_owner():
    left = [[0, 0], [100, 0], [100, 100], [0, 100]]
    right = [[0, 0], [100, 0], [100, 100], [0, 100]]
    result = route_regions([region("A", left), region("B", right)], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    assert all(route["fact_owner"] is False for route in result.routes)
    assert "OVERLAP_OWNER_UNDECIDED" in codes(result)


def test_region_without_any_evidence_stays_unknown():
    result = route_regions([region(engine_label=None)], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    assert result.visuals[0]["classification"] == "UNKNOWN"
    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"


def test_review_decision_confirms_a_candidate():
    result = route_regions([region(engine_label="table")], review_decisions={"R1": "RV-9"}, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    assert result.visuals[0]["classification_state"] == "CONFIRMED"
    assert result.visuals[0]["review_decision_ref"] == "RV-9"


def test_review_decision_cannot_confirm_an_unknown_region():
    result = route_regions([region(engine_label=None)], review_decisions={"R1": "RV-9"}, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
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

    result = route_regions([region(engine_label="chart")], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    contract_models.Visual.model_validate(result.visuals[0])


def test_duplicate_region_ids_are_refused():
    with pytest.raises(ExtensionError):
        route_regions([region("R1"), region("R1")], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)


# ------------------- L32: low and missing confidence are not "confirmed by default"
def test_low_confidence_downgrades_a_fact_class_to_review():
    """qa-l22 B1: 0.05 used to route exactly like 0.99 (CANDIDATE, no issue)."""
    result = route_regions([region(engine_label="table", engine_confidence=0.05)], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)

    assert result.visuals[0]["classification"] == "TABLE"
    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"
    assert "CONFIDENCE_BELOW_THRESHOLD" in codes(result)


def test_missing_confidence_is_not_a_high_confidence():
    """qa-l22 B2: ``None`` was treated the same as a confident label."""
    result = route_regions([region(engine_label="chart", engine_confidence=None)], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)

    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"
    assert "CONFIDENCE_MISSING" in codes(result)


def test_high_confidence_still_reaches_candidate():
    # The gate must not become a blanket downgrade.
    result = route_regions([region(engine_label="table", engine_confidence=0.95)], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)

    assert result.visuals[0]["classification_state"] == "CANDIDATE"
    assert codes(result) == []


def test_the_threshold_is_parameterised_and_marked_uncalibrated():
    # A caller may tune it; the issue detail must not claim the default is calibrated.
    result = route_regions(
        [region(engine_label="table", engine_confidence=0.3)], confidence_threshold=0.2
    )
    assert result.visuals[0]["classification_state"] == "CANDIDATE"

    strict = route_regions(
        [region(engine_label="table", engine_confidence=0.3)], confidence_threshold=0.9
    )
    # Assert the issue exists before indexing: otherwise deleting the whole gate
    # shows up as IndexError (qa-l22 FIND-C), which reads like a crash rather than
    # "the behaviour under test disappeared".
    assert "CONFIDENCE_BELOW_THRESHOLD" in codes(strict)
    detail = [i for i in strict.issues if i["code"] == "CONFIDENCE_BELOW_THRESHOLD"][0]["detail"]
    assert detail["threshold_calibrated"] is False


def test_a_bad_threshold_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        route_regions([region(engine_label="table")], confidence_threshold=0.0)
    assert "confidence_threshold" in str(excinfo.value)


def test_decorative_and_unknown_are_not_subject_to_the_gate():
    # They produce no facts, so a threshold on them would be noise.
    decorative = route_regions(
        [region(engine_label="image", engine_confidence=0.01)],
        decorative_reasons={"R1": "logo"},
        vlm_candidates={"R1": {"classification": "DECORATIVE"}},
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    assert decorative.visuals[0]["classification"] == "DECORATIVE"
    assert "CONFIDENCE_BELOW_THRESHOLD" not in codes(decorative)

    unknown = route_regions([region(engine_confidence=None)], confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD)
    assert unknown.visuals[0]["classification"] == "UNKNOWN"
    assert "CONFIDENCE_MISSING" not in codes(unknown)


def test_the_threshold_must_be_chosen_explicitly():
    """B-lite (qa-l22): an uncalibrated number that callers may omit is a policy
    nobody chose. ``DEFAULT_CONFIDENCE_THRESHOLD`` stays public so accepting it is a
    grep-able act at the call site, not a hidden default.
    """
    with pytest.raises(TypeError):
        route_regions([region(engine_label="table")])


# -------- L34: a human can settle low confidence, but cannot invent a class
def test_a_reviewer_can_confirm_a_low_confidence_region_with_audit():
    """qa-l22 FIND-A: the confidence gate made low confidence *unrecoverable*.

    The gate set NEEDS_REVIEW, and the older "an unresolved classification is not
    confirmed by a ref" branch then swallowed the human decision — so the only ways
    out were lowering the global threshold or re-running the engine.
    """
    result = route_regions(
        [region(engine_label="table", engine_confidence=0.05)],
        review_decisions={"R1": "RV-9"},
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    )

    visual = result.visuals[0]
    assert visual["classification_state"] == "CONFIRMED"
    assert visual["review_decision_ref"] == "RV-9"
    code_list = codes(result)
    assert "CONFIDENCE_OVERRIDDEN" in code_list
    assert "CONFIDENCE_BELOW_THRESHOLD" in code_list, "the original finding must survive"
    assert "REVIEW_REF_IGNORED" not in code_list


def test_a_reviewer_still_cannot_invent_a_class_for_an_unknown_region():
    result = route_regions(
        [region(engine_confidence=0.05)],
        review_decisions={"R1": "RV-9"},
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    )

    visual = result.visuals[0]
    assert visual["classification"] == "UNKNOWN"
    assert visual["classification_state"] == "NEEDS_REVIEW"
    assert visual["review_decision_ref"] is None
    assert "REVIEW_REF_IGNORED" in codes(result)
    assert "CONFIDENCE_OVERRIDDEN" not in codes(result)


def test_a_reviewer_cannot_confirm_a_decorative_region_without_a_reason():
    result = route_regions(
        [region(engine_label="image")],
        vlm_candidates={"R1": {"classification": "DECORATIVE"}},
        review_decisions={"R1": "RV-9"},
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    assert result.visuals[0]["classification_state"] == "NEEDS_REVIEW"
    assert "REVIEW_REF_IGNORED" in codes(result)


# ------------ L36: a blank ref is not a decision (qa-l22 FIND-E)
def test_a_blank_review_ref_is_refused_even_when_it_could_override():
    """L34 made low confidence recoverable; that turned a blank ref into a decision.

    Before L34 the blank ref was harmless (the unresolved branch dropped it). After
    L34 it produced ``CONFIRMED`` with ``review_decision_ref='  '`` written to the
    audit trail — a confirmation nobody made.
    """
    with pytest.raises(ExtensionError) as excinfo:
        route_regions(
            [region(engine_label="table", engine_confidence=0.05)],
            review_decisions={"R1": "  "},
            confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "non-blank" in str(excinfo.value)


def test_a_blank_ref_is_refused_on_the_normal_path_too():
    with pytest.raises(ExtensionError):
        route_regions(
            [region(engine_label="table", engine_confidence=0.95)],
            review_decisions={"R1": "   "},
            confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
        )


def test_the_default_threshold_is_part_of_the_public_surface():
    from app.extensions.vision import classify

    assert "DEFAULT_CONFIDENCE_THRESHOLD" in classify.__all__
