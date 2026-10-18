"""Flow nodes and arrow *candidates* (SDD 10.2, ``PI-F05-02``).

Two properties are structural, not stylistic:

1. **No edge is ever invented.** There is no code path here that looks at
   coordinates, reading order or distances to create an edge — that is why the
   module takes edges as an input instead of deriving them. SDD 10.2.5:
   "不按阅读顺序/距离补边".
2. **A cycle is not an error.** Return flows are real in bank processes;
   ``_find_cycles`` reports them so a reviewer can look, and never sorts them
   away. SDD 10.3: "有环不是一律错误；不能强行拓扑排序删回流".

``direction_state`` starts at ``UNRESOLVED`` unless the engine stated the
direction *and* both endpoints are known; ``CONFIRMED`` is reachable only through
a review decision (the contract enforces the reference).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

ROLES = ("START", "END", "ACTION", "DECISION", "UNKNOWN")

__all__ = [
    "ROLES",
    "FlowResult",
    "assemble_flow",
    "find_cycles",
    "nodes_from_ocr",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class FlowResult:
    """A ``FlowGraph``-shaped payload plus the review findings about it."""

    graph: dict[str, Any]
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"graph": dict(self.graph), "issues": [dict(i) for i in self.issues]}


def nodes_from_ocr(
    lines: Sequence[Mapping[str, Any]],
    *,
    visual_id: str,
    swimlane_map: Mapping[int, str] | None = None,
    evidence_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Build one node per OCR line.

    Repeated text stays separate on purpose: a flow chart that says "审核" three
    times has three steps, and merging them would invent a loop the chart does not
    have. SDD 10.2.1: "重复文字仍有不同node_id".
    """
    if not isinstance(visual_id, str) or not visual_id:
        raise _input_error(f"visual_id must be a non-empty string, got {visual_id!r}")
    evidence = list(evidence_ids)
    nodes: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            raise _input_error(f"lines[{index}] must be a mapping")
        text = line.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _input_error(f"lines[{index}].text must be a non-empty string")
        swimlane = line.get("swimlane")
        if swimlane is None and swimlane_map is not None:
            swimlane = swimlane_map.get(index)
        if swimlane is not None and not isinstance(swimlane, str):
            raise _input_error(f"lines[{index}].swimlane must be a string or null")
        nodes.append(
            {
                "node_id": f"{visual_id}-N{index:03d}",
                "text": text.strip(),
                "role": "UNKNOWN",
                "swimlane": swimlane,
                "evidence_ids": evidence,
            }
        )
    return nodes


def find_cycles(graph: Mapping[str, Any]) -> list[list[str]]:
    """Return every simple cycle found by DFS; ordering is stable for reporting."""
    adjacency: dict[str, list[str]] = {}
    for edge in graph.get("edges") or ():
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        if source is None or target is None:
            continue
        adjacency.setdefault(source, []).append(target)

    cycles: list[list[str]] = []
    seen_cycles: set[frozenset[str]] = set()
    for start in list(adjacency):
        stack: list[tuple[str, list[str]]] = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for neighbour in adjacency.get(node, ()):
                if neighbour == start and len(path) >= 1:
                    key = frozenset(path)
                    if key not in seen_cycles:
                        seen_cycles.add(key)
                        cycles.append(list(path))
                    continue
                if neighbour in path:
                    continue
                if len(path) > 64:  # pragma: no cover - defensive bound
                    continue
                stack.append((neighbour, path + [neighbour]))
    return cycles


def assemble_flow(
    *,
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]] = (),
    review_decisions: Mapping[str, str] | None = None,
) -> FlowResult:
    """Validate nodes/edges into a ``FlowGraph`` and report review findings.

    ``edges`` must already be in *local* node ids (the adapter maps model ids).
    ``review_decisions`` maps ``edge_id -> review_decision_ref``; without one no
    edge is ``CONFIRMED``.
    """
    decisions = dict(review_decisions or {})
    issues: list[dict[str, Any]] = []
    node_rows: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    for index, raw in enumerate(nodes):
        if not isinstance(raw, Mapping):
            raise _input_error(f"nodes[{index}] must be a mapping")
        node_id = raw.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise _input_error(f"nodes[{index}] has no usable node_id")
        if node_id in node_ids:
            raise _input_error(f"duplicate node_id {node_id!r}")
        node_ids.add(node_id)
        role = raw.get("role", "UNKNOWN")
        if role not in ROLES:
            raise _input_error(
                f"node {node_id}: role must be one of {list(ROLES)}, got {role!r}"
            )
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise _input_error(f"node {node_id}: text must be a string or null")
        swimlane = raw.get("swimlane")
        if swimlane is not None and not isinstance(swimlane, str):
            raise _input_error(f"node {node_id}: swimlane must be a string or null")
        node_rows.append(
            {
                "node_id": node_id,
                "text": text,
                "role": role,
                "swimlane": swimlane,
                "evidence_ids": list(raw.get("evidence_ids") or ()),
            }
        )

    edge_rows: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    outgoing: dict[str, int] = {}
    incident: dict[str, int] = {}

    for index, raw in enumerate(edges):
        if not isinstance(raw, Mapping):
            raise _input_error(f"edges[{index}] must be a mapping")
        edge_id = raw.get("edge_id")
        if not isinstance(edge_id, str) or not edge_id:
            raise _input_error(f"edges[{index}] has no usable edge_id")
        if edge_id in edge_ids:
            raise _input_error(f"duplicate edge_id {edge_id!r}")
        edge_ids.add(edge_id)

        source = raw.get("source_node_id")
        target = raw.get("target_node_id")
        for name, value in (("source_node_id", source), ("target_node_id", target)):
            if value is None:
                continue
            if not isinstance(value, str):
                raise _input_error(f"edge {edge_id}: {name} must be a string or null")
            if value not in node_ids:
                raise _input_error(
                    f"edge {edge_id}: {name} = {value!r} is not a node of this graph"
                )

        direction = raw.get("direction", "UNRESOLVED")
        if direction not in ("DIRECTED", "BIDIRECTIONAL", "UNRESOLVED"):
            raise _input_error(
                f"edge {edge_id}: direction must be DIRECTED/BIDIRECTIONAL/UNRESOLVED, got "
                f"{direction!r}"
            )

        ref = decisions.get(edge_id)
        if ref is not None and (not isinstance(ref, str) or not ref):
            raise _input_error(
                f"edge {edge_id}: review decision ref must be a non-empty string"
            )

        if direction == "DIRECTED" and source and target:
            state = "CONFIRMED" if ref else "CANDIDATE"
        else:
            state = "UNRESOLVED"
            if direction == "BIDIRECTIONAL":
                issues.append(
                    {
                        "code": "BIDIRECTIONAL_EDGE",
                        "edge_id": edge_id,
                        "message": "both directions drawn; endpoints are not resolved here",
                    }
                )
            elif direction == "UNRESOLVED":
                issues.append(
                    {
                        "code": "DIRECTION_UNRESOLVED",
                        "edge_id": edge_id,
                        "message": "the model did not state a direction; it is not inferred",
                    }
                )
        if ref and state != "CONFIRMED":
            issues.append(
                {
                    "code": "REVIEW_REF_IGNORED",
                    "edge_id": edge_id,
                    "message": "a review ref cannot confirm an edge whose endpoints/direction "
                    "are unresolved",
                }
            )
            ref = None

        for endpoint in (source, target):
            if endpoint:
                incident[endpoint] = incident.get(endpoint, 0) + 1
        if source:
            outgoing[source] = outgoing.get(source, 0) + 1

        edge_rows.append(
            {
                "edge_id": edge_id,
                "source_node_id": source,
                "target_node_id": target,
                "label": raw.get("label"),
                "direction_state": state,
                "evidence_ids": list(raw.get("evidence_ids") or ()),
                "review_decision_ref": ref,
            }
        )

    for node in node_rows:
        node_id = node["node_id"]
        if incident.get(node_id, 0) == 0:
            issues.append(
                {
                    "code": "ISOLATED_NODE",
                    "node_id": node_id,
                    "message": "node has no incident edge; kept for review, not deleted",
                }
            )
        if node["role"] == "DECISION" and outgoing.get(node_id, 0) == 0:
            issues.append(
                {
                    "code": "DECISION_WITHOUT_EXIT",
                    "node_id": node_id,
                    "message": "decision node has no outgoing edge; a branch is missing",
                }
            )

    graph = {"nodes": node_rows, "edges": edge_rows}
    for cycle in find_cycles(graph):
        issues.append(
            {
                "code": "CYCLE_PRESENT",
                "message": "return flow detected; it is reported and kept, never sorted away",
                "detail": {"cycle": cycle},
            }
        )
    return FlowResult(graph=graph, issues=issues)
