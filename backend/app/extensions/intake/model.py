"""One shape for every input format.

The batch layer exists so that ``report.json`` from a .docx and ``report.json``
from a scanned page can be compared without the caller knowing which reader ran.
That only works if every reader emits the same three things: text blocks, tables,
and an explicit list of what it could not do. Anything a reader could not parse
becomes an :class:`Issue`; nothing is dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Issue:
    """A fact the caller needs, stated once instead of inferred from absence."""

    code: str
    message: str
    severity: str = "WARNING"  # INFO | WARNING | ERROR
    detail: dict[str, Any] | None = None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class Block:
    kind: str  # heading | paragraph | table | image | page_break
    text: str = ""
    level: int | None = None
    page: int | None = None
    bbox: list[float] | None = None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.text:
            payload["text"] = self.text
        if self.level is not None:
            payload["level"] = self.level
        if self.page is not None:
            payload["page"] = self.page
        if self.bbox is not None:
            payload["bbox"] = self.bbox
        return payload


@dataclass
class Table:
    """A grid, not a string.

    ``cells`` keeps row/col/span so downstream code can address a cell instead of
    searching HTML for it. ``html`` is carried as provenance when the engine
    produced one, never as the primary representation.
    """

    index: int
    n_rows: int
    n_cols: int
    cells: list[dict[str, Any]]
    html: str | None = None
    source: str = "unknown"

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": self.index,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "source": self.source,
            "cells": self.cells,
        }
        if self.html:
            payload["html"] = self.html
        return payload


@dataclass
class Document:
    path: str
    sha256: str
    size: int
    kind: str
    engine: str
    blocks: list[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    page_count: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text)

    def add_issue(self, code: str, message: str, *, severity: str = "WARNING",
                  detail: dict[str, Any] | None = None) -> None:
        self.issues.append(Issue(code, message, severity, detail))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": "doc2md-intake-document/1.0",
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "kind": self.kind,
            "engine": self.engine,
            "page_count": self.page_count,
            "meta": self.meta,
            "blocks": [block.to_mapping() for block in self.blocks],
            "tables": [table.to_mapping() for table in self.tables],
            "issues": [issue.to_mapping() for issue in self.issues],
        }
