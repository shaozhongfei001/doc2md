"""T602 / ``PI-F05-02``: nodes from OCR, edges only when the model said so."""

from __future__ import annotations

import pytest

from app.extensions import ExtensionError
from app.extensions.vision.flow import assemble_flow, find_cycles, nodes_from_ocr


def line(text: str, **extra) -> dict:
    payload = {"text": text}
    payload.update(extra)
    return payload


def node(node_id: str, role: str = "ACTION", text: str | None = None) -> dict:
    return {"node_id": node_id, "text": text or node_id, "role": role, "swimlane": None}


def edge(edge_id: str, source, target, direction: str = "DIRECTED", **extra) -> dict:
    payload = {
        "edge_id": edge_id,
        "source_node_id": source,
        "target_node_id": target,
        "label": None,
        "direction": direction,
        "evidence_ids": ["EV-1"],
    }
    payload.update(extra)
    return payload


def codes(result) -> list[str]:
    return [issue["code"] for issue in result.issues]


# ------------------------------------------------------------------ OCR nodes
def test_repeated_text_produces_distinct_nodes():
    nodes = nodes_from_ocr([line("审核"), line("审核"), line("审核")], visual_id="V1")
    assert [n["node_id"] for n in nodes] == ["V1-N000", "V1-N001", "V1-N002"]
    assert len({n["node_id"] for n in nodes}) == 3


def test_swimlane_can_come_from_a_region_map():
    nodes = nodes_from_ocr(
        [line("受理"), line("复核")], visual_id="V1", swimlane_map={0: "柜面", 1: "风控"}
    )
    assert [n["swimlane"] for n in nodes] == ["柜面", "风控"]


def test_blank_ocr_line_is_refused():
    with pytest.raises(ExtensionError):
        nodes_from_ocr([line("  ")], visual_id="V1")


# --------------------------------------------------------------- assembling
def test_directed_edge_with_known_endpoints_is_a_candidate():
    result = assemble_flow(nodes=[node("N1"), node("N2")], edges=[edge("E1", "N1", "N2")])
    assert result.graph["edges"][0]["direction_state"] == "CANDIDATE"
    assert result.graph["edges"][0]["review_decision_ref"] is None


def test_review_decision_promotes_to_confirmed():
    result = assemble_flow(
        nodes=[node("N1"), node("N2")],
        edges=[edge("E1", "N1", "N2")],
        review_decisions={"E1": "RV-1"},
    )
    assert result.graph["edges"][0]["direction_state"] == "CONFIRMED"
    assert result.graph["edges"][0]["review_decision_ref"] == "RV-1"


def test_bidirectional_edge_stays_unresolved():
    result = assemble_flow(
        nodes=[node("N1"), node("N2")],
        edges=[edge("E1", "N1", "N2", direction="BIDIRECTIONAL")],
    )
    assert result.graph["edges"][0]["direction_state"] == "UNRESOLVED"
    assert "BIDIRECTIONAL_EDGE" in codes(result)


def test_missing_endpoint_stays_unresolved_and_keeps_null():
    result = assemble_flow(
        nodes=[node("N1")], edges=[edge("E1", "N1", None, direction="UNRESOLVED")]
    )
    built = result.graph["edges"][0]
    assert built["target_node_id"] is None
    assert built["direction_state"] == "UNRESOLVED"
    assert "DIRECTION_UNRESOLVED" in codes(result)


def test_missing_endpoint_is_not_filled_by_the_neighbour():
    # Two nodes, an edge that names only the source: the target must stay null.
    result = assemble_flow(
        nodes=[node("N1"), node("N2")], edges=[edge("E1", "N1", None)]
    )
    assert result.graph["edges"][0]["target_node_id"] is None


def test_unknown_endpoint_is_refused_not_invented():
    with pytest.raises(ExtensionError) as excinfo:
        assemble_flow(nodes=[node("N1")], edges=[edge("E1", "N1", "N404")])
    assert "not a node of this graph" in str(excinfo.value)


def test_no_edge_is_created_from_proximity():
    # Two nodes that sit next to each other on the page: with no edge input,
    # the graph must have zero edges. There is no proximity code path at all.
    result = assemble_flow(nodes=[node("N1"), node("N2")], edges=[])
    assert result.graph["edges"] == []
    assert codes(result).count("ISOLATED_NODE") == 2


def test_isolated_node_is_kept_and_reported():
    result = assemble_flow(
        nodes=[node("N1"), node("N2"), node("N3")], edges=[edge("E1", "N1", "N2")]
    )
    assert [n["node_id"] for n in result.graph["nodes"]] == ["N1", "N2", "N3"]
    assert "ISOLATED_NODE" in codes(result)


def test_decision_without_exit_is_reported():
    result = assemble_flow(
        nodes=[node("N1", "DECISION"), node("N2")], edges=[edge("E1", "N2", "N1")]
    )
    assert "DECISION_WITHOUT_EXIT" in codes(result)


def test_cycle_is_reported_and_edges_are_not_removed():
    result = assemble_flow(
        nodes=[node("N1", "START"), node("N2"), node("N3", "END")],
        edges=[edge("E1", "N1", "N2"), edge("E2", "N2", "N3"), edge("E3", "N3", "N2")],
    )
    assert "CYCLE_PRESENT" in codes(result)
    assert len(result.graph["edges"]) == 3  # nothing was sorted away


def test_find_cycles_returns_the_loop_nodes():
    graph = {
        "edges": [
            {"source_node_id": "A", "target_node_id": "B"},
            {"source_node_id": "B", "target_node_id": "A"},
        ]
    }
    cycles = find_cycles(graph)
    assert cycles and set(cycles[0]) == {"A", "B"}


def test_duplicate_node_id_is_refused():
    with pytest.raises(ExtensionError):
        assemble_flow(nodes=[node("N1"), node("N1")], edges=[])


def test_invalid_role_is_refused():
    with pytest.raises(ExtensionError):
        assemble_flow(nodes=[{"node_id": "N1", "role": "BRANCH"}], edges=[])


def test_review_ref_cannot_confirm_an_unresolved_edge():
    result = assemble_flow(
        nodes=[node("N1"), node("N2")],
        edges=[edge("E1", "N1", "N2", direction="BIDIRECTIONAL")],
        review_decisions={"E1": "RV-1"},
    )
    built = result.graph["edges"][0]
    assert built["direction_state"] == "UNRESOLVED"
    assert built["review_decision_ref"] is None
    assert "REVIEW_REF_IGNORED" in codes(result)


def test_graph_satisfies_the_published_flow_contract():
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    contract_dir = repo / "contracts" / "doc2md-19cap" / "v1.0.0"
    if str(contract_dir) not in sys.path:
        sys.path.insert(0, str(contract_dir))
    import contract_models

    result = assemble_flow(
        nodes=[node("N1", "START"), node("N2", "END")],
        edges=[edge("E1", "N1", "N2")],
        review_decisions={"E1": "RV-1"},
    )
    contract_models.FlowGraph.model_validate(result.graph)
