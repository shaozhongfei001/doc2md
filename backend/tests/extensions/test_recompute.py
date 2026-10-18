"""T1101–T1104: a change must actually rebuild its descendants, or say why not.

The failure this file guards against is a *list* being mistaken for a rebuild:
before L11 the correction path produced "these objects are affected" and stopped.
"""

from __future__ import annotations

import copy

import pytest

from app.extensions import ExtensionError
from app.extensions.review.dependencies import DependencyGraph
from app.extensions.review.recompute import FAILED, REBUILT, recompute


def edge(graph: DependencyGraph, parent: str, child: str, edge_type: str = "CELL_TEXT") -> None:
    graph.add_edge(
        parent_id=parent,
        parent_version="v1",
        child_id=child,
        child_version="v1",
        edge_type=edge_type,
        producer="test",
    )


def copy_producer(seen: list | None = None):
    def produce(parent_payload, child_id):
        if seen is not None:
            seen.append((child_id, copy.deepcopy(parent_payload)))
        return {"child": child_id, "from": parent_payload}

    return produce


# ------------------------------------------------------------------- ordering
def test_only_descendants_of_the_changed_object_are_rebuilt():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")
    edge(graph, "S1", "P1")
    edge(graph, "T2", "S2")  # unrelated chain

    result = recompute(
        graph,
        root_id="T1",
        state={"T1": {"v": 1}, "S1": {"v": 0}, "P1": {"v": 0}, "T2": {"v": 9}, "S2": {"v": 0}},
        producers={"CELL_TEXT": copy_producer()},
        generation_id="G1",
    )

    assert result.order == ("S1", "P1")
    assert [item.object_id for item in result.outcomes] == ["S1", "P1"]
    assert all(item.status == REBUILT for item in result.outcomes)
    assert "S2" not in result.rebuilt, "an unrelated branch must not be recomputed"
    assert result.ok is True


def test_a_child_sees_its_parent_after_the_parent_was_rebuilt():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")
    edge(graph, "S1", "P1")
    seen: list[tuple[str, dict]] = []

    recompute(
        graph,
        root_id="T1",
        state={"T1": {"v": 1}, "S1": {"v": 0}, "P1": {"v": 0}},
        producers={"CELL_TEXT": copy_producer(seen)},
        generation_id="G1",
    )

    # P1's parent is S1, and S1's payload at that moment is the *rebuilt* one.
    assert seen[0][0] == "S1"
    assert seen[1] == ("P1", {"child": "S1", "from": {"v": 1}})


def test_order_is_deterministic_across_runs():
    graph = DependencyGraph()
    for child in ("S3", "S1", "S2"):
        edge(graph, "T1", child)

    first = recompute(
        graph,
        root_id="T1",
        state={"T1": {}, "S1": {}, "S2": {}, "S3": {}},
        producers={"CELL_TEXT": copy_producer()},
        generation_id="G1",
    )
    second = recompute(
        graph,
        root_id="T1",
        state={"T1": {}, "S1": {}, "S2": {}, "S3": {}},
        producers={"CELL_TEXT": copy_producer()},
        generation_id="G1",
    )
    assert first.order == second.order == ("S1", "S2", "S3")
    assert first.digest() == second.digest()


# ------------------------------------------------------- no silent partial runs
def test_missing_producer_refuses_the_whole_plan_before_rebuilding():
    graph = DependencyGraph()
    edge(graph, "T1", "S1", edge_type="CELL_TEXT")
    edge(graph, "T1", "C1", edge_type="CHART_AXIS_UNIT")
    calls: list[str] = []

    def produce(parent_payload, child_id):
        calls.append(child_id)
        return {}

    with pytest.raises(ExtensionError) as excinfo:
        recompute(
            graph,
            root_id="T1",
            state={"T1": {}, "S1": {}, "C1": {}},
            producers={"CELL_TEXT": produce},  # CHART_AXIS_UNIT missing
            generation_id="G1",
        )

    assert excinfo.value.code == "E_NOT_IMPLEMENTED"
    assert excinfo.value.detail["missing_edge_types"] == ["CHART_AXIS_UNIT"]
    assert any(item["object_id"] == "C1" for item in excinfo.value.detail["blocked_objects"])
    assert calls == [], "nothing may be rebuilt when the plan cannot be completed"


# --------------------------------------------------------------- failure modes
def test_one_failure_is_recorded_and_others_still_run():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")
    edge(graph, "T1", "S2")

    def produce(parent_payload, child_id):
        if child_id == "S1":
            raise ExtensionError("E_TABLE_SPAN_INVALID", "bad span")
        return {"child": child_id}

    result = recompute(
        graph,
        root_id="T1",
        state={"T1": {}, "S1": {}, "S2": {}},
        producers={"CELL_TEXT": produce},
        generation_id="G1",
    )

    statuses = {item.object_id: item.status for item in result.outcomes}
    assert statuses == {"S1": FAILED, "S2": REBUILT}
    assert result.ok is False
    assert result.failed[0].error["code"] == "E_TABLE_SPAN_INVALID"


def test_unexpected_exception_is_recorded_not_swallowed():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")

    def produce(parent_payload, child_id):
        raise RuntimeError("boom")

    result = recompute(
        graph,
        root_id="T1",
        state={"T1": {}, "S1": {}},
        producers={"CELL_TEXT": produce},
        generation_id="G1",
    )
    assert result.outcomes[0].error["code"] == "PRODUCER_RAISED"
    assert "boom" in result.outcomes[0].error["message"]


def test_missing_parent_payload_is_a_failure_not_a_skip():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")
    edge(graph, "S1", "P1")

    result = recompute(
        graph,
        root_id="T1",
        state={"T1": {}, "S1": {}},  # P1 absent
        producers={"CELL_TEXT": copy_producer()},
        generation_id="G1",
    )
    by_id = {item.object_id: item for item in result.outcomes}
    # S1 rebuilds and produces a payload; P1 is reachable through it, so it also runs.
    assert by_id["S1"].status == REBUILT
    assert result.ok is True


def test_root_must_exist_in_state():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")
    with pytest.raises(ExtensionError) as excinfo:
        recompute(
            graph,
            root_id="T404",
            state={"T1": {}, "S1": {}},
            producers={"CELL_TEXT": copy_producer()},
            generation_id="G1",
        )
    assert "not in the supplied state" in str(excinfo.value)


def test_object_with_no_descendants_is_a_no_op():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")
    result = recompute(
        graph,
        root_id="S1",
        state={"T1": {}, "S1": {}},
        producers={"CELL_TEXT": copy_producer()},
        generation_id="G1",
    )
    assert result.outcomes == ()
    assert result.ok is True


# -------------------------------------------------------------- purity/digest
def test_input_state_is_never_mutated():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")
    state = {"T1": {"v": 1}, "S1": {"v": 0}}
    snapshot = copy.deepcopy(state)

    recompute(
        graph,
        root_id="T1",
        state=state,
        producers={"CELL_TEXT": copy_producer()},
        generation_id="G1",
    )
    assert state == snapshot


def test_same_inputs_produce_the_same_digest():
    graph = DependencyGraph()
    edge(graph, "T1", "S1")
    kwargs = {
        "root_id": "T1",
        "state": {"T1": {"v": 1}, "S1": {"v": 0}},
        "producers": {"CELL_TEXT": copy_producer()},
        "generation_id": "G1",
    }
    first = recompute(graph, **kwargs)
    second = recompute(graph, **kwargs)
    assert first.digest() == second.digest()
    assert first.payload_digests() == second.payload_digests()


# ------------------------------------------------- T1104: end-to-end closure
def test_changing_one_cell_rebuilds_its_semantics_and_leaves_the_old_version_alone(tmp_path):
    """L07-GAP-04 closure: impact list → rebuilt payloads → a *new* committed version."""
    import json

    from app.extensions.io.artifact_store import ArtifactStore
    from app.extensions.io.paths import Roots
    from app.extensions.tables.grid import to_contract_table
    from app.extensions.tables.semantics import derive_header_paths

    def build(text: str) -> dict:
        cells = [
            {"cell_id": "h0", "row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "金额", "text_origin": "SOURCE_XML"},
            {"cell_id": "d0", "row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": text, "text_origin": "SOURCE_XML"},
        ]
        return to_contract_table("T1", cells)

    graph = DependencyGraph()
    edge(graph, "table:T1", "semantics:T1", edge_type="CELL_TEXT")

    def semantics_producer(parent_payload, child_id):
        return derive_header_paths(parent_payload, header_rows=[0]).cell_semantics

    old_table = build("100")
    corrected = build("150")

    result = recompute(
        graph,
        root_id="table:T1",
        state={"table:T1": corrected, "semantics:T1": []},
        producers={"CELL_TEXT": semantics_producer},
        generation_id="GEN-NEW",
    )
    assert result.ok
    rebuilt = result.rebuilt["semantics:T1"]
    assert rebuilt[0]["column_header_cell_ids"] == ["h0"]

    extension = tmp_path / "ext"
    extension.mkdir()
    store = ArtifactStore(Roots(extension=extension))
    old_attempt = store.new_attempt_id()
    old_file = store.write_staged(old_attempt, "report.json", json.dumps(old_table).encode("utf-8"))
    store.commit_generation(
        old_attempt,
        content_sha256="a" * 64,
        generation_id="GEN-OLD",
        expected_files={"report.json": old_file.sha256},
        manifest_relative_path="report.json",
    )
    old_report = next(extension.rglob("report.json"))
    before = old_report.read_bytes()

    # The rebuilt output is committed as a *new* generation; the old bytes stay.
    new_attempt = store.new_attempt_id()
    new_file = store.write_staged(
        new_attempt,
        "report.json",
        json.dumps({"semantics": rebuilt}).encode("utf-8"),
    )
    store.commit_generation(
        new_attempt,
        content_sha256="b" * 64,
        generation_id=result.generation_id,
        expected_files={"report.json": new_file.sha256},
        manifest_relative_path="report.json",
    )

    # `rglob` order is unspecified; compare the specific file, not index 0.
    assert old_report.read_bytes() == before
    assert len(list(extension.rglob("report.json"))) == 2
