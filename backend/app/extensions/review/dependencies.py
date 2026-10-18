"""Processing DAG and impact fan-out (SDD 12.2, ``PI-F04-09``).

The distinction that matters:

* a **business** flow chart may contain cycles (that is ``vision.flow``'s problem);
* a **processing** dependency graph must not — a cycle here means "to rebuild A you
  must first rebuild A", which is never a legitimate schedule.

:func:`add_edge` therefore rejects cycles outright, unlike the flow module which
only reports them.

The impact table is copied from SDD 12.2 rather than invented here, because the
cost of missing one edge (forgetting that a header change invalidates every column
path under it) is a silently stale answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .. import ExtensionError

#: SDD 12.2 table, verbatim in intent: change kind → object kinds that must be rebuilt.
IMPACT_TABLE: dict[str, tuple[str, ...]] = {
    "HEADER_CELL": (
        "COLUMN_PATH",
        "CELL_SEMANTICS",
        "DERIVED_TEXT",
        "EXTERNAL_INDEX",
    ),
    "CELL_TEXT": ("CELL_SEMANTICS", "CANDIDATE_EVIDENCE", "CHART_POINT"),
    "NOTE_SCOPE": ("CONTEXT", "DERIVED_KNOWLEDGE_CANDIDATE"),
    "CONTINUATION": ("COMBINED_VIEW", "CROSS_PAGE_CONTEXT"),
    "FLOW_EDGE": ("FLOW_GRAPH", "TEXT_IMAGE_CONSISTENCY", "STEP_CANDIDATE"),
    "CHART_AXIS_UNIT": ("CHART_POINT", "BUSINESS_CANDIDATE"),
    "SOURCE_REVOKED": ("CANDIDATE_EVIDENCE", "CACHE", "EXPORT"),
}

__all__ = [
    "IMPACT_TABLE",
    "DependencyGraph",
    "impact_of",
    "reverse_patch",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def impact_of(change_kind: str) -> tuple[str, ...]:
    """Affected object kinds for one change; unknown kinds are refused, not ignored."""
    if change_kind not in IMPACT_TABLE:
        raise _input_error(
            f"unknown change kind {change_kind!r}; an unlisted change would silently "
            f"invalidate nothing. Known: {sorted(IMPACT_TABLE)}"
        )
    return IMPACT_TABLE[change_kind]


@dataclass
class DependencyGraph:
    """``dependencies.json`` as code: parent → child edges with producer metadata."""

    edges: list[dict[str, Any]] = field(default_factory=list)

    def add_edge(
        self,
        *,
        parent_id: str,
        parent_version: str,
        child_id: str,
        child_version: str,
        edge_type: str,
        producer: str,
    ) -> None:
        for name, value in (
            ("parent_id", parent_id),
            ("parent_version", parent_version),
            ("child_id", child_id),
            ("child_version", child_version),
            ("edge_type", edge_type),
            ("producer", producer),
        ):
            if not isinstance(value, str) or not value:
                raise _input_error(f"{name} must be a non-empty string, got {value!r}")
        if parent_id == child_id:
            raise _input_error(
                f"edge {parent_id} -> {child_id} is a self dependency; a rebuild cannot "
                "require itself"
            )
        if self._reaches(child_id, parent_id):
            raise _input_error(
                f"edge {parent_id} -> {child_id} would close a cycle in the processing DAG; "
                "rebuild order would be undefined",
                parent_id=parent_id,
                child_id=child_id,
            )
        self.edges.append(
            {
                "parent_id": parent_id,
                "parent_version": parent_version,
                "child_id": child_id,
                "child_version": child_version,
                "edge_type": edge_type,
                "producer": producer,
            }
        )

    def _children(self) -> dict[str, list[str]]:
        children: dict[str, list[str]] = {}
        for edge in self.edges:
            children.setdefault(edge["parent_id"], []).append(edge["child_id"])
        return children

    def _reaches(self, start: str, target: str) -> bool:
        children = self._children()
        stack = [start]
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(children.get(node, ()))
        return False

    def descendants(self, parent_id: str) -> list[str]:
        """Transitive children of ``parent_id`` in a stable order (fan-out)."""
        if not isinstance(parent_id, str) or not parent_id:
            raise _input_error(f"parent_id must be a non-empty string, got {parent_id!r}")
        children = self._children()
        out: list[str] = []
        seen: set[str] = set()
        stack = [parent_id]
        while stack:
            node = stack.pop()
            for child in children.get(node, ()):
                if child in seen:
                    continue
                seen.add(child)
                out.append(child)
                stack.append(child)
        return sorted(out)

    def impact(self, parent_id: str) -> dict[str, Any]:
        """Descendants plus the object kinds implied by the edges' ``edge_type``."""
        affected = self.descendants(parent_id)
        kinds: set[str] = set()
        for edge in self.edges:
            if edge["child_id"] in affected:
                kinds.update(impact_of(edge["edge_type"]))
        return {
            "parent_id": parent_id,
            "affected_object_ids": affected,
            "affected_kinds": sorted(kinds),
        }


def reverse_patch(
    current: Mapping[str, Any],
    changed_paths: Sequence[str],
    *,
    previous_values: Mapping[str, Any],
    reason: str = "reverse of an earlier patch",
) -> list[dict[str, Any]]:
    """Build a new patch against the *current* version that restores old values.

    SDD 12.2: "撤销补丁用针对当前版本的新反向补丁，保留历史；禁止删除旧patch或改原件来
    回滚." — so this returns operations, never an edit of history.
    """
    if not isinstance(current, Mapping):
        raise _input_error("current document must be a mapping")
    if not isinstance(reason, str) or not reason.strip():
        raise _input_error("a reverse patch must carry a reason")
    ops: list[dict[str, Any]] = []
    for path in changed_paths:
        if not isinstance(path, str) or not path.startswith("/"):
            raise _input_error(f"changed path must be a JSON Pointer, got {path!r}")
        if path not in previous_values:
            raise _input_error(
                f"no previous value recorded for {path}; a reverse patch cannot invent one"
            )
        value = _resolve(current, path)
        ops.append({"op": "test", "path": path, "value": value})
        ops.append({"op": "replace", "path": path, "value": previous_values[path]})
    if not ops:
        raise _input_error("a reverse patch needs at least one path")
    return ops



def _resolve(document: Any, path: str) -> Any:
    node = document
    for token in path.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(key)]
        elif isinstance(node, Mapping):
            node = node[key]
        else:  # pragma: no cover - defensive
            raise _input_error(f"path {path} does not resolve")
    return node
