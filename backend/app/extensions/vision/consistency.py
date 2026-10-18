"""Graph structure, control flow and text-vs-image checks (SDD 10.3, ``PI-F05-03``).

Three layers, with deliberately different consequences:

* **Structure** — duplicate node ids, edges pointing at missing nodes: hard errors
  that block confirmation. An isolated node is *not* one: it is a review finding,
  and the node stays.
* **Control flow** — missing START/END, a decision without a labelled branch, a
  swimlane jump, a return flow. All findings, none of them auto-fixed. In
  particular a cycle is reported and kept: SDD says "有环不是一律错误；不能强行
  拓扑排序删回流".
* **Text vs image** — each claim is compared with the passage that is supposed to
  describe it and yields ``CONSISTENT / CONFLICT / UNKNOWN``. A model's opinion is
  only a candidate, and a conflict keeps **both** references: nothing is resolved
  by a date or by preference.

The function never mutates its input; a test asserts the graph is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError
from .flow import ROLES, find_cycles

#: Words that flip a sentence's polarity. Used only to *detect disagreement*, never
#: to rewrite a requirement.
NEGATION_WORDS = ("不得", "禁止", "不可", "无需", "不需", "不能", "取消", "除外")

__all__ = [
    "NEGATION_WORDS",
    "ConsistencyReport",
    "check_flow",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class ConsistencyReport:
    hard_errors: list[dict[str, Any]] = field(default_factory=list)
    soft_findings: list[dict[str, Any]] = field(default_factory=list)
    text_image_verdicts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def confirmed_ok(self) -> bool:
        """True only when nothing hard blocks confirmation (findings may remain)."""
        return not self.hard_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "hard_errors": [dict(i) for i in self.hard_errors],
            "soft_findings": [dict(i) for i in self.soft_findings],
            "text_image_verdicts": [dict(i) for i in self.text_image_verdicts],
        }


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _has_negation(text: str) -> bool:
    return any(word in text for word in NEGATION_WORDS)


def check_flow(
    graph: Mapping[str, Any],
    *,
    text_context: Sequence[Mapping[str, Any]] = (),
    chapter_ref: str | None = None,
) -> ConsistencyReport:
    """Return structure/control/text findings for ``graph`` without touching it.

    ``text_context`` entries are ``{claim_id, target_id, passage}``: the passage is
    the body text that is supposed to describe that node or edge.
    """
    if not isinstance(graph, Mapping):
        raise _input_error(f"graph must be a mapping, got {type(graph).__name__}")

    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []

    nodes_raw = graph.get("nodes") or ()
    edges_raw = graph.get("edges") or ()
    if isinstance(nodes_raw, (str, bytes)) or not isinstance(nodes_raw, Sequence):
        raise _input_error("graph.nodes must be a sequence")
    if isinstance(edges_raw, (str, bytes)) or not isinstance(edges_raw, Sequence):
        raise _input_error("graph.edges must be a sequence")

    node_by_id: dict[str, Mapping[str, Any]] = {}
    for index, node in enumerate(nodes_raw):
        if not isinstance(node, Mapping):
            raise _input_error(f"graph.nodes[{index}] must be a mapping")
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise _input_error(f"graph.nodes[{index}] has no usable node_id")
        if node_id in node_by_id:
            hard.append(
                {
                    "code": "DUPLICATE_NODE_ID",
                    "node_id": node_id,
                    "message": "two nodes share an id; the graph is not a graph",
                }
            )
            continue
        node_by_id[node_id] = node
        role = node.get("role")
        if role not in ROLES:
            hard.append(
                {
                    "code": "INVALID_ROLE",
                    "node_id": node_id,
                    "message": f"role must be one of {list(ROLES)}, got {role!r}",
                }
            )

    incident: dict[str, int] = {node_id: 0 for node_id in node_by_id}
    outgoing: dict[str, list[Mapping[str, Any]]] = {node_id: [] for node_id in node_by_id}
    for index, edge in enumerate(edges_raw):
        if not isinstance(edge, Mapping):
            raise _input_error(f"graph.edges[{index}] must be a mapping")
        edge_id = edge.get("edge_id") or f"<edge {index}>"
        for name in ("source_node_id", "target_node_id"):
            endpoint = edge.get(name)
            if endpoint is None:
                continue
            if endpoint not in node_by_id:
                hard.append(
                    {
                        "code": "MISSING_ENDPOINT",
                        "edge_id": edge_id,
                        "message": f"{name} = {endpoint!r} is not a node of this graph",
                    }
                )
                continue
            incident[endpoint] += 1
        source = edge.get("source_node_id")
        if source in outgoing:
            outgoing[source].append(edge)
        state = edge.get("direction_state")
        if state not in ("CANDIDATE", "CONFIRMED", "UNRESOLVED"):
            hard.append(
                {
                    "code": "INVALID_EDGE_STATE",
                    "edge_id": edge_id,
                    "message": f"unexpected direction_state {state!r}",
                }
            )

    for node_id, node in node_by_id.items():
        if incident.get(node_id, 0) == 0:
            soft.append(
                {
                    "code": "ISOLATED_NODE",
                    "node_id": node_id,
                    "message": "node carries no edge; kept for review, not deleted",
                }
            )
        if node.get("role") == "DECISION":
            exits = outgoing.get(node_id, [])
            if not exits:
                soft.append(
                    {
                        "code": "DECISION_WITHOUT_EXIT",
                        "node_id": node_id,
                        "message": "decision node has no outgoing edge",
                    }
                )
            else:
                labels = {(edge.get("label") or "").strip() for edge in exits}
                if len(exits) < 2 or not any(labels):
                    soft.append(
                        {
                            "code": "BRANCH_LABEL_INCOMPLETE",
                            "node_id": node_id,
                            "message": (
                                "a decision needs at least two labelled exits; one unlabelled "
                                "exit cannot express both branches"
                            ),
                            "detail": {"exits": len(exits), "labels": sorted(labels)},
                        }
                    )

    roles = {node.get("role") for node in node_by_id.values()}
    if "START" not in roles:
        soft.append({"code": "NO_START", "message": "graph has no START node"})
    if "END" not in roles:
        soft.append({"code": "NO_END", "message": "graph has no END node"})

    swimlanes = {(node.get("swimlane") or "") for node in node_by_id.values()}
    if len(swimlanes) > 1:
        soft.append(
            {
                "code": "SWIMLANE_CHANGE",
                "message": "graph crosses swimlanes; a hand-off must be explicit",
                "detail": {"swimlanes": sorted(swimlanes)},
            }
        )

    for cycle in find_cycles(graph):
        soft.append(
            {
                "code": "CYCLE_PRESENT",
                "message": "return flow detected; reported and kept, never sorted away",
                "detail": {"cycle": cycle},
            }
        )

    verdicts: list[dict[str, Any]] = []
    for index, claim in enumerate(text_context):
        if not isinstance(claim, Mapping):
            raise _input_error(f"text_context[{index}] must be a mapping")
        claim_id = claim.get("claim_id") or f"CL{index}"
        target_id = claim.get("target_id")
        passage = claim.get("passage")
        if not isinstance(target_id, str) or not target_id:
            raise _input_error(f"text_context[{index}].target_id must be a non-empty string")
        if not isinstance(passage, str) or not passage.strip():
            raise _input_error(f"text_context[{index}].passage must be non-empty text")

        node = node_by_id.get(target_id)
        node_text = node.get("text") if node else None
        if node is None:
            verdict, reason = "UNKNOWN", "TARGET_NOT_FOUND"
        elif not node_text:
            verdict, reason = "UNKNOWN", "NODE_HAS_NO_TEXT"
        else:
            normalised_node = _normalise(node_text)
            normalised_passage = _normalise(passage)
            if normalised_node not in normalised_passage:
                verdict, reason = "UNKNOWN", "TEXT_DOES_NOT_MENTION_NODE"
            elif _has_negation(normalised_node) != _has_negation(normalised_passage):
                verdict, reason = "CONFLICT", "NEGATION_MISMATCH"
            else:
                verdict, reason = "CONSISTENT", "ACTION_PRESENT_WITH_SAME_POLARITY"

        verdicts.append(
            {
                "claim_id": claim_id,
                "target_id": target_id,
                "chapter_ref": chapter_ref,
                "verdict": verdict,
                "reason": reason,
                # Both sides are kept so a reviewer sees the disagreement itself.
                "node_text": node_text,
                "passage": passage,
            }
        )

    return ConsistencyReport(
        hard_errors=hard, soft_findings=soft, text_image_verdicts=verdicts
    )
