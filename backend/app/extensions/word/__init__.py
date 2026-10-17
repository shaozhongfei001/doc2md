"""Native Word (``.docx``) structure support for DOC2MD-19CAP.

Scope (SDD §7, PI-F04-01): structural fidelity for native Word tables plus a
FINAL/REVIEW_CONTEXT split. This package does **not** re-implement a Word parser
and does not add semantic enhancement (header paths, continuation joins and
footnotes belong to later loops).

Module map
----------
``package``       read-only, fail-closed OOXML access (zip guards, part hashes)
``final_view``    FINAL text of the document + the review trail kept beside it
``native_tables`` ``w:tbl`` → contract-shaped ``Table`` payloads

What this package deliberately does not do
------------------------------------------
* it never writes the source ``.docx`` (no rename, no touch, no lock file), so
  ``source_sha256`` is stable across a read;
* it never executes macros, fields or external relationships, and never resolves
  external entities;
* it never guesses: an ambiguous merge, an unexplained hole or an undecidable
  structural revision raises instead of being filled in.

Error codes used here are the ones registered by the contract layer
(``E_INPUT_INVALID``, ``E_DOCX_MERGE_INVALID``, ``E_DOCX_REVISION_UNRESOLVED``,
``E_TABLE_SPAN_INVALID``); this package adds no new code.
"""

from __future__ import annotations

from app.extensions.word.final_view import (
    FinalView,
    build_final_view,
    element_path,
    structural_revision_notes,
    visible_text,
)
from app.extensions.word.native_tables import CELL_KEYS, TABLE_KEYS, extract_tables
from app.extensions.word.package import (
    DOCX_MAX_COMPRESSION_RATIO,
    DOCX_MAX_MEMBER_BYTES,
    DOCX_MAX_MEMBERS,
    DOCX_MAX_TOTAL_BYTES,
    DocxPackage,
    open_docx,
    parse_xml_part,
)

__all__ = [
    # package
    "DOCX_MAX_MEMBERS",
    "DOCX_MAX_MEMBER_BYTES",
    "DOCX_MAX_TOTAL_BYTES",
    "DOCX_MAX_COMPRESSION_RATIO",
    "DocxPackage",
    "open_docx",
    "parse_xml_part",
    # final view
    "FinalView",
    "build_final_view",
    "visible_text",
    "element_path",
    "structural_revision_notes",
    # tables
    "extract_tables",
    "TABLE_KEYS",
    "CELL_KEYS",
]
