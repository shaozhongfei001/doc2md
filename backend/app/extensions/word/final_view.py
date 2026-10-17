"""The FINAL view of a Word document, plus the review trail kept *beside* it.

SDD §7 step 3: what a reader sees after accepting all tracked changes
(``w:ins`` / ``w:moveTo``) and discarding the deleted ones (``w:del`` /
``w:moveFrom``). The discarded text is not thrown away: it moves to
``review_context`` so a reviewer can still inspect it, but it never leaks into
``paragraphs`` — a deleted clause must not be quoted as if it were still in the
document.

Two distinctions this module refuses to blur:

* **text revisions vs structural revisions.** Text revisions have a decidable
  final state, so they are resolved in place. ``w:cellMerge``, ``w:cellDel``,
  ``w:trPr/w:del`` and property-change histories (``*PrChange``) do not: the
  final *shape* depends on Word's own merge bookkeeping. Those are reported in
  ``unresolved_structural`` with a reproducible XML path instead of being
  guessed;
* **document evidence vs review context.** ``review_context`` entries are not
  effective evidence for a FINAL claim (the contract enforces
  ``DocxLocator.view == "FINAL"`` for evidence), which is exactly why deleted
  text is stored here and not in ``paragraphs``.

Field instructions are never interpreted: ``w:instrText`` is skipped, only the
field *display result* (``w:t``) is read. Macros, OLE objects and external
relationships are never expanded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from app.extensions import ExtensionError
from app.extensions.word.package import DocxPackage, parse_xml_part

__all__ = [
    "FinalView",
    "build_final_view",
    "visible_text",
    "element_path",
    "structural_revision_notes",
]

MAIN_PART = "word/document.xml"
REVISION_PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
COMMENTS_PART = "word/comments.xml"

#: Revision wrappers whose text is *not* in the final document.
DELETE_REVISIONS = frozenset({"del", "moveFrom"})
#: Revision wrappers whose text *is* in the final document.
INSERT_REVISIONS = frozenset({"ins", "moveTo"})

#: Structural revisions with an undecidable final shape (SDD §7, error table).
STRUCTURAL_REVISIONS: tuple[tuple[str, str], ...] = (
    ("tcPr", "cellMerge"),
    ("trPr", "cellDel"),
    ("trPr", "del"),
    ("tblPr", "tblPrChange"),
    ("trPr", "trPrChange"),
    ("tcPr", "tcPrChange"),
    ("pPr", "pPrChange"),
)

#: Subtrees that contribute no *text* (and must not be expanded).
_TEXT_SKIP = frozenset(
    {
        "tbl",
        "drawing",
        "pict",
        "object",
        "sym",
        "fldChar",
        "sectPr",
        "instrText",
        "delInstrText",
        "delText",
        "footnoteReference",
        "endnoteReference",
        "commentReference",
    }
)


# --------------------------------------------------------------------- helpers
def _localname(element: Any) -> str:
    tag = getattr(element, "tag", None)
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _children(element: Any, name: str) -> list[Any]:
    return [child for child in element if _localname(child) == name]


def _first(element: Any, name: str) -> Any | None:
    for child in element:
        if _localname(child) == name:
            return child
    return None


def _wattr(element: Any, name: str) -> str | None:
    for key, value in element.attrib.items():
        if key.rsplit("}", 1)[-1] == name:
            return value
    return None


def element_path(element: Any) -> str:
    """A stable, reproducible path such as ``/w:body/w:p[2]/w:del[1]``."""
    segments: list[str] = []
    node = element
    while node is not None:
        name = _localname(node)
        parent = node.getparent()
        if not name:
            break
        if parent is None:
            segments.append(f"w:{name}")
            break
        same_tag = [child for child in parent if child.tag == node.tag]
        position = same_tag.index(node) + 1 if node in same_tag else 1
        segments.append(f"w:{name}[{position}]")
        node = parent
    return "/" + "/".join(reversed(segments))


def _walk_text(element: Any, *, excluded: bool, out: list[str]) -> None:
    for child in element:
        name = _localname(child)
        if name in DELETE_REVISIONS:
            _walk_text(child, excluded=True, out=out)
            continue
        if name in _TEXT_SKIP:
            continue
        if name == "t":
            if not excluded:
                out.append(child.text or "")
            continue
        if name == "tab":
            if not excluded:
                out.append("\t")
            continue
        if name in ("br", "cr"):
            if not excluded:
                out.append("\n")
            continue
        if name == "noBreakHyphen":
            if not excluded:
                out.append("-")
            continue
        if name == "p":
            _walk_text(child, excluded=excluded, out=out)
            if not excluded:
                out.append("\n")
            continue
        _walk_text(child, excluded=excluded, out=out)


def visible_text(element: Any) -> str:
    """FINAL-visible text of a paragraph, cell or run container.

    ``w:tab`` becomes ``\\t``, ``w:br``/``w:cr`` become ``\\n``; deleted and
    moved-away text is dropped; field instructions are not interpreted; nested
    ``w:tbl`` subtrees are skipped so a child table is never flattened into its
    parent cell's text (SDD §7 step 6).
    """
    out: list[str] = []
    _walk_text(element, excluded=False, out=out)
    return "".join(out)


def _deleted_text(element: Any) -> str:
    """Text carried by ``w:delText`` (and, defensively, ``w:t``) inside a revision."""
    parts: list[str] = []
    for node in element.iter():
        name = _localname(node)
        if name in ("delText", "t"):
            parts.append(node.text or "")
        elif name == "tab":
            parts.append("\t")
        elif name in ("br", "cr"):
            parts.append("\n")
    return "".join(parts)


def _iter_body_paragraphs(element: Any) -> Iterator[Any]:
    """Body paragraphs in document order, skipping paragraphs inside tables."""
    for child in element:
        name = _localname(child)
        if name == "p":
            yield child
        elif name == "tbl":
            continue
        else:
            yield from _iter_body_paragraphs(child)


def _iter_tables(element: Any) -> Iterator[Any]:
    for child in element:
        if _localname(child) == "tbl":
            yield child
            # A table inside a cell is its own table, not part of the parent.
            yield from _iter_tables(child)
        else:
            yield from _iter_tables(child)


def _collect_revisions(
    root: Any,
    part: str,
    deletions: list[dict[str, Any]],
    insertions: list[dict[str, Any]],
    moves: list[dict[str, Any]],
) -> None:
    for node in root.iter():
        name = _localname(node)
        if name == "del":
            deletions.append(
                {
                    "part": part,
                    "element_path": element_path(node),
                    "text": _deleted_text(node),
                    "author": _wattr(node, "author"),
                    "date": _wattr(node, "date"),
                }
            )
        elif name == "ins":
            insertions.append(
                {
                    "part": part,
                    "element_path": element_path(node),
                    "text": visible_text(node),
                    "author": _wattr(node, "author"),
                    "date": _wattr(node, "date"),
                }
            )
        elif name in ("moveFrom", "moveTo"):
            direction = "FROM" if name == "moveFrom" else "TO"
            moves.append(
                {
                    "part": part,
                    "element_path": element_path(node),
                    "direction": direction,
                    "move_id": _wattr(node, "id"),
                    "text": _deleted_text(node) if direction == "FROM" else visible_text(node),
                    "author": _wattr(node, "author"),
                    "date": _wattr(node, "date"),
                }
            )


def structural_revision_notes(root: Any, part: str) -> list[str]:
    """Reproducible notes for structural revisions with an undecidable shape."""
    found: list[str] = []
    for node in root.iter():
        for parent_name, child_name in STRUCTURAL_REVISIONS:
            if _localname(node) != child_name:
                continue
            parent = node.getparent()
            if parent is None or _localname(parent) != parent_name:
                continue
            found.append(
                f"{part}:{element_path(node)}: structural revision "
                f"w:{parent_name}/w:{child_name} — the final table shape is "
                "indeterminate; resolve it in Word (accept/reject) before extraction"
            )
    return found


def _read_comments(package: DocxPackage) -> list[dict[str, Any]]:
    if not package.has_part(COMMENTS_PART):
        return []
    root = parse_xml_part(package, COMMENTS_PART)
    comments: list[dict[str, Any]] = []
    for node in root.iter():
        if _localname(node) != "comment":
            continue
        comments.append(
            {
                "comment_id": _wattr(node, "id"),
                "author": _wattr(node, "author"),
                "initials": _wattr(node, "initials"),
                "date": _wattr(node, "date"),
                "text": visible_text(node).strip(),
                "element_path": element_path(node),
                "part": COMMENTS_PART,
            }
        )
    return comments


# ------------------------------------------------------------------ public API
@dataclass(frozen=True)
class FinalView:
    """What the document says once revisions are resolved (SDD §7 step 3)."""

    paragraphs: tuple[str, ...]
    table_count: int
    review_context: dict = field(default_factory=dict)
    unresolved_structural: tuple[str, ...] = ()

    def text(self, separator: str = "\n") -> str:
        """FINAL text of the body paragraphs, joined in document order."""
        return separator.join(self.paragraphs)


def build_final_view(package: DocxPackage) -> FinalView:
    """Resolve the FINAL view of ``package`` without writing anything back."""
    document = parse_xml_part(package, MAIN_PART)
    body = _first(document, "body")
    if body is None:
        raise ExtensionError(
            "E_INPUT_INVALID",
            "docx main part has no w:body",
            detail={"part": MAIN_PART},
        )

    paragraphs = tuple(visible_text(paragraph) for paragraph in _iter_body_paragraphs(body))
    table_count = sum(1 for _ in _iter_tables(document))

    deletions: list[dict[str, Any]] = []
    insertions: list[dict[str, Any]] = []
    moves: list[dict[str, Any]] = []
    structural: list[str] = []

    for part in REVISION_PARTS:
        if part == MAIN_PART:
            root = document
        elif package.has_part(part):
            root = parse_xml_part(package, part)
        else:
            continue
        _collect_revisions(root, part, deletions, insertions, moves)
        structural.extend(structural_revision_notes(root, part))

    review_context: dict[str, Any] = {
        "deletions": deletions,
        "insertions": insertions,
        "moves": moves,
        "comments": _read_comments(package),
    }

    return FinalView(
        paragraphs=paragraphs,
        table_count=table_count,
        review_context=review_context,
        unresolved_structural=tuple(structural),
    )


def iter_revisions(view: FinalView) -> Iterable[dict[str, Any]]:
    """Convenience view over every review-context entry (document order per list)."""
    for key in ("deletions", "insertions", "moves"):
        for entry in view.review_context.get(key, ()):
            yield entry
