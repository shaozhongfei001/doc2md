"""Producer side of ``L05-GAP-03``: footnote / endnote text and its reference.

Before this module, ``tables.notes.bind_notes`` could only bind notes that some
caller had already extracted; real Word footnotes never reached it. Now they do,
with three deliberate limits:

1. **Text is preserved verbatim** (only the outer whitespace is trimmed). A
   footnote is the exception clause; rewriting it changes the condition.
2. **A reference is located, not guessed.** The anchor comes from walking the
   cell that actually contains ``w:footnoteReference``; a footnote with no
   reference in the body is emitted with ``scope="UNRESOLVED"`` and empty
   ``target_ids`` so ``bind_notes`` marks it ``NEEDS_REVIEW`` instead of attaching
   it to the nearest table.
3. **Separator footnotes are skipped.** Word stores ``w:type="separator"`` /
   ``"continuationSeparator"`` entries (ids -1/0) that are rendering artefacts,
   not content.

Cell ids follow the same deterministic scheme as ``native_tables``
(``TBL-0001-R0000C0000``) so the output drops straight into ``bind_notes`` without
a translation step; the table walk is the shared one, so the numbering cannot
drift from ``extract_tables``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from .. import ExtensionError
from .final_view import _localname, visible_text
from .native_tables import _cells_in, _cell_span, _first, _rows_in, _top_level_tables, _wattr
from .package import DocxPackage, parse_xml_part

MAIN_PART = "word/document.xml"
FOOTNOTE_PART = "word/footnotes.xml"
ENDNOTE_PART = "word/endnotes.xml"

#: Word's rendering artefacts, never content.
SKIP_TYPES = frozenset({"separator", "continuationSeparator", "continuationNotice"})

__all__ = [
    "ENDNOTE_PART",
    "FOOTNOTE_PART",
    "ExtractedNote",
    "NotesExtraction",
    "read_notes",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class ExtractedNote:
    note_id: str
    text: str
    marker: str
    kind: str  # FOOTNOTE | ENDNOTE
    reference_id: str | None
    target_cell_id: str | None
    anchor: tuple[int, int, int] | None
    evidence_ids: tuple[str, ...]

    def to_candidate(self) -> dict[str, Any]:
        """Shape accepted by ``tables.notes.bind_notes``."""
        if self.target_cell_id is None:
            return {
                "note_id": self.note_id,
                "text": self.text,
                "marker": self.marker,
                "scope": "UNRESOLVED",
                "target_ids": [],
                "evidence_ids": list(self.evidence_ids),
                "evidence_kind": "DOCX_FOOTNOTE",
            }
        return {
            "note_id": self.note_id,
            "text": self.text,
            "marker": self.marker,
            "scope": "CELL",
            "target_ids": [self.target_cell_id],
            "evidence_ids": list(self.evidence_ids),
            "evidence_kind": "DOCX_FOOTNOTE",
        }


@dataclass(frozen=True)
class NotesExtraction:
    notes: list[ExtractedNote] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)

    def candidates(self) -> list[dict[str, Any]]:
        return [note.to_candidate() for note in self.notes]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "notes": [
                {
                    "note_id": note.note_id,
                    "text": note.text,
                    "marker": note.marker,
                    "kind": note.kind,
                    "reference_id": note.reference_id,
                    "target_cell_id": note.target_cell_id,
                    "anchor": None if note.anchor is None else list(note.anchor),
                    "evidence_ids": list(note.evidence_ids),
                }
                for note in self.notes
            ],
            "issues": [dict(issue) for issue in self.issues],
        }


def _note_texts(package: DocxPackage, part: str, kind: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """``ref id -> text`` for one notes part, plus issues for empty entries."""
    if not package.has_part(part):
        return {}, []
    root = parse_xml_part(package, part)
    texts: dict[str, str] = {}
    issues: list[dict[str, Any]] = []
    for node in root.iter():
        name = _localname(node)
        if name not in ("footnote", "endnote"):
            continue
        note_type = _wattr(node, "type")
        if note_type in SKIP_TYPES:
            continue
        ref_id = _wattr(node, "id")
        if not ref_id:
            continue
        text = visible_text(node).strip()
        if not text:
            issues.append(
                {
                    "code": "EMPTY_NOTE",
                    "part": part,
                    "note_ref_id": ref_id,
                    "kind": kind,
                    "message": "note carries no visible text; kept out of candidates",
                }
            )
            continue
        texts[ref_id] = text
    return texts, issues


def _table_cells(body: Any) -> Iterator[tuple[int, int, int, str, Any]]:
    """Yield ``(table_index, row, col, cell_id, cell_element)`` in document order.

    Only top-level tables are walked: a footnote inside a nested table would need
    the nested table's own id, and ``native_tables`` assigns those in a different
    sequence. Reporting the anchor without a cell id is safer than guessing one.
    """
    for table_index, table in enumerate(_top_level_tables(body)):
        for row_index, row in enumerate(_rows_in(table)):
            column = 0
            for cell_index, cell in enumerate(_cells_in(row)):
                span = _cell_span(cell, table_id=f"TBL-{table_index + 1:04d}", row=row_index, index=cell_index)
                cell_id = f"TBL-{table_index + 1:04d}-R{row_index:04d}C{column:04d}"
                yield table_index, row_index, column, cell_id, cell
                column += span


def read_notes(package: DocxPackage) -> NotesExtraction:
    """Extract footnote/endnote text and bind each to the cell that references it."""
    if not isinstance(package, DocxPackage):
        raise _input_error(f"package must be a DocxPackage, got {type(package).__name__}")

    footnotes, footnote_issues = _note_texts(package, FOOTNOTE_PART, "FOOTNOTE")
    endnotes, endnote_issues = _note_texts(package, ENDNOTE_PART, "ENDNOTE")
    issues = footnote_issues + endnote_issues

    document = parse_xml_part(package, MAIN_PART)
    body = _first(document, "body")
    if body is None:
        raise _input_error("docx main part has no w:body", part=MAIN_PART)

    # reference id -> list of (kind, anchor, cell_id); a footnote referenced from
    # two places is reported twice rather than merged, because the two anchors are
    # two different facts.
    referenced: dict[tuple[str, str], list[tuple[tuple[int, int, int], str]]] = {}
    for table_index, row, column, cell_id, cell in _table_cells(body):
        for node in cell.iter():
            name = _localname(node)
            if name not in ("footnoteReference", "endnoteReference"):
                continue
            ref_id = _wattr(node, "id")
            if not ref_id:
                continue
            kind = "FOOTNOTE" if name == "footnoteReference" else "ENDNOTE"
            referenced.setdefault((kind, ref_id), []).append(
                ((table_index, row, column), cell_id)
            )

    notes: list[ExtractedNote] = []
    for kind, texts in (("FOOTNOTE", footnotes), ("ENDNOTE", endnotes)):
        for ref_id, text in texts.items():
            anchors = referenced.get((kind, ref_id), [])
            if not anchors:
                notes.append(
                    ExtractedNote(
                        note_id=f"{kind[0]}N-{ref_id}",
                        text=text,
                        marker=ref_id,
                        kind=kind,
                        reference_id=None,
                        target_cell_id=None,
                        anchor=None,
                        evidence_ids=(f"docx:{kind.lower()}:{ref_id}",),
                    )
                )
                issues.append(
                    {
                        "code": "NOTE_WITHOUT_REFERENCE",
                        "kind": kind,
                        "note_ref_id": ref_id,
                        "message": (
                            "the note text exists but the body has no reference to it; the "
                            "target stays unresolved instead of being attached to a table"
                        ),
                    }
                )
                continue
            for index, (anchor, cell_id) in enumerate(anchors):
                notes.append(
                    ExtractedNote(
                        note_id=f"{kind[0]}N-{ref_id}" if index == 0 else f"{kind[0]}N-{ref_id}-{index}",
                        text=text,
                        marker=ref_id,
                        kind=kind,
                        reference_id=ref_id,
                        target_cell_id=cell_id,
                        anchor=anchor,
                        evidence_ids=(
                            f"docx:{kind.lower()}:{ref_id}",
                            f"docx:{kind.lower()}Reference:{cell_id}",
                        ),
                    )
                )

    for (kind, ref_id), anchors in referenced.items():
        texts = footnotes if kind == "FOOTNOTE" else endnotes
        if ref_id in texts:
            continue
        issues.append(
            {
                "code": "REFERENCE_WITHOUT_NOTE",
                "kind": kind,
                "note_ref_id": ref_id,
                "message": "the body references a note whose text is missing from the package",
                "anchors": [{"cell_id": cell_id} for _, cell_id in anchors],
            }
        )

    return NotesExtraction(notes=notes, issues=issues)
