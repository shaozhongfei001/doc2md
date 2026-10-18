"""T703 / ``PI-F04-09``: the processing DAG is acyclic, and impact is not guessed."""

from __future__ import annotations

import jsonpatch
import pytest

from app.extensions import ExtensionError
from app.extensions.review.dependencies import (
    DependencyGraph,
    impact_of,
    reverse_patch,
)


def edge(graph: DependencyGraph, parent: str, child: str, edge_type: str = "CELL_TEXT") -> None:
    graph.add_edge(
        parent_id=parent,
        parent_version="v1",
        child_id=child,
        child_version="v1",
        edge_type=edge_type,
        producer="extractor",
    )


def test_edges_are_recorded_with_producer_metadata():
    graph = DependencyGraph()
    edge(graph, "header-1", "cell-1", edge_type="HEADER_CELL")
    assert graph.edges[0]["producer"] == "extractor"
    assert graph.edges[0]["edge_type"] == "HEADER_CELL"


def test_cycle_in_the_processing_dag_is_refused():
    graph = DependencyGraph()
    edge(graph, "A", "B")
    with pytest.raises(ExtensionError) as excinfo:
        edge(graph, "B", "A")
    assert "would close a cycle" in str(excinfo.value)


def test_self_dependency_is_refused():
    graph = DependencyGraph()
    with pytest.raises(ExtensionError) as excinfo:
        edge(graph, "A", "A")
    assert "self dependency" in str(excinfo.value)


def test_descendants_are_transitive_and_stable():
    graph = DependencyGraph()
    edge(graph, "H", "C1", edge_type="HEADER_CELL")
    edge(graph, "C1", "S1", edge_type="CELL_TEXT")
    edge(graph, "H", "C2", edge_type="HEADER_CELL")
    assert graph.descendants("H") == ["C1", "C2", "S1"]
    assert graph.descendants("S1") == []


def test_impact_collects_the_object_kinds_of_the_reached_edges():
    graph = DependencyGraph()
    edge(graph, "H", "C1", edge_type="HEADER_CELL")
    edge(graph, "C1", "S1", edge_type="CELL_TEXT")
    impact = graph.impact("H")
    assert impact["affected_object_ids"] == ["C1", "S1"]
    # Header fan-out plus everything the downstream cell-text edge implies.
    assert set(impact["affected_kinds"]) == {
        "COLUMN_PATH",
        "CELL_SEMANTICS",
        "DERIVED_TEXT",
        "EXTERNAL_INDEX",
        "CANDIDATE_EVIDENCE",
        "CHART_POINT",
    }


def test_a_cell_change_does_not_invalidate_an_unrelated_table():
    graph = DependencyGraph()
    edge(graph, "cell-A", "out-A", edge_type="CELL_TEXT")
    edge(graph, "cell-B", "out-B", edge_type="CELL_TEXT")
    assert graph.impact("cell-A")["affected_object_ids"] == ["out-A"]


@pytest.mark.parametrize(
    "kind",
    ["HEADER_CELL", "CELL_TEXT", "NOTE_SCOPE", "CONTINUATION", "FLOW_EDGE", "CHART_AXIS_UNIT", "SOURCE_REVOKED"],
)
def test_every_documented_change_kind_has_an_impact_entry(kind):
    assert impact_of(kind)


def test_unknown_change_kind_is_refused_not_ignored():
    with pytest.raises(ExtensionError) as excinfo:
        impact_of("VIBES")
    assert "unknown change kind" in str(excinfo.value)


# ------------------------------------------------------------- reverse patch
def test_reverse_patch_restores_the_previous_value_on_the_current_version():
    current = {"tables": [{"cells": [{"text": "150"}]}]}
    ops = reverse_patch(
        current,
        ["/tables/0/cells/0/text"],
        previous_values={"/tables/0/cells/0/text": "100"},
    )
    assert [op["op"] for op in ops] == ["test", "replace"]
    restored = jsonpatch.JsonPatch(ops).apply(current, in_place=False)
    assert restored["tables"][0]["cells"][0]["text"] == "100"


def test_reverse_patch_without_a_recorded_value_is_refused():
    with pytest.raises(ExtensionError) as excinfo:
        reverse_patch({}, ["/a"], previous_values={})
    assert "cannot invent one" in str(excinfo.value)


def test_reverse_patch_is_a_new_patch_not_a_history_edit():
    ops = reverse_patch({"a": 2}, ["/a"], previous_values={"/a": 1})
    # It is expressed entirely against the current document.
    assert ops[0] == {"op": "test", "path": "/a", "value": 2}
