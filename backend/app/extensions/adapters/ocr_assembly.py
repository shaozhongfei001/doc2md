"""Assembly entry for OCR tables (Owner 2026-10-19).

``pipeline.assemble_package`` starts from a ``DocxPackage`` because native tables carry
things OCR does not: ``w:tblHeader``, footnote anchors, ``slots`` from the XML grid. This
module is the OCR-side entry, and its rule is **empty plus an issue, never a plausible
default**:

* ``notes`` is empty **and** an issue says footnotes were not extracted. An empty list
  alone would read as "this table has no footnotes", which is a different claim.
* ``omission_evidence_ids`` is empty **and** an issue says omission was not computed.
* Evidence ids are empty **and** an issue says OCR produced no candidate evidence.

The tables themselves are real: cells, slots and the human-declared header band all come
from :mod:`ocr_tables`. What this module refuses to do is pretend the rest exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .. import ExtensionError
from .ocr_tables import OcrTable

__all__ = ["OcrAssembly", "assemble_from_ocr"]

NOTES_UNAVAILABLE = "NOTES_UNAVAILABLE_FROM_OCR"
OMISSION_UNAVAILABLE = "OMISSION_UNAVAILABLE_FROM_OCR"
EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE_FROM_OCR"
NO_TABLES = "NO_TABLES_RECOGNISED"


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class OcrAssembly:
    generation_id: str
    source_sha256: str | None
    tables: list[dict[str, Any]]
    notes: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    packets: list[Any] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "source_sha256": self.source_sha256,
            "tables": [dict(table) for table in self.tables],
            "notes": [dict(note) for note in self.notes],
            "issues": [dict(issue) for issue in self.issues],
            "packets": len(self.packets),
            "stats": dict(self.stats),
        }


def assemble_from_ocr(
    tables: Sequence[OcrTable],
    *,
    generation_id: str,
    source_sha256: str | None = None,
    job_key: str = "ocr",
) -> OcrAssembly:
    if not isinstance(generation_id, str) or not generation_id:
        raise _input_error(f"generation_id must be a non-empty string, got {generation_id!r}")
    if source_sha256 is not None and (
        not isinstance(source_sha256, str) or len(source_sha256) != 64
    ):
        raise _input_error("source_sha256 must be a 64-character hex string or None")

    if not tables:
        # Refusing is the point: an "assembly" of nothing looks like a clean empty result.
        raise _input_error(
            "no OCR tables to assemble; an empty document and a failed recognition would "
            "look identical, so an explicit issue must be produced by the caller instead"
        )

    payloads: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, OcrTable):
            # Accepting a dict here would let an undeclared header slip in as data.
            raise _input_error(
                f"tables must be OcrTable instances, got {type(table).__name__}; a declared "
                "header is not optional"
            )
        payload = {
            "table_id": table.table_id,
            "n_rows": table.n_rows,
            "n_cols": table.n_cols,
            "cells": [dict(cell) for cell in table.cells],
            "slots": [list(row) for row in table.slots],
            "notes": [],
            "omission_evidence_ids": [],
            "issue_codes": [issue["code"] for issue in table.issues],
        }
        payloads.append(payload)
        issues.append(
            {
                "layer": "notes",
                "code": NOTES_UNAVAILABLE,
                "table_id": table.table_id,
                "message": "OCR carries no footnote anchors; an empty note list is not "
                "evidence that the table has none",
            }
        )
        issues.append(
            {
                "layer": "structure",
                "code": OMISSION_UNAVAILABLE,
                "table_id": table.table_id,
                "message": "omission was not computed for an OCR table; the native "
                "gridAfter path does not exist in an image",
            }
        )

    issues.append(
        {
            "layer": "evidence",
            "code": EVIDENCE_UNAVAILABLE,
            "table_id": None,
            "message": "OCR produced no candidate evidence; review packets cannot claim "
            "omission ⊆ evidence for this generation",
        }
    )

    stats = {
        "generation_id": generation_id,
        "job_key": job_key,
        "tables": len(payloads),
        "cells": sum(len(payload["cells"]) for payload in payloads),
        "notes": 0,
        "issues": len(issues),
        "header_rows_by_table": {table.table_id: list(table.header_rows) for table in tables},
        "notes_unavailable": True,
        "evidence_unavailable": True,
    }
    return OcrAssembly(
        generation_id=generation_id,
        source_sha256=source_sha256,
        tables=payloads,
        notes=[],
        issues=issues,
        stats=stats,
    )
