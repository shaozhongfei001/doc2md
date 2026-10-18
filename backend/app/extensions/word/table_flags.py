"""Producer side of the L05 gaps: header band, column widths, page index.

`L05-GAP-01`: ``derive_header_paths`` needs to be told which rows form the header
band, and the only admissible source is the **native** ``w:trPr/w:tblHeader``
mark. A table without the mark reports ``header_rows = ()`` and the caller must
keep every cell ``NEEDS_REVIEW`` — the temptation to "the first row is bold, so it
is a header" is exactly what SDD 9.1 refuses.

`L05-GAP-02`: ``propose_continuation`` wants column widths and a page index.

* widths come from ``w:tblGrid/w:gridCol/@w:w`` (twips) and are normalised to
  ratios, so two pages rendered at different sizes still compare;
* a page index is only available from Word's own ``w:lastRenderedPageBreak``
  cache. That cache can be **stale** (Word writes it at save time), so it is
  reported as ``page_source="LAST_RENDERED_PAGE_BREAK"`` and never silently
  upgraded to a measured page. Without the cache the value is ``None``.

Traversal reuse
---------------
Table order must match ``native_tables.extract_tables`` exactly, or a header band
would be attached to the wrong table — a silent, plausible corruption. Rather than
re-implement the walk (and risk drift), this module imports the same private
helpers that ``extract_tables`` uses and a test asserts the two walks agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from .. import ExtensionError
from .final_view import _localname  # same localname helper the Word layer uses
from .native_tables import (
    _cells_in,
    _nested_tables,
    _rows_in,
    _top_level_tables,
    _wattr,
    _first,
)
from .package import DocxPackage, parse_xml_part

MAIN_PART = "word/document.xml"

HEADER_SOURCE_NATIVE = "TBL_HEADER"
HEADER_SOURCE_ABSENT = "NO_NATIVE_MARK"
PAGE_SOURCE_CACHE = "LAST_RENDERED_PAGE_BREAK"
PAGE_SOURCE_UNAVAILABLE = "UNAVAILABLE"

__all__ = [
    "HEADER_SOURCE_ABSENT",
    "HEADER_SOURCE_NATIVE",
    "PAGE_SOURCE_CACHE",
    "PAGE_SOURCE_UNAVAILABLE",
    "TableFlags",
    "read_table_flags",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class TableFlags:
    """Producer output for one table, aligned with ``extract_tables`` order."""

    table_index: int
    table_id: str
    header_rows: tuple[int, ...]
    header_source: str
    column_widths: tuple[float, ...] | None
    column_width_units: str | None
    first_page_index: int | None
    page_source: str
    evidence_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "table_index": self.table_index,
            "table_id": self.table_id,
            "header_rows": list(self.header_rows),
            "header_source": self.header_source,
            "column_widths": None if self.column_widths is None else list(self.column_widths),
            "column_width_units": self.column_width_units,
            "first_page_index": self.first_page_index,
            "page_source": self.page_source,
            "evidence_ids": list(self.evidence_ids),
        }


def _document_tables(body: Any) -> Iterator[Any]:
    """Top-level tables in document order, each followed by its nested tables.

    Deliberately mirrors ``native_tables._build_table``: parent first, then the
    nested tables found in its cells. Any divergence here would shift every
    ``table_index`` after it.
    """
    for table in _top_level_tables(body):
        yield from _document_tables_from(table)


def _document_tables_from(table: Any) -> Iterator[Any]:
    yield table
    for row in _rows_in(table):
        for cell in _cells_in(row):
            for nested in _nested_tables(cell):
                yield from _document_tables_from(nested)


def _page_at_table(document: Any) -> dict[str, int]:
    """Count ``w:lastRenderedPageBreak`` occurrences before each table element.

    Keyed by the element's XPath rather than ``id()``: lxml may hand out a fresh
    proxy object for the same node between two traversals, so identity-based keys
    silently miss. If the document carries **no** such cache at all, nothing is
    returned — an absent cache means the page is unknown, not page 0.
    """
    tree = document.getroottree()
    pages: dict[str, int] = {}
    page = 0
    seen_break = False
    for node in document.iter():
        name = _localname(node)
        if name == "lastRenderedPageBreak":
            page += 1
            seen_break = True
        elif name == "tbl":
            pages.setdefault(tree.getpath(node), page)
    return pages if seen_break else {}


def _header_rows(table: Any, *, table_index: int) -> tuple[tuple[int, ...], tuple[str, ...]]:
    rows: list[int] = []
    evidence: list[str] = []
    for index, row in enumerate(_rows_in(table)):
        properties = _first(row, "trPr")
        if properties is None:
            continue
        if _first(properties, "tblHeader") is not None:
            rows.append(index)
            evidence.append(f"docx:tblHeader:{table_index}:{index}")
    return tuple(rows), tuple(evidence)


def _column_widths(table: Any, *, table_index: int) -> tuple[tuple[float, ...] | None, tuple[str, ...]]:
    grid = _first(table, "tblGrid")
    if grid is None:
        return None, ()
    raw: list[float] = []
    for child in grid:
        if _localname(child) != "gridCol":
            continue
        value = _wattr(child, "w")
        if not isinstance(value, str) or not value.strip():
            return None, ()
        try:
            number = float(value)
        except ValueError:
            return None, ()
        if number <= 0:
            return None, ()
        raw.append(number)
    if not raw:
        return None, ()
    total = sum(raw)
    return tuple(value / total for value in raw), (f"docx:tblGrid:{table_index}",)


def read_table_flags(package: DocxPackage) -> list[TableFlags]:
    """Read header bands, column widths and page cache for every table."""
    if not isinstance(package, DocxPackage):
        raise _input_error(f"package must be a DocxPackage, got {type(package).__name__}")
    document = parse_xml_part(package, MAIN_PART)
    body = _first(document, "body")
    if body is None:
        raise _input_error("docx main part has no w:body", part=MAIN_PART)

    pages = _page_at_table(document)
    tree = document.getroottree()
    flags: list[TableFlags] = []
    for table_index, table in enumerate(_document_tables(body)):
        header_rows, header_evidence = _header_rows(table, table_index=table_index)
        widths, width_evidence = _column_widths(table, table_index=table_index)
        page_index = pages.get(tree.getpath(table))
        evidence = header_evidence + width_evidence
        if page_index is not None:
            evidence += (f"docx:lastRenderedPageBreak:{table_index}:{page_index}",)
        flags.append(
            TableFlags(
                table_index=table_index,
                table_id=f"TBL-{table_index + 1:04d}",
                header_rows=header_rows,
                header_source=HEADER_SOURCE_NATIVE if header_rows else HEADER_SOURCE_ABSENT,
                column_widths=widths,
                column_width_units="TWIPS" if widths else None,
                first_page_index=page_index,
                page_source=PAGE_SOURCE_CACHE if page_index is not None else PAGE_SOURCE_UNAVAILABLE,
                evidence_ids=evidence,
            )
        )
    return flags


def flags_for(flags: list[TableFlags], table_index: int) -> TableFlags:
    """Look one table up by index; a missing index is an error, not a default."""
    for item in flags:
        if item.table_index == table_index:
            return item
    raise _input_error(
        f"no table flags for index {table_index}; known: "
        f"{[item.table_index for item in flags]}"
    )


def header_rows_by_table(flags: list[TableFlags]) -> dict[int, tuple[int, ...]]:
    """Convenience mapping for ``derive_header_paths(header_rows=...)`` per table."""
    return {item.table_index: item.header_rows for item in flags}
