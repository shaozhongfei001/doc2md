"""T603 / ``PI-F05-03``: structure blocks, control flow reports, text disagrees visibly."""

from __future__ import annotations

import copy

import pytest

from app.extensions import ExtensionError
from app.extensions.vision.consistency import check_flow


def node(node_id: str, role: str = "ACTION", text: str | None = None, swimlane=None) -> dict:
    return {
        "node_id": node_id,
        "text": text if text is not None else node_id,
        "role": role,
        "swimlane": swimlane,
    }


def edge(edge_id: str, source, target, label=None, state: str = "CANDIDATE") -> dict:
    return {
        "edge_id": edge_id,
        "source_node_id": source,
        "target_node_id": target,
        "label": label,
        "direction_state": state,
    }


def graph(nodes=None, edges=None) -> dict:
    return {
        "nodes": nodes if nodes is not None else [node("N1", "START"), node("N2", "END")],
        "edges": edges if edges is not None else [edge("E1", "N1", "N2")],
    }


def codes(report, bucket="soft_findings") -> list[str]:
    return [item["code"] for item in getattr(report, bucket)]


# --------------------------------------------------------------- hard errors
def test_duplicate_node_id_is_a_hard_error():
    report = check_flow(graph(nodes=[node("N1"), node("N1")]))
    assert "DUPLICATE_NODE_ID" in codes(report, "hard_errors")
    assert report.confirmed_ok is False


def test_edge_to_a_missing_node_is_a_hard_error():
    report = check_flow(graph(edges=[edge("E1", "N1", "N404")]))
    assert "MISSING_ENDPOINT" in codes(report, "hard_errors")


def test_invalid_role_is_a_hard_error():
    report = check_flow(graph(nodes=[{"node_id": "N1", "role": "BRANCH", "text": "x"}]))
    assert "INVALID_ROLE" in codes(report, "hard_errors")


def test_clean_graph_has_no_hard_errors():
    report = check_flow(graph())
    assert report.hard_errors == []
    assert report.confirmed_ok is True


# -------------------------------------------------------------- soft findings
def test_isolated_node_is_reported_and_survives():
    report = check_flow(graph(nodes=[node("N1"), node("N2"), node("N3")]))
    assert "ISOLATED_NODE" in codes(report)
    # Nothing was deleted: the caller still owns three nodes.
    assert len(graph(nodes=[node("N1"), node("N2"), node("N3")])["nodes"]) == 3


def test_decision_without_exit_is_reported():
    report = check_flow(graph(nodes=[node("N1", "DECISION"), node("N2")], edges=[edge("E1", "N2", "N1")]))
    assert "DECISION_WITHOUT_EXIT" in codes(report)


def test_decision_with_one_unlabelled_exit_is_flagged():
    report = check_flow(
        graph(
            nodes=[node("D", "DECISION"), node("A"), node("B")],
            edges=[edge("E1", "D", "A"), edge("E2", "D", "B")],
        )
    )
    assert "BRANCH_LABEL_INCOMPLETE" in codes(report)


def test_labelled_yes_no_branches_are_not_flagged():
    report = check_flow(
        graph(
            nodes=[node("D", "DECISION"), node("A"), node("B")],
            edges=[edge("E1", "D", "A", label="是"), edge("E2", "D", "B", label="否")],
        )
    )
    assert "BRANCH_LABEL_INCOMPLETE" not in codes(report)


def test_missing_start_and_end_are_reported():
    report = check_flow(graph(nodes=[node("N1"), node("N2")]))
    assert "NO_START" in codes(report)
    assert "NO_END" in codes(report)


def test_cycle_is_reported_and_edges_are_not_removed():
    original = graph(
        nodes=[node("N1", "START"), node("N2"), node("N3", "END")],
        edges=[edge("E1", "N1", "N2"), edge("E2", "N2", "N3"), edge("E3", "N3", "N2")],
    )
    snapshot = copy.deepcopy(original)
    report = check_flow(original)
    assert "CYCLE_PRESENT" in codes(report)
    assert original == snapshot


def test_swimlane_change_is_reported():
    report = check_flow(
        graph(nodes=[node("N1", "START", swimlane="柜面"), node("N2", "END", swimlane="风控")])
    )
    assert "SWIMLANE_CHANGE" in codes(report)


# ------------------------------------------------------------- text vs image
def test_matching_text_is_consistent():
    report = check_flow(
        graph(nodes=[node("N1", "START", text="受理"), node("N2", "END")]),
        text_context=[{"claim_id": "C1", "target_id": "N1", "passage": "客户提交申请后受理。"}],
        chapter_ref="3.2",
    )
    verdict = report.text_image_verdicts[0]
    assert verdict["verdict"] == "CONSISTENT"
    assert verdict["chapter_ref"] == "3.2"


def test_negation_mismatch_is_a_conflict_keeping_both_sides():
    report = check_flow(
        graph(nodes=[node("N1", "START", text="复核")], edges=[edge("E1", "N1", "N1")]),
        text_context=[{"claim_id": "C1", "target_id": "N1", "passage": "该环节不得复核。"}],
    )
    verdict = report.text_image_verdicts[0]
    assert verdict["verdict"] == "CONFLICT"
    assert verdict["reason"] == "NEGATION_MISMATCH"
    assert verdict["node_text"] == "复核"
    assert verdict["passage"] == "该环节不得复核。"


def test_text_that_does_not_mention_the_node_is_unknown():
    report = check_flow(
        graph(),
        text_context=[{"claim_id": "C1", "target_id": "N1", "passage": "本章描述授信额度。"}],
    )
    assert report.text_image_verdicts[0]["verdict"] == "UNKNOWN"
    assert report.text_image_verdicts[0]["reason"] == "TEXT_DOES_NOT_MENTION_NODE"


def test_unknown_target_is_unknown_not_consistent():
    report = check_flow(
        graph(), text_context=[{"claim_id": "C1", "target_id": "N404", "passage": "复核"}]
    )
    assert report.text_image_verdicts[0]["verdict"] == "UNKNOWN"
    assert report.text_image_verdicts[0]["reason"] == "TARGET_NOT_FOUND"


def test_graph_is_never_mutated_by_the_check():
    original = graph(
        nodes=[node("N1", "DECISION"), node("N2"), node("N3")],
        edges=[edge("E1", "N1", "N2"), edge("E2", "N1", "N3"), edge("E3", "N3", "N1")],
    )
    snapshot = copy.deepcopy(original)
    check_flow(
        original,
        text_context=[{"claim_id": "C1", "target_id": "N2", "passage": "N2"}],
    )
    assert original == snapshot


def test_malformed_text_context_is_refused():
    with pytest.raises(ExtensionError):
        check_flow(graph(), text_context=[{"claim_id": "C1", "target_id": "N1", "passage": "  "}])
