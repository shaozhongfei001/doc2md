"""End-to-end assembly: one ``.docx`` in, one extension artifact out.

Before this module existed, L01–L09 were nine islands: `grep` for
``extract_tables`` / ``derive_header_paths`` / ``build_review_packet`` in
``runner.py`` and ``cli.py`` returned nothing, so the extension layer had never
processed a document. This module is the seam.

It is deliberately *not* a new authority on anything:

* it does not re-derive header bands, notes or grids — it calls the module that
  owns each rule (L03 tables, L09 flags/notes, L05 semantics/notes, L05 packet);
* it does not invent missing fields. ``missing_contract_fields`` lists exactly why
  the produced payload is still **not** a ``DocumentEnhancement`` (no geometries,
  no locator-bearing evidence, no visuals/continuations), instead of filling them
  with plausible defaults;
* it writes only through ``io.artifact_store`` (staging + fsync + rename), so a
  reader never sees a half-written generation and ``--dry-run`` cannot create a
  directory, a lock or a file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import ExtensionError
from .io.artifact_store import ArtifactStore
from .render.review_packet import ReviewPacket, build_review_packet
from .tables.notes import NotesResult, bind_notes, link_cell_note_ids
from .tables.semantics import derive_header_paths
from .word.final_view import build_final_view
from .word.native_tables import extract_tables
from .word.notes_extract import read_notes
from .word.package import DocxPackage
from .word.table_flags import read_table_flags

ASSEMBLY_SCHEMA = "doc2md-extension-assembly/0.1"

#: What this path still cannot produce, stated once so a reader of the artifact
#: does not mistake it for a complete ``DocumentEnhancement``.
MISSING_CONTRACT_FIELDS = (
    "geometries (no page raster in the DOCX path)",
    "evidence[].locator (candidate ids are recorded, locators are not)",
    "visuals / flow / chart (no image analysis on this path)",
    "continuations (needs page index; DOCX only has a stale render cache)",
    "review decisions (this is a candidate, not a reviewed generation)",
)

__all__ = [
    "ASSEMBLY_SCHEMA",
    "MISSING_CONTRACT_FIELDS",
    "AssemblyResult",
    "assemble_package",
    "write_assembly",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class AssemblyResult:
    generation_id: str
    source_sha256: str
    tables: list[dict[str, Any]]
    notes: list[dict[str, Any]]
    packets: list[ReviewPacket]
    issues: list[dict[str, Any]] = field(default_factory=list)
    stats: Mapping[str, Any] = field(default_factory=dict)

    @property
    def markdown(self) -> str:
        """All tables' packets in document order, separated by a rule.

        A document with no tables still gets a packet, because an empty file and
        "this document has no tables" are different statements and only one of them
        is true.
        """
        if not self.packets:
            return (
                "# 复核包\n\n"
                "本文档未检出原生 Word 表格（w:tbl）。\n"
                f"- 源摘要：`{self.source_sha256}`\n"
                f"- 生成：`{self.generation_id}`\n"
            )
        return "\n\n---\n\n".join(packet.markdown for packet in self.packets)

    def payload(self) -> dict[str, Any]:
        return {
            "schema": ASSEMBLY_SCHEMA,
            "generation_id": self.generation_id,
            "source_sha256": self.source_sha256,
            "missing_contract_fields": list(MISSING_CONTRACT_FIELDS),
            "stats": dict(self.stats),
            "tables": [dict(table) for table in self.tables],
            "notes": [dict(note) for note in self.notes],
            "issues": [dict(issue) for issue in self.issues],
        }

    def to_mapping(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "source_sha256": self.source_sha256,
            "stats": dict(self.stats),
            "issue_count": len(self.issues),
            "issue_codes": sorted({str(issue.get("code")) for issue in self.issues}),
        }


def _note_candidates_for(table_id: str, candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Notes whose target cell belongs to ``table_id``; unbound notes go to table 0.

    An unresolved note has no target, so it cannot be attributed to one table by
    cell id. Rather than dropping it (which would hide a missing footnote), it is
    attached to the first table so ``bind_notes`` marks it ``NEEDS_REVIEW`` and it
    stays visible in that table's packet.
    """
    owned: list[dict[str, Any]] = []
    for candidate in candidates:
        targets = candidate.get("target_ids") or ()
        if not targets:
            owned.append(dict(candidate))
            continue
        if any(str(target).startswith(f"{table_id}-") for target in targets):
            owned.append(dict(candidate))
    return owned


def _evidence_candidates(
    table: Mapping[str, Any], notes: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    ids: list[str] = []
    for evidence_id in table.get("omission_evidence_ids") or ():
        if evidence_id not in ids:
            ids.append(str(evidence_id))
    for note in notes:
        for evidence_id in note.get("evidence_ids") or ():
            if evidence_id not in ids:
                ids.append(str(evidence_id))
    return [{"candidate_evidence_id": evidence_id} for evidence_id in ids]


def assemble_package(
    package: DocxPackage,
    *,
    generation_id: str,
    source_sha256: str | None = None,
    job_key: str = "local-word",
) -> AssemblyResult:
    """Run the whole Word path once and return the assembled artifact."""
    if not isinstance(package, DocxPackage):
        raise ExtensionError(
            "E_INPUT_INVALID", f"package must be a DocxPackage, got {type(package).__name__}"
        )
    if not isinstance(generation_id, str) or not generation_id:
        raise ExtensionError(
            "E_INPUT_INVALID",
            f"generation_id must be a non-empty string, got {generation_id!r}",
        )

    view = build_final_view(package)
    tables = extract_tables(package, view)
    flags = read_table_flags(package)
    flag_by_table = {flag.table_id: flag for flag in flags}

    extraction = read_notes(package)
    candidates = extraction.candidates()

    issues: list[dict[str, Any]] = []
    for issue in extraction.issues:
        issues.append({"layer": "notes_extract", **dict(issue)})

    assembled_tables: list[dict[str, Any]] = []
    packets: list[ReviewPacket] = []
    bound_notes: list[dict[str, Any]] = []

    for table in tables:
        table_id = table["table_id"]
        flag = flag_by_table.get(table_id)
        table_issues: list[dict[str, Any]] = []

        semantics = derive_header_paths(
            table,
            header_rows=list(flag.header_rows) if flag else [],
            context={},
        )
        for issue in semantics.issues:
            payload = issue.to_dict()
            table_issues.append({"layer": "semantics", **payload})

        table_candidates = _note_candidates_for(table_id, candidates)
        notes_result: NotesResult = bind_notes(table, table_candidates) if table_candidates else NotesResult(notes=[], issues=[])
        for issue in notes_result.issues:
            table_issues.append({"layer": "notes", **issue.to_dict()})

        linked = link_cell_note_ids(semantics.cell_semantics, notes_result.notes)

        table = dict(table)
        table["cell_semantics"] = linked
        table["notes"] = [dict(note) for note in notes_result.notes]
        table["assembly_issues"] = table_issues

        packet = build_review_packet(
            table,
            semantics=linked,
            notes=notes_result.notes,
            evidence_candidates=_evidence_candidates(table, notes_result.notes),
            blocked=table_issues,
            context={},
        )
        assembled_tables.append(table)
        packets.append(packet)
        bound_notes.extend(dict(note) for note in notes_result.notes)
        issues.extend(table_issues)

    source_digest = source_sha256 or package.source_sha256
    stats = {
        "tables": len(assembled_tables),
        "tables_with_header_band": sum(
            1
            for table in assembled_tables
            if flag_by_table.get(table["table_id"]) and flag_by_table[table["table_id"]].header_rows
        ),
        "notes": len(bound_notes),
        "notes_publishable": sum(1 for note in bound_notes if note["state"] != "NEEDS_REVIEW"),
        "cells_with_header_path": sum(
            1
            for table in assembled_tables
            for cell in table["cell_semantics"]
            if cell["column_header_cell_ids"] or cell["row_header_cell_ids"]
        ),
        "issues": len(issues),
        "job_key": job_key,
    }
    return AssemblyResult(
        generation_id=generation_id,
        source_sha256=source_digest,
        tables=assembled_tables,
        notes=bound_notes,
        packets=packets,
        issues=issues,
        stats=stats,
    )


def write_assembly(
    store: ArtifactStore,
    result: AssemblyResult,
    *,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Stage + commit the assembly under ``extension_root``; return the receipt."""
    if not isinstance(store, ArtifactStore):
        raise ValueError(f"store must be an ArtifactStore, got {type(store).__name__}")

    payload = json.dumps(
        result.payload(), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    packet = result.markdown.encode("utf-8")

    attempt = attempt_id or store.new_attempt_id()
    report_file = store.write_staged(attempt, "report.json", payload)
    packet_file = store.write_staged(attempt, "review_packet.md", packet)

    # ``commit_generation`` refuses to publish without a staged manifest, which is
    # the point: a generation must carry its own file ledger rather than relying on
    # whatever happens to be on disk.
    manifest = {
        "schema": "doc2md-extension-manifest/0.1",
        "generation_id": result.generation_id,
        "content_sha256": result.source_sha256,
        "files": {
            report_file.relative_path: report_file.sha256,
            packet_file.relative_path: packet_file.sha256,
        },
        "stats": dict(result.stats),
        "missing_contract_fields": list(MISSING_CONTRACT_FIELDS),
        "created_by": "DOC2MD-19CAP/L10",
    }
    manifest_file = store.write_staged(
        attempt,
        "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )

    expected = {
        report_file.relative_path: report_file.sha256,
        packet_file.relative_path: packet_file.sha256,
        manifest_file.relative_path: manifest_file.sha256,
    }
    receipt = store.commit_generation(
        attempt,
        content_sha256=result.source_sha256,
        generation_id=result.generation_id,
        expected_files=expected,
    )
    return {
        "generation_id": result.generation_id,
        "attempt_id": attempt,
        "files": expected,
        "receipt": {
            "generation_id": getattr(receipt, "generation_id", result.generation_id),
            "directory": getattr(receipt, "directory", None),
            "manifest_sha256": getattr(receipt, "manifest_sha256", None),
            "already_committed": getattr(receipt, "already_committed", False),
            "files": [
                {
                    "relative_path": getattr(item, "relative_path", None),
                    "sha256": getattr(item, "sha256", None),
                }
                for item in getattr(receipt, "files", ())
            ],
        },
    }
