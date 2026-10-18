"""Turn one human correction into a new generation (closes ``L11-LIM-01``).

``recompute`` could rebuild a graph, but nothing called it: ``assemble_package``
runs the whole path once and there was no second entry that started from an
existing result. So "correct one cell and everything downstream follows" was a
capability, not a behaviour.

This module is that second entry. It fixes four things the naive version gets
wrong:

* **A correction without an attributable decision is not applied.** The value is
  not the only thing that changes — the artifact becomes one a reviewer vouched
  for, so ``review_decision_ref`` and ``reason`` are required.
* **The header band is never guessed.** Re-deriving a header path needs the
  declared header rows. When the caller does not supply them we refuse; falling
  back to "first row" or "bold row" is exactly what L05 forbids.
* **Only affected tables are rebuilt.** An unrelated table's payload and packet
  must stay byte-identical, otherwise "rebuilt" quietly becomes "regenerated
  everything", which is indistinguishable from a fresh run and hides the blast
  radius.
* **Notes are not silently re-bound.** Note anchoring needs the document-level
  candidate list, which an ``AssemblyResult`` does not carry. Rather than
  pretending, the result states ``notes_rebound=False`` so a note that *should*
  have moved is visibly still attached where it was.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .. import ExtensionError
from ..pipeline import AssemblyResult
from ..render.review_packet import ReviewPacket, build_review_packet
from ..tables.notes import NotesResult, bind_notes, link_cell_note_ids
from ..tables.semantics import derive_header_paths
from .dependencies import DependencyGraph, impact_of
from .recompute import RebuildResult, recompute

__all__ = [
    "Correction",
    "CorrectionOutcome",
    "apply_correction",
    "require_publishable",
]

SEMANTICS_EDGE = "CELL_TEXT"


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _review_required(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_REVIEW_REQUIRED", message, detail=detail or None)


@dataclass(frozen=True)
class Correction:
    """One reviewer-authorized data change."""

    object_id: str
    path: tuple[str, ...]
    value: Any
    reason: str
    review_decision_ref: str
    change_kind: str = SEMANTICS_EDGE

    def to_mapping(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "path": list(self.path),
            "value": self.value,
            "reason": self.reason,
            "review_decision_ref": self.review_decision_ref,
            "change_kind": self.change_kind,
        }


@dataclass(frozen=True)
class CorrectionOutcome:
    assembly: AssemblyResult
    rebuild: RebuildResult
    affected_tables: tuple[str, ...]
    notes_rebound: bool

    @property
    def publishable(self) -> bool:
        return self.rebuild.ok

    def to_mapping(self) -> dict[str, Any]:
        return {
            "generation_id": self.assembly.generation_id,
            "affected_tables": list(self.affected_tables),
            "notes_rebound": self.notes_rebound,
            "publishable": self.publishable,
            "rebuild": self.rebuild.stats(),
            "failures": [item.to_mapping() for item in self.rebuild.failed],
        }


def require_publishable(outcome: CorrectionOutcome) -> None:
    """Raise unless every affected object was actually rebuilt.

    Kept separate from ``apply_correction`` so a caller that only wants to *see*
    what would happen can, while the write path cannot forget the check.
    """
    if outcome.publishable:
        return
    raise _review_required(
        "the correction did not rebuild every affected object; publishing this "
        "generation would ship a document whose downstream is still pre-correction",
        failed=[item.to_mapping() for item in outcome.rebuild.failed],
        generation_id=outcome.assembly.generation_id,
    )


def _write_path(target: dict[str, Any], path: Sequence[str], value: Any) -> None:
    if not path:
        raise _input_error("correction path must name at least one field")
    node: Any = target
    for depth, segment in enumerate(path[:-1]):
        if not isinstance(node, Mapping) or segment not in node:
            raise _input_error(
                f"correction path {'.'.join(path)!r} does not exist at {segment!r}; "
                "refusing to create fields, because a typo would then look like data",
                depth=depth,
            )
        node = node[segment]
        if not isinstance(node, dict):
            raise _input_error(f"correction path {'.'.join(path)!r} crosses a non-object")
    last = path[-1]
    if not isinstance(node, dict) or last not in node:
        raise _input_error(
            f"correction path {'.'.join(path)!r} names unknown field {last!r}; "
            "existing fields only",
            available=sorted(node) if isinstance(node, dict) else None,
        )

    # A silently retyped field is worse than a rejected one: ``text = 150`` renders
    # (no quotes in JSON) and only fails at the contract boundary, where it looks
    # like a downstream bug. ``type() is not type()`` also keeps ``True`` out of an
    # int field, which ``isinstance`` would allow.
    current = node[last]
    if current is not None and type(current) is not type(value):
        raise _input_error(
            f"correction for {'.'.join(path)!r} changes the field type from "
            f"{type(current).__name__} to {type(value).__name__}; a retyped field is not "
            "a corrected value",
            path=list(path),
        )
    node[last] = value


def _find_table(tables: Sequence[Mapping[str, Any]], object_id: str) -> tuple[int, str | None]:
    """Return ``(table_index, cell_id or None)`` for a ``table:x`` or cell id."""
    if object_id.startswith("table:"):
        table_id = object_id.split(":", 1)[1]
        for index, table in enumerate(tables):
            if table.get("table_id") == table_id:
                return index, None
        raise _input_error(f"no table {table_id!r} in this assembly")

    for index, table in enumerate(tables):
        for cell in table.get("cells") or ():
            if cell.get("cell_id") == object_id:
                return index, object_id
    raise _input_error(
        f"no cell {object_id!r} in this assembly; pass 'table:<id>' to correct the table itself"
    )


def _note_candidates_for(
    table_index: int, table_id: str, candidates: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Which notes belong to this table.

    A note with no target cannot be attributed to a table. Dropping it would turn a
    missing footnote into an absent one, so it goes to the first table — and *only*
    the first: ``pipeline._note_candidates_for`` appends it to every table, which
    turns one orphan footnote into N notes and makes the same condition appear
    twice in different packets. That defect is registered as ``L13-FIND-01`` and
    fixed in ``pipeline`` next; here the count stays honest.
    """
    owned: list[dict[str, Any]] = []
    for candidate in candidates:
        targets = candidate.get("target_ids") or ()
        if not targets:
            if table_index == 0:
                owned.append(dict(candidate))
            continue
        if any(str(target).startswith(f"{table_id}-") for target in targets):
            owned.append(dict(candidate))
    return owned


def _evidence_candidates(table: Mapping[str, Any], notes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ids: list[str] = []
    for evidence_id in table.get("omission_evidence_ids") or ():
        if evidence_id not in ids:
            ids.append(str(evidence_id))
    for note in notes:
        for evidence_id in note.get("evidence_ids") or ():
            if evidence_id not in ids:
                ids.append(str(evidence_id))
    return [{"candidate_evidence_id": evidence_id} for evidence_id in ids]


def apply_correction(
    assembly: AssemblyResult,
    correction: Correction,
    *,
    generation_id: str,
    header_rows_by_table: Mapping[str, Sequence[int]],
    note_candidates: Sequence[Mapping[str, Any]] | None = None,
) -> CorrectionOutcome:
    """Apply ``correction`` and rebuild only what depends on it.

    ``note_candidates`` is the document-level list from ``read_notes(package).candidates()``.
    Passing it makes the correction *re-bind* notes; omitting it keeps the old notes
    and reports ``notes_rebound=False``. The list is an argument rather than
    something this function parses itself so the module stays a pure function over
    data it was given.
    """
    if not isinstance(assembly, AssemblyResult):
        raise _input_error(f"assembly must be an AssemblyResult, got {type(assembly).__name__}")
    if not isinstance(correction, Correction):
        raise _input_error(f"correction must be a Correction, got {type(correction).__name__}")
    if not isinstance(generation_id, str) or not generation_id:
        raise _input_error(f"generation_id must be a non-empty string, got {generation_id!r}")
    if generation_id == assembly.generation_id:
        raise _input_error(
            "a corrected assembly must carry a new generation_id; reusing the old one "
            "would overwrite the very artifact the correction supersedes"
        )
    if not correction.reason or not correction.reason.strip():
        raise _review_required("a correction needs a stated reason")
    if not correction.review_decision_ref or not correction.review_decision_ref.strip():
        raise _review_required(
            "a correction needs review_decision_ref: the artifact becomes one a "
            "reviewer vouched for, so the decision must be attributable"
        )
    impact_of(correction.change_kind)  # unknown kinds are refused, not ignored

    tables = copy.deepcopy(list(assembly.tables))
    table_index, cell_id = _find_table(tables, correction.object_id)
    table = tables[table_index]
    table_id = table["table_id"]

    if cell_id is None:
        _write_path(table, correction.path, correction.value)
    else:
        for cell in table["cells"]:
            if cell.get("cell_id") != cell_id:
                continue
            _write_path(cell, correction.path, correction.value)
            break
        else:  # pragma: no cover - _find_table already proved it exists
            raise _input_error(f"cell {cell_id!r} disappeared")

    rows = header_rows_by_table.get(table_id)
    if rows is None:
        raise _review_required(
            f"no header rows are declared for table {table_id!r}; re-deriving its header "
            "paths would require guessing them from formatting, which L05 forbids",
            table_id=table_id,
        )

    def produce_semantics(parent_payload: Mapping[str, Any], child_id: str) -> dict[str, Any]:
        rebuilt_table = child_id.split(":", 1)[1]
        rebuilt_rows = header_rows_by_table.get(rebuilt_table)
        if rebuilt_rows is None:
            raise _review_required(f"no header rows are declared for table {rebuilt_table!r}")
        result = derive_header_paths(
            parent_payload, header_rows=list(rebuilt_rows), context={}
        )
        return {
            "cell_semantics": result.cell_semantics,
            "issues": [issue.to_dict() for issue in result.issues],
        }

    graph = DependencyGraph()
    for candidate in tables:
        graph.add_edge(
            parent_id=f"table:{candidate['table_id']}",
            parent_version=assembly.generation_id,
            child_id=f"semantics:{candidate['table_id']}",
            child_version=assembly.generation_id,
            edge_type=correction.change_kind,
            producer="derive_header_paths",
        )

    state: dict[str, Any] = {}
    for candidate in tables:
        candidate_id = candidate["table_id"]
        state[f"table:{candidate_id}"] = candidate
        state[f"semantics:{candidate_id}"] = {
            "cell_semantics": candidate.get("cell_semantics") or [],
            "issues": [
                issue
                for issue in candidate.get("assembly_issues") or ()
                if issue.get("layer") == "semantics"
            ],
        }

    rebuild = recompute(
        graph,
        root_id=f"table:{table_id}",
        state=state,
        producers={correction.change_kind: produce_semantics},
        generation_id=generation_id,
    )

    rebuilt = rebuild.rebuilt.get(f"semantics:{table_id}") or {}
    cell_semantics = list(rebuilt.get("cell_semantics") or [])
    semantics_issues = list(rebuilt.get("issues") or [])

    previous_notes = [dict(note) for note in table.get("notes") or ()]
    notes_rebound = note_candidates is not None
    notes_issues: list[dict[str, Any]] = []
    if notes_rebound:
        owned = _note_candidates_for(table_index, table_id, note_candidates or ())
        notes_result: NotesResult = (
            bind_notes(table, owned) if owned else NotesResult(notes=[], issues=[])
        )
        table_notes = [dict(note) for note in notes_result.notes]
        notes_issues = [issue.to_dict() for issue in notes_result.issues]
    else:
        table_notes = previous_notes

    linked = link_cell_note_ids(cell_semantics, table_notes)

    kept_issues = [
        issue
        for issue in table.get("assembly_issues") or ()
        if issue.get("layer") not in ("semantics", "correction", "notes")
    ]
    table_issues = (
        kept_issues
        + [{"layer": "semantics", **issue} for issue in semantics_issues]
        + [{"layer": "notes", **issue} for issue in notes_issues]
    )
    if table_notes and not rebuild.ok:
        table_issues.append(
            {
                "layer": "correction",
                "code": "NOTES_NOT_REBOUND",
                "message": "note anchors were carried over unchanged; a correction that "
                "moves or removes a note marker must re-run notes extraction",
            }
        )
    if notes_rebound and len(table_notes) < len(previous_notes):
        # Re-binding legitimately drops notes whose anchor no longer exists, but the
        # drop must be visible: a footnote quietly disappearing from a financial table
        # is worse than one flagged as unresolved.
        table_issues.append(
            {
                "layer": "correction",
                "code": "NOTES_DROPPED_ON_REBIND",
                "message": "re-binding produced fewer notes than the table carried before",
                "before": [note.get("note_id") for note in previous_notes],
                "after": [note.get("note_id") for note in table_notes],
            }
        )

    table["cell_semantics"] = linked
    table["notes"] = table_notes
    table["assembly_issues"] = table_issues

    packet: ReviewPacket = build_review_packet(
        table,
        semantics=linked,
        notes=table_notes,
        evidence_candidates=_evidence_candidates(table, table_notes),
        blocked=table_issues,
        context={},
    )

    packets = list(assembly.packets)
    if packets and len(packets) == len(assembly.tables):
        packets[table_index] = packet
    else:  # pragma: no cover - assemble_package always emits one packet per table
        packets = [*packets, packet]

    # Flatten in table order, exactly as ``assemble_package`` does, so a consumer
    # that compares ``notes`` between generations sees the same ordering rule.
    #
    # Duplicates are collapsed **and reported**: ``pipeline`` currently copies an
    # unattached footnote into every table, so one footnote arrives here twice with
    # the same ``note_id``. Counting it twice would say the document has two
    # conditions. Dropping it silently would hide the upstream defect, so the
    # document-level list is de-duplicated while each table's own list is left
    # byte-identical, and the collapse is recorded in ``issues``.
    flat_notes: list[dict[str, Any]] = []
    seen_note_ids: set[str] = set()
    duplicate_ids: list[str] = []
    for candidate in tables:
        for note in candidate.get("notes") or ():
            note_id = str(note.get("note_id"))
            if note_id in seen_note_ids:
                if note_id not in duplicate_ids:
                    duplicate_ids.append(note_id)
                continue
            seen_note_ids.add(note_id)
            flat_notes.append(dict(note))

    issues = list(assembly.issues)
    for note_id in duplicate_ids:
        issues.append(
            {
                "layer": "correction",
                "code": "DUPLICATE_NOTE_ID_DEDUPED",
                "message": "the same note was bound to more than one table; the "
                "document-level list keeps one entry per note id",
                "note_id": note_id,
            }
        )

    stats = dict(assembly.stats)
    stats.update(
        {
            "generation_id": generation_id,
            "corrections": [
                *list(stats.get("corrections") or ()),
                {**correction.to_mapping(), "affects": [table_id]},
            ],
            "rebuild": rebuild.stats(),
            "notes_rebound": notes_rebound,
            "notes": len(flat_notes),
            "notes_publishable": sum(1 for note in flat_notes if note["state"] != "NEEDS_REVIEW"),
        }
    )

    corrected = AssemblyResult(
        generation_id=generation_id,
        source_sha256=assembly.source_sha256,
        tables=tables,
        notes=flat_notes,
        packets=packets,
        issues=issues,
        stats=stats,
    )
    return CorrectionOutcome(
        assembly=corrected,
        rebuild=rebuild,
        affected_tables=(table_id,),
        notes_rebound=notes_rebound,
    )
