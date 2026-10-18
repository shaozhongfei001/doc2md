"""Actually rebuild what a change invalidates (closes ``L07-GAP-04``).

Up to L10 the correction path stopped at a list: ``dependencies.impact()`` said
*which* objects a change touched, and nothing ever re-ran their producers. A list
of affected ids is easy to mistake for a rebuilt document, and that mistake is
invisible — the artifact still looks complete.

This module closes the gap with four properties, each of which is a way the naive
version goes wrong:

1. **Topological order.** A child is rebuilt only after every affected parent, so a
   header change cannot be recomputed against the pre-change header. The order is
   deterministic (stable tie-break on id), because non-deterministic order makes
   idempotency testing meaningless.
2. **No silent skips.** If the plan needs an edge type without a registered
   producer, the whole run is refused with ``E_NOT_IMPLEMENTED`` *before* anything
   is rebuilt. Skipping that object and reporting success is exactly the failure
   mode this module exists to prevent.
3. **Per-object outcomes.** One failing producer does not abort the batch and does
   not vanish: it is recorded as ``FAILED`` with its error while the others
   continue, and the batch reports itself as failed.
4. **Purity.** The input state is deep-copied, nothing is written, and the result
   carries a digest that is identical for identical inputs — so "rebuilt" can be
   compared across runs instead of asserted.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .. import ExtensionError
from .dependencies import DependencyGraph

REBUILT = "REBUILT"
FAILED = "FAILED"
SKIPPED = "SKIPPED"

#: ``producer(parent_payload, child_id) -> new_child_payload``
Producer = Callable[[Mapping[str, Any], str], Any]

__all__ = [
    "FAILED",
    "REBUILT",
    "RebuildOutcome",
    "RebuildResult",
    "Producer",
    "recompute",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class RebuildOutcome:
    object_id: str
    status: str
    edge_type: str | None
    parent_id: str | None
    digest: str | None = None
    error: Mapping[str, Any] | None = None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "object_id": self.object_id,
            "status": self.status,
            "edge_type": self.edge_type,
            "parent_id": self.parent_id,
            "digest": self.digest,
        }
        if self.error is not None:
            payload["error"] = dict(self.error)
        return payload


@dataclass(frozen=True)
class RebuildResult:
    generation_id: str
    outcomes: tuple[RebuildOutcome, ...]
    rebuilt: Mapping[str, Any] = field(default_factory=dict)
    order: tuple[str, ...] = ()

    @property
    def failed(self) -> tuple[RebuildOutcome, ...]:
        return tuple(item for item in self.outcomes if item.status == FAILED)

    @property
    def ok(self) -> bool:
        return not self.failed

    def stats(self) -> dict[str, Any]:
        counts = {REBUILT: 0, FAILED: 0, SKIPPED: 0}
        for item in self.outcomes:
            counts[item.status] = counts.get(item.status, 0) + 1
        return {
            "generation_id": self.generation_id,
            "objects": len(self.outcomes),
            **counts,
            "order": list(self.order),
        }

    def digest(self) -> str:
        """Stable across runs for identical inputs — the basis of the idempotency test."""
        canonical = json.dumps(
            {
                "generation_id": self.generation_id,
                "order": list(self.order),
                "outcomes": [
                    {
                        "object_id": item.object_id,
                        "status": item.status,
                        "digest": item.digest,
                        "edge_type": item.edge_type,
                    }
                    for item in self.outcomes
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def payload_digests(self) -> dict[str, str]:
        return {
            object_id: hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                    "utf-8"
                )
            ).hexdigest()
            for object_id, payload in self.rebuilt.items()
        }


def _subgraph_order(graph: DependencyGraph, root_id: str) -> list[tuple[str, str, str]]:
    """Topological order of the affected subgraph as ``(child, parent, edge_type)``.

    Only edges whose parent is the root or another affected object participate;
    everything else cannot influence this change. Ties break on id so two runs
    produce the same order.
    """
    affected = set(graph.descendants(root_id))
    if not affected:
        return []

    incoming: dict[str, list[tuple[str, str]]] = {object_id: [] for object_id in affected}
    for edge in graph.edges:
        child = edge["child_id"]
        parent = edge["parent_id"]
        if child in affected and (parent == root_id or parent in affected):
            incoming[child].append((parent, edge["edge_type"]))

    remaining = {object_id: set(parent for parent, _ in incoming[object_id]) for object_id in affected}
    ordered: list[tuple[str, str, str]] = []
    # The root is already "changed", not rebuilt: its own payload is the input, so
    # treating it as resolved is what makes its direct children ready.
    resolved: set[str] = {root_id}

    while remaining:
        ready = sorted(
            object_id
            for object_id, parents in remaining.items()
            if not (parents - resolved)
        )
        if not ready:
            # The DAG is validated at edge insertion; reaching this means the graph
            # was mutated behind the API's back.
            raise _input_error(
                "affected subgraph has no executable order; the dependency graph is not "
                "acyclic",
                remaining=sorted(remaining),
            )
        for object_id in ready:
            parent, edge_type = incoming[object_id][0]
            ordered.append((object_id, parent, edge_type))
            resolved.add(object_id)
            del remaining[object_id]
    return ordered


def recompute(
    graph: DependencyGraph,
    *,
    root_id: str,
    state: Mapping[str, Any],
    producers: Mapping[str, Producer],
    generation_id: str,
) -> RebuildResult:
    """Rebuild every object affected by ``root_id``, in dependency order.

    ``state`` maps object id → its current payload. ``producers`` maps
    ``edge_type`` → callable. Nothing is written; the caller decides where the new
    generation goes.
    """
    if not isinstance(graph, DependencyGraph):
        raise _input_error(f"graph must be a DependencyGraph, got {type(graph).__name__}")
    if not isinstance(state, Mapping):
        raise _input_error("state must map object id -> payload")
    if not isinstance(producers, Mapping):
        raise _input_error("producers must map edge_type -> callable")
    if not isinstance(generation_id, str) or not generation_id:
        raise _input_error(f"generation_id must be a non-empty string, got {generation_id!r}")
    if root_id not in state:
        raise _input_error(f"root object {root_id!r} is not in the supplied state")

    order = _subgraph_order(graph, root_id)
    if not order:
        return RebuildResult(generation_id=generation_id, outcomes=())

    # Refuse the whole plan before rebuilding anything: a partial rebuild whose
    # remaining edges have no producer would report success for an incomplete graph.
    missing = sorted({edge_type for _, _, edge_type in order if edge_type not in producers})
    if missing:
        blocked = [
            {"object_id": object_id, "edge_type": edge_type}
            for object_id, _, edge_type in order
            if edge_type not in producers
        ]
        raise ExtensionError(
            "E_NOT_IMPLEMENTED",
            "no producer is registered for " f"{missing}; refusing to rebuild a partial graph",
            detail={"missing_edge_types": missing, "blocked_objects": blocked},
        )

    working = copy.deepcopy(dict(state))
    outcomes: list[RebuildOutcome] = []
    executed: list[str] = []

    for object_id, parent_id, edge_type in order:
        producer = producers[edge_type]
        parent_payload = working.get(parent_id)
        if parent_payload is None:
            outcomes.append(
                RebuildOutcome(
                    object_id=object_id,
                    status=FAILED,
                    edge_type=edge_type,
                    parent_id=parent_id,
                    error={
                        "code": "PARENT_MISSING",
                        "message": f"parent {parent_id!r} has no payload to rebuild from",
                    },
                )
            )
            continue
        try:
            rebuilt = producer(parent_payload, object_id)
        except ExtensionError as error:
            outcomes.append(
                RebuildOutcome(
                    object_id=object_id,
                    status=FAILED,
                    edge_type=edge_type,
                    parent_id=parent_id,
                    error={"code": error.code, "message": str(error)},
                )
            )
            executed.append(object_id)
            continue
        except Exception as error:  # noqa: BLE001 - recorded, never swallowed silently
            outcomes.append(
                RebuildOutcome(
                    object_id=object_id,
                    status=FAILED,
                    edge_type=edge_type,
                    parent_id=parent_id,
                    error={"code": "PRODUCER_RAISED", "message": repr(error)},
                )
            )
            executed.append(object_id)
            continue

        working[object_id] = rebuilt
        digest = hashlib.sha256(
            json.dumps(rebuilt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        outcomes.append(
            RebuildOutcome(
                object_id=object_id,
                status=REBUILT,
                edge_type=edge_type,
                parent_id=parent_id,
                digest=digest,
            )
        )
        executed.append(object_id)

    rebuilt_payloads = {
        object_id: working[object_id] for object_id in executed if object_id in working
    }
    return RebuildResult(
        generation_id=generation_id,
        outcomes=tuple(outcomes),
        rebuilt=rebuilt_payloads,
        order=tuple(object_id for object_id, _, _ in order),
    )
