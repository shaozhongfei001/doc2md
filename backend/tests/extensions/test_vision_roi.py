"""T605 / ``PI-F05-05``: one extra pass, verified crop only, both candidates kept."""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.vision.roi import RoiBudget, merge_rerun, plan_roi

REGION = {
    "evidence_class": "REGION_VERIFIED",
    "polygon": [[100.0, 100.0], [200.0, 100.0], [200.0, 160.0], [100.0, 160.0]],
    "page_number": 1,
    "source_sha256": "a" * 64,
}


def test_verified_region_produces_a_bounded_crop_with_a_hash():
    plan = plan_roi(REGION, object_key="V1", budget=RoiBudget(), margin=5.0,
                    page_size=(1000, 800))
    assert plan.mode == "ROI"
    assert plan.polygon == [[95.0, 95.0], [205.0, 95.0], [205.0, 165.0], [95.0, 165.0]]
    assert plan.crop_ref.startswith("crop:")
    assert len(plan.crop_ref) == len("crop:") + 16


def test_crop_is_clipped_to_the_page():
    region = dict(REGION, polygon=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
    plan = plan_roi(region, object_key="V1", budget=RoiBudget(), margin=20.0, page_size=(100, 100))
    assert plan.polygon[0] == [0.0, 0.0]
    assert plan.polygon[2] == [30.0, 30.0]


def test_page_only_region_falls_back_instead_of_guessing_a_crop():
    region = {"evidence_class": "PAGE_ONLY", "polygon": None, "page_number": 1}
    plan = plan_roi(region, object_key="V1", budget=RoiBudget())
    assert plan.mode == "PAGE_FALLBACK"
    assert plan.polygon is None and plan.crop_ref is None
    assert "would be a guess" in plan.reason


def test_second_pass_for_the_same_object_is_refused():
    budget = RoiBudget()
    plan_roi(REGION, object_key="V1", budget=budget)
    with pytest.raises(ExtensionError) as excinfo:
        plan_roi(REGION, object_key="V1", budget=budget)
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "one extra ROI pass" in str(excinfo.value)


def test_transport_retries_are_counted_separately_from_passes():
    budget = RoiBudget(max_retries=1)
    budget.begin_object("V1")
    assert budget.retry() == 1
    with pytest.raises(ExtensionError) as excinfo:
        budget.retry()
    assert excinfo.value.code == "E_ENGINE_TIMEOUT"
    assert excinfo.value.detail["limit"] == 1


def test_object_budget_is_enforced():
    budget = RoiBudget(max_objects=1)
    budget.begin_object("V1")
    with pytest.raises(ExtensionError) as excinfo:
        budget.begin_object("V2")
    assert "object budget exhausted" in str(excinfo.value)


def test_pass_budget_cannot_be_raised_by_configuration():
    with pytest.raises(ValueError):
        RoiBudget(max_passes_per_object=2)


def test_crop_matrix_is_carried_when_supplied_and_validated():
    matrix = [[1.0, 0.0, -95.0], [0.0, 1.0, -95.0], [0.0, 0.0, 1.0]]
    plan = plan_roi(REGION, object_key="V1", budget=RoiBudget(), crop_matrix=matrix)
    assert plan.crop_matrix == matrix
    with pytest.raises(ExtensionError):
        plan_roi(REGION, object_key="V2", budget=RoiBudget(), crop_matrix=[[1.0, 0.0]])


def test_margin_must_be_non_negative():
    with pytest.raises(ExtensionError):
        plan_roi(REGION, object_key="V1", budget=RoiBudget(), margin=-1)


# ------------------------------------------------------------------- merging
def test_agreement_is_still_only_a_candidate():
    merged = merge_rerun({"text": "100"}, {"text": "100"}, object_key="V1", independent=True)
    assert merged["verdict"] == "CONSISTENT"
    assert merged["state"] == "CANDIDATE"
    assert merged["resolved"] is None


def test_disagreement_keeps_both_and_needs_review():
    merged = merge_rerun(
        {"text": "100", "score": 0.99},
        {"text": "120", "score": 0.51},
        object_key="V1",
        independent=True,
    )
    assert merged["verdict"] == "CONFLICT"
    assert merged["state"] == "NEEDS_REVIEW"
    assert merged["original"]["text"] == "100"
    assert merged["rerun"]["text"] == "120"
    # The higher self-reported score did not win anything.
    assert merged["resolved"] is None


def test_same_model_different_prompt_is_not_independent():
    merged = merge_rerun({"text": "100"}, {"text": "100"}, object_key="V1", independent=False)
    assert merged["independent"] is False
    assert merged["state"] == "NEEDS_REVIEW"
    assert "not independent" in merged["reason"]


def test_missing_payload_on_one_side_is_unknown_not_agreement():
    merged = merge_rerun({"text": "100"}, {"value": None}, object_key="V1", independent=True)
    assert merged["verdict"] == "UNKNOWN"
