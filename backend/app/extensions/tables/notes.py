"""Footnote / table-external condition binding (SDD 9.3, ``PI-F04-08``).

Contract sentences this module implements:

    "原生脚注/版面备注绑定到明确 scope，歧义阻断条件发布"
    "scope=TABLE/COLUMN/ROW/CELL 必须指明 target_ids；不能确定则 UNRESOLVED、NEEDS_REVIEW"
    "同页两表都出现 * 时，先根据显式引用/表边界匹配；不把最近文本自动套全表"

Three rules follow, and all three are enforced rather than advised:

1. **Text is preserved verbatim.** A footnote's wording is the exception clause;
   normalising or "cleaning" it changes the condition. Only whitespace around
   the whole string is stripped.
2. **A scope without targets is not a scope.** A caller that declares ``CELL``
   but supplies no ids gets ``UNRESOLVED`` + ``NEEDS_REVIEW`` — the note is kept
   (it is still source evidence) but it cannot be published as a condition.
3. **Layout adjacency can never widen to the whole table.** A note bound by page
   proximity (``PADDLE_LAYOUT_BLOCK``) may attach to a specific cell/row/column
   with an explicit anchor; asking it to cover ``TABLE`` is refused and demoted,
   because "nearest text applies to everything" is exactly the inference SDD
   forbids.

Conflicting text for the same target+marker is not resolved by picking one: both
notes become ``NEEDS_REVIEW`` so a human decides which clause is the real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError

NOTES_CODE = "E_TABLE_NOTES_INVALID"

SCOPES = ("TABLE", "COLUMN", "ROW", "CELL", "UNRESOLVED")

#: Lower binds first. DOCX native references outrank MinerU's table_footnote,
#: which outranks a Paddle layout-block adjacency guess (SDD 9.3 order).
EVIDENCE_PRIORITY = {
    "DOCX_FOOTNOTE": 0,
    "DOCX_ENDNOTE": 0,
    "MINERU_TABLE_FOOTNOTE": 1,
    "PADDLE_LAYOUT_BLOCK": 2,
}

#: Kinds that may legitimately claim whole-table scope without an anchor.
_TABLE_SCOPE_KINDS = frozenset({"DOCX_FOOTNOTE", "DOCX_ENDNOTE", "MINERU_TABLE_FOOTNOTE"})

__all__ = [
    "NOTES_CODE",
    "SCOPES",
    "EVIDENCE_PRIORITY",
    "NoteIssue",
    "NotesResult",
    "bind_notes",
    "link_cell_note_ids",
]


def _error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError(NOTES_CODE, message, detail=detail or None)


@dataclass(frozen=True)
class NoteIssue:
    code: str
    message: str
    note_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.note_id is not None:
            payload["note_id"] = self.note_id
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload


@dataclass(frozen=True)
class NotesResult:
    notes: list[dict[str, Any]]
    issues: list[NoteIssue]
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def publishable(self) -> list[dict[str, Any]]:
        """Notes that may enter a published condition package.

        ``NEEDS_REVIEW`` is excluded by design: SDD 9.3 says a field whose
        exception scope is unknown must not be released.
        """
        return [note for note in self.notes if note["state"] != "NEEDS_REVIEW"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "notes": [dict(note) for note in self.notes],
            "issues": [issue.to_dict() for issue in self.issues],
            "context": dict(self.context),
        }


def _cell_ids(table: Mapping[str, Any]) -> set[str]:
    if not isinstance(table, Mapping):
        raise _error(f"table must be a mapping, got {type(table).__name__}")
    table_id = table.get("table_id")
    if not isinstance(table_id, str) or not table_id:
        raise _error(f"table has no usable table_id: {table_id!r}")
    cells = table.get("cells")
    if isinstance(cells, (str, bytes)) or not isinstance(cells, Sequence):
        raise _error("table.cells must be a sequence of cells")
    ids: set[str] = set()
    for index, cell in enumerate(cells):
        if not isinstance(cell, Mapping):
            raise _error(f"table.cells[{index}] must be a mapping")
        cell_id = cell.get("cell_id")
        if not isinstance(cell_id, str) or not cell_id:
            raise _error(f"table.cells[{index}] has no usable cell_id")
        ids.add(cell_id)
    return ids


def _string_sequence(value: Any, *, field_name: str, note_id: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(
            f"note {note_id}: {field_name} must be a sequence of ids, not "
            f"{type(value).__name__}"
        )
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _error(f"note {note_id}: {field_name} contains {item!r}")
        items.append(item)
    return items


def bind_notes(
    table: Mapping[str, Any],
    notes: Sequence[Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
) -> NotesResult:
    """Bind each candidate footnote to an explicit scope of ``table``.

    Each input note accepts: ``note_id``, ``text``, ``marker``, ``scope``,
    ``target_ids``, ``evidence_ids``, ``evidence_kind`` and
    ``review_decision_ref``. Output notes are exactly the closed ``Note``
    payload — ``{note_id, text, scope, target_ids, evidence_ids, state,
    review_decision_ref}`` — so they can be dropped straight into a
    ``DocumentEnhancement`` without a translation step.
    """
    table_id = table.get("table_id") if isinstance(table, Mapping) else None
    if not isinstance(table_id, str) or not table_id:
        raise _error(f"table has no usable table_id: {table_id!r}")
    known_cells = _cell_ids(table)

    if isinstance(notes, (str, bytes)) or not isinstance(notes, Sequence):
        raise _error(
            f"notes must be a sequence of mappings, got {type(notes).__name__}"
        )

    issues: list[NoteIssue] = []
    bound: list[dict[str, Any]] = []
    #: (frozenset(targets), marker) → note ids, used to detect contradictory text
    claims: dict[tuple[frozenset[str], str], list[dict[str, Any]]] = {}
    seen_ids: set[str] = set()

    for index, raw in enumerate(notes):
        if not isinstance(raw, Mapping):
            raise _error(f"notes[{index}] must be a mapping, got {type(raw).__name__}")

        note_id = raw.get("note_id")
        if not isinstance(note_id, str) or not note_id.strip():
            raise _error(f"notes[{index}] has no usable note_id: {note_id!r}")
        if note_id in seen_ids:
            raise _error(f"duplicate note_id {note_id!r}")
        seen_ids.add(note_id)

        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _error(f"note {note_id}: text must be non-empty")
        # Verbatim apart from the outer whitespace; internal spacing is the clause.
        preserved = text.strip()

        marker = raw.get("marker")
        if marker is not None and not isinstance(marker, str):
            raise _error(f"note {note_id}: marker must be a string or null")
        marker_key = (marker or "").strip()

        scope = raw.get("scope", "UNRESOLVED")
        if scope not in SCOPES:
            raise _error(
                f"note {note_id}: scope must be one of {list(SCOPES)}, got {scope!r}"
            )

        targets = _string_sequence(raw.get("target_ids"), field_name="target_ids", note_id=note_id)
        evidence = _string_sequence(
            raw.get("evidence_ids"), field_name="evidence_ids", note_id=note_id
        )

        kind = raw.get("evidence_kind")
        if kind is not None and kind not in EVIDENCE_PRIORITY:
            raise _error(
                f"note {note_id}: evidence_kind must be one of "
                f"{sorted(EVIDENCE_PRIORITY)} or null, got {kind!r}"
            )

        ref = raw.get("review_decision_ref")
        if ref is not None and (not isinstance(ref, str) or not ref.strip()):
            raise _error(f"note {note_id}: review_decision_ref must be a non-empty string")

        state = "CANDIDATE"

        if not evidence:
            state = "NEEDS_REVIEW"
            issues.append(
                NoteIssue(
                    code="MISSING_EVIDENCE",
                    message=(
                        "a note without source evidence cannot be published; the clause "
                        "would be unverifiable"
                    ),
                    note_id=note_id,
                )
            )

        if scope != "UNRESOLVED" and not targets:
            scope = "UNRESOLVED"
            state = "NEEDS_REVIEW"
            issues.append(
                NoteIssue(
                    code="SCOPE_WITHOUT_TARGETS",
                    message=(
                        f"scope {raw.get('scope')!r} was declared without target_ids; "
                        "an unknown scope must be reported, not implied"
                    ),
                    note_id=note_id,
                )
            )

        if scope == "TABLE" and kind not in _TABLE_SCOPE_KINDS:
            scope = "UNRESOLVED"
            state = "NEEDS_REVIEW"
            issues.append(
                NoteIssue(
                    code="ADJACENCY_MAY_NOT_SCOPE_TABLE",
                    message=(
                        f"evidence kind {kind!r} binds by proximity; it may not widen to "
                        "the whole table without an explicit reference"
                    ),
                    note_id=note_id,
                )
            )

        dangling = [tid for tid in targets if tid != table_id and tid not in known_cells]
        if dangling:
            state = "NEEDS_REVIEW"
            issues.append(
                NoteIssue(
                    code="DANGLING_TARGET",
                    message="target id is neither this table nor one of its cells",
                    note_id=note_id,
                    detail={"dangling": dangling},
                )
            )

        if scope == "UNRESOLVED":
            state = "NEEDS_REVIEW"

        if ref is not None and state == "NEEDS_REVIEW":
            # A review decision cannot rescue an unresolved scope; SDD 9.3 keeps
            # unknown exception scope out of published conditions regardless.
            issues.append(
                NoteIssue(
                    code="REVIEW_REF_IGNORED",
                    message="review_decision_ref was supplied but the note is unresolved",
                    note_id=note_id,
                )
            )
            ref = None

        note = {
            "note_id": note_id,
            "text": preserved,
            "scope": scope,
            "target_ids": targets,
            "evidence_ids": evidence,
            "state": "CONFIRMED" if ref else state,
            "review_decision_ref": ref,
        }
        bound.append(note)
        if targets:
            claims.setdefault((frozenset(targets), marker_key), []).append(note)

    # Two notes claiming the same targets with the same marker but different text:
    # SDD forbids choosing by proximity, so both become NEEDS_REVIEW.
    for (targets, marker_key), group in claims.items():
        if len(group) < 2:
            continue
        texts = {note["text"] for note in group}
        if len(texts) < 2:
            continue
        ids = sorted(note["note_id"] for note in group)
        issues.append(
            NoteIssue(
                code="CONFLICTING_TEXT",
                message=(
                    "the same target and marker carry different clause text; neither may "
                    "be published until a review decision picks one"
                ),
                detail={"note_ids": ids, "targets": sorted(targets), "marker": marker_key},
            )
        )
        for note in group:
            note["state"] = "NEEDS_REVIEW"
            note["review_decision_ref"] = None

    return NotesResult(notes=bound, issues=issues, context=dict(context or {}))


def link_cell_note_ids(
    cell_semantics: Sequence[Mapping[str, Any]],
    notes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Copy CELL-scoped note ids onto ``CellSemantics.note_ids``.

    The root contract requires ``note_ids`` on every cell semantic to be a subset
    of the table's notes, so a dangling reference here is a hard error rather
    than something to filter out silently.
    """
    by_target: dict[str, list[str]] = {}
    known: set[str] = set()
    for note in notes:
        if not isinstance(note, Mapping):
            raise _error("notes entries must be mappings")
        note_id = note.get("note_id")
        if not isinstance(note_id, str) or not note_id:
            raise _error(f"note has no usable note_id: {note_id!r}")
        known.add(note_id)
        if note.get("scope") != "CELL":
            continue
        if note.get("state") == "NEEDS_REVIEW":
            continue
        for target in note.get("target_ids") or ():
            by_target.setdefault(target, []).append(note_id)

    linked: list[dict[str, Any]] = []
    for item in cell_semantics:
        if not isinstance(item, Mapping):
            raise _error("cell_semantics entries must be mappings")
        cell_id = item.get("data_cell_id")
        if not isinstance(cell_id, str) or not cell_id:
            raise _error(f"cell_semantics entry has no data_cell_id: {cell_id!r}")
        updated = dict(item)
        existing = list(item.get("note_ids") or ())
        for note_id in existing:
            if note_id not in known:
                raise _error(
                    f"cell {cell_id} references unknown note {note_id!r}"
                )
        merged = existing + [nid for nid in by_target.get(cell_id, []) if nid not in existing]
        updated["note_ids"] = merged
        linked.append(updated)
    return linked
