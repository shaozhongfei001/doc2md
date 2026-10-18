"""Approved-VLM structured response adapter (SDD 10.2, ``PI-F05-02``).

The engine is never called here: this turns an **already captured** VLM response
into this run's objects. SDD 10.2.4 says the adapter must reject

    "源图不存在的ID、外部URL、工具调用或指令字段"

and that is the whole point of the module — a vision model is an untrusted input
source, and "it returned JSON" proves nothing beyond the format.

Deliberate refusals:

* any ``http://`` / ``https://`` string anywhere in the payload (an image URL is
  how a model smuggles in a fetch it was told not to make);
* any ``tool_call`` / ``function_call`` / ``tools`` / ``system`` / ``instruction``
  / ``prompt`` key (prompt injection attempts arrive as fields);
* edges naming node ids that do not exist in this payload;
* a missing explicit ``id_map`` — array order is never an identity.

Confidences are passed through untouched; a missing one stays ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

ROLES = ("START", "END", "ACTION", "DECISION", "UNKNOWN")

#: Keys that indicate an attempt to steer the agent rather than describe a graph.
FORBIDDEN_KEYS = frozenset(
    {"tool_call", "tool_calls", "function_call", "functions", "tools", "system", "instruction", "prompt"}
)

__all__ = [
    "FORBIDDEN_KEYS",
    "ROLES",
    "VlmFlowResult",
    "read_vlm_flow",
]


def _drift(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_ENGINE_SCHEMA_DRIFT", message, detail=detail or None)


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class VlmFlowResult:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [dict(node) for node in self.nodes],
            "edges": [dict(edge) for edge in self.edges],
            "issues": [dict(issue) for issue in self.issues],
        }


def _scan_forbidden(value: Any, *, path: str = "$") -> None:
    """Walk the payload once; refuse URLs and instruction-looking keys."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_KEYS:
                raise _input_error(
                    f"{path}.{key} is an instruction/tool field; a graph response may not "
                    "carry them",
                    path=f"{path}.{key}",
                )
            _scan_forbidden(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_forbidden(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
        raise _input_error(
            f"{path} is an external URL; the run only accepts references to its own assets",
            path=path,
            value=value[:120],
        )


def _point(value: Any, *, name: str) -> list[list[float]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        raise _drift(f"{name} must be four points, got {value!r}")
    out: list[list[float]] = []
    for index, point in enumerate(value):
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
            raise _drift(f"{name}[{index}] must be a pair (x, y)")
        for coordinate in point:
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise _drift(f"{name}[{index}] has a non-numeric coordinate {coordinate!r}")
        out.append([float(point[0]), float(point[1])])
    return out


def read_vlm_flow(
    payload: Mapping[str, Any],
    *,
    id_map: Mapping[str, str],
    evidence_ids: Sequence[str] = (),
) -> VlmFlowResult:
    """Validate a captured VLM flow response and map its ids to local node ids.

    ``id_map`` maps *model* node id → *this run's* node id and must be complete;
    a partial map is refused rather than filled in by position.
    """
    if not isinstance(payload, Mapping):
        raise _input_error(f"payload must be a mapping, got {type(payload).__name__}")
    if not isinstance(id_map, Mapping):
        raise _input_error(
            "id_map is required: model ids are not positional and cannot be inferred"
        )
    for key, value in id_map.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
            raise _input_error(f"id_map contains {key!r}:{value!r}; both sides must be ids")

    _scan_forbidden(payload)

    nodes_raw = payload.get("nodes")
    edges_raw = payload.get("edges", [])
    if isinstance(nodes_raw, (str, bytes)) or not isinstance(nodes_raw, Sequence) or not nodes_raw:
        raise _drift("response has no usable 'nodes' list")
    if isinstance(edges_raw, (str, bytes)) or not isinstance(edges_raw, Sequence):
        raise _drift("response 'edges' must be a list")

    evidence = [eid for eid in evidence_ids]
    model_ids: list[str] = []
    nodes: list[dict[str, Any]] = []
    for index, raw in enumerate(nodes_raw):
        if not isinstance(raw, Mapping):
            raise _drift(f"nodes[{index}] must be an object")
        model_id = raw.get("id")
        if not isinstance(model_id, str) or not model_id:
            raise _drift(f"nodes[{index}] has no usable id")
        if model_id in model_ids:
            raise _drift(f"duplicate model node id {model_id!r}")
        model_ids.append(model_id)
        role = raw.get("role", "UNKNOWN")
        if role not in ROLES:
            raise _drift(
                f"nodes[{index}].role must be one of {list(ROLES)}, got {role!r}"
            )
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise _drift(f"nodes[{index}].text must be a string or null")
        swimlane = raw.get("swimlane")
        if swimlane is not None and not isinstance(swimlane, str):
            raise _drift(f"nodes[{index}].swimlane must be a string or null")
        bbox = raw.get("bbox")
        node: dict[str, Any] = {
            "model_id": model_id,
            "text": text,
            "role": role,
            "swimlane": swimlane,
            "bbox": _point(bbox, name=f"nodes[{index}].bbox") if bbox is not None else None,
            "confidence": raw.get("confidence"),
            "evidence_ids": evidence,
        }
        missing = model_id if model_id not in id_map else None
        if missing is not None:
            raise _input_error(
                f"model node {missing!r} has no local id in id_map; matching by array "
                "position is exactly what this adapter refuses",
                model_id=missing,
            )
        node["node_id"] = id_map[model_id]
        nodes.append(node)

    edges: list[dict[str, Any]] = []
    for index, raw in enumerate(edges_raw):
        if not isinstance(raw, Mapping):
            raise _drift(f"edges[{index}] must be an object")
        edge_id = raw.get("id") or f"E{index}"
        if not isinstance(edge_id, str) or not edge_id:
            raise _drift(f"edges[{index}] has no usable id")
        source = raw.get("source")
        target = raw.get("target")
        for name, value in (("source", source), ("target", target)):
            if value is None:
                continue
            if not isinstance(value, str):
                raise _drift(f"edges[{index}].{name} must be a string or null")
            if value not in model_ids:
                raise _drift(
                    f"edges[{index}].{name} = {value!r} names a node that does not exist in "
                    "this response; a missing node is not invented"
                )
        direction = raw.get("direction", "UNRESOLVED")
        if direction not in ("DIRECTED", "BIDIRECTIONAL", "UNRESOLVED"):
            raise _drift(
                f"edges[{index}].direction must be DIRECTED/BIDIRECTIONAL/UNRESOLVED, got "
                f"{direction!r}"
            )
        edges.append(
            {
                "edge_id": edge_id,
                "source_model_id": source,
                "target_model_id": target,
                "label": raw.get("label"),
                "direction": direction,
                "evidence_ids": evidence,
            }
        )

    return VlmFlowResult(nodes=nodes, edges=edges)
