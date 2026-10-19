"""Readers for every supported family, producing one :class:`Document` shape.

Two rules hold across all of them:

1. **A reader never raises for content it merely cannot understand.** It records
   an issue and returns what it did get. Batch runs must survive one bad file.
2. **A reader never pretends.** When text has to come from OCR, ``needs_ocr`` is
   set and the visual pass fills it in; a reader that returned an empty block
   list with no issue would be indistinguishable from an empty document.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import pathlib
import subprocess
from typing import Any

from .model import Block, Document, Issue, Table
from .router import DOCX, IMAGE, OFFICE, PDF, TEXT, UNSUPPORTED, route

_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
_MIN_CHARS_PER_PAGE = 40


def sha256_file(path: pathlib.Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(chunk), b""):
            digest.update(piece)
    return digest.hexdigest()


def _decode(raw: bytes) -> tuple[str, str]:
    for encoding in _TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8-replace"


def _blocks_from_text(text: str, *, page: int | None = None) -> list[Block]:
    """Split prose into headings and paragraphs without inventing structure."""
    blocks: list[Block] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            body = " ".join(line.strip() for line in buffer).strip()
            if body:
                blocks.append(Block("paragraph", body, page=page))
            buffer.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if hashes and stripped[hashes:].startswith(" "):
            flush()
            blocks.append(Block("heading", stripped[hashes:].strip(), level=hashes, page=page))
            continue
        buffer.append(stripped)
    flush()
    return blocks


def _table_from_rows(rows: list[list[str]], *, index: int, source: str,
                     html: str | None = None) -> Table:
    width = max((len(row) for row in rows), default=0)
    cells = [
        {
            "cell_id": f"T{index}-R{r}C{c}",
            "row": r,
            "col": c,
            "rowspan": 1,
            "colspan": 1,
            "text": (row[c] if c < len(row) else ""),
        }
        for r, row in enumerate(rows)
        for c in range(width)
    ]
    return Table(index=index, n_rows=len(rows), n_cols=width, cells=cells,
                 html=html, source=source)


def _read_text(path: pathlib.Path, doc: Document) -> None:
    raw = path.read_bytes()
    text, encoding = _decode(raw)
    doc.meta["encoding"] = encoding

    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        except csv.Error as exc:
            doc.add_issue("CSV_PARSE_FAILED", str(exc), severity="ERROR")
            rows = []
        if rows:
            doc.tables.append(_table_from_rows(rows, index=0, source="csv"))
            doc.blocks.append(Block("table", f"table 0: {len(rows)} rows", page=None))
        return

    if suffix in {".json", ".jsonl"}:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            doc.add_issue("JSON_PARSE_FAILED", str(exc), severity="WARNING")
            payload = None
        if isinstance(payload, list) and payload and all(isinstance(r, dict) for r in payload):
            header = list(payload[0].keys())
            rows = [header] + [[str(row.get(key, "")) for key in header] for row in payload]
            doc.tables.append(_table_from_rows(rows, index=0, source="json"))
            doc.blocks.append(Block("table", f"table 0: {len(payload)} records"))
        elif isinstance(payload, dict):
            doc.meta["json_keys"] = sorted(payload.keys())[:50]

    doc.blocks.extend(_blocks_from_text(text))
    if not doc.blocks:
        doc.add_issue("EMPTY_CONTENT", "file decoded to no text at all", severity="INFO")


def _read_markitdown(path: pathlib.Path, doc: Document) -> str:
    from markitdown import MarkItDown

    result = MarkItDown().convert(path)
    text = result.text_content or ""
    doc.meta["markitdown_title"] = getattr(result, "title", None)
    return text


def _read_docx(path: pathlib.Path, doc: Document) -> None:
    from app.extensions import ExtensionError
    from app.extensions.word.final_view import build_final_view
    from app.extensions.word.native_tables import extract_tables
    from app.extensions.word.package import open_docx

    try:
        text = _read_markitdown(path, doc)
        doc.blocks.extend(_blocks_from_text(text))
    except Exception as exc:  # noqa: BLE001 - markitdown is an optional dependency
        doc.add_issue("TEXT_EXTRACTION_FAILED", f"markitdown: {exc}", severity="WARNING")

    try:
        package = open_docx(path)
        tables = extract_tables(package, build_final_view(package))
    except ExtensionError as exc:
        doc.add_issue(exc.code, exc.message, severity="WARNING",
                      detail=exc.detail if isinstance(exc.detail, dict) else None)
        return
    except Exception as exc:  # noqa: BLE001
        doc.add_issue("DOCX_TABLE_FAILED", str(exc), severity="WARNING")
        return

    for position, payload in enumerate(tables):
        doc.tables.append(
            Table(
                index=position,
                n_rows=int(payload["n_rows"]),
                n_cols=int(payload["n_cols"]),
                cells=list(payload["cells"]),
                source="docx-native",
            )
        )
        doc.blocks.append(Block("table", f"table {position}: native grid"))


def _pdf_pages(path: pathlib.Path, doc: Document) -> list[str] | None:
    """Text layer via poppler; ``None`` means the layer is unusable."""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True, timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        doc.add_issue("PDF_TEXT_LAYER_FAILED", str(exc), severity="WARNING")
        return None
    if proc.returncode != 0:
        doc.add_issue(
            "PDF_TEXT_LAYER_FAILED",
            proc.stderr.decode("utf-8", "replace")[:300],
            severity="WARNING",
        )
        return None

    pages = proc.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    doc.page_count = len(pages) or None
    total = sum(len(page.strip()) for page in pages)
    if not pages or total < _MIN_CHARS_PER_PAGE * len(pages):
        doc.add_issue(
            "PDF_TEXT_LAYER_ABSENT",
            f"{total} chars over {len(pages)} pages; treating as scanned",
            severity="INFO",
        )
        return None
    return pages


def _read_pdf(path: pathlib.Path, doc: Document, *, deep: bool) -> None:
    pages = _pdf_pages(path, doc)
    if pages is None:
        doc.meta["needs_ocr"] = True
        doc.engine = "paddle-structure"
        return
    for number, page in enumerate(pages, 1):
        doc.blocks.extend(_blocks_from_text(page, page=number))
    doc.engine = "pdftotext"
    doc.meta["text_layer"] = "PRESENT"
    if deep:
        # Text is already good; the visual pass runs only to recover tables.
        doc.meta["needs_visual"] = True
        doc.meta["visual_tables_only"] = True


def _read_office(path: pathlib.Path, doc: Document) -> None:
    text = _read_markitdown(path, doc)
    doc.blocks.extend(_blocks_from_text(text))
    if not text.strip():
        doc.add_issue("EMPTY_CONTENT", "office converter returned no text", severity="INFO")


def read(path: str | pathlib.Path, *, deep: bool = False) -> Document:
    """Read one file. Raises only for I/O problems, never for format problems.

    ``deep`` additionally sends text-layer PDFs through the visual engine for
    tables. It is opt-in because it costs seconds per page.
    """
    file_path = pathlib.Path(path)
    stat = file_path.stat()
    doc = Document(
        path=str(file_path),
        sha256=sha256_file(file_path),
        size=stat.st_size,
        kind="",
        engine="none",
    )
    kind, notes = route(file_path)
    doc.kind = kind
    for note in notes:
        doc.add_issue(note["code"], note["message"], severity="WARNING")

    if kind == TEXT:
        doc.engine = "text"
        _read_text(file_path, doc)
    elif kind == DOCX:
        doc.engine = "docx+markitdown"
        _read_docx(file_path, doc)
    elif kind == OFFICE:
        doc.engine = "markitdown"
        _read_office(file_path, doc)
    elif kind == PDF:
        _read_pdf(file_path, doc, deep=deep)
    elif kind == IMAGE:
        doc.engine = "paddle-structure"
        doc.meta["needs_ocr"] = True
        doc.page_count = 1
    else:
        doc.add_issue(
            "UNSUPPORTED_FORMAT",
            f"no reader for {file_path.suffix!r}; file recorded but not parsed",
            severity="ERROR",
        )
    return doc


def apply_visual(
    path: pathlib.Path,
    doc: Document,
    payload: dict[str, Any],
    *,
    tables_only: bool = False,
) -> None:
    """Merge a PP-StructureV3 result into ``doc``.

    The engine reports per-page layout blocks and per-table cell boxes. Cells are
    kept only when the engine gave a grid; a table with no ``cell_box_list`` is
    carried as an issue instead of an empty table, because an empty table reads
    as "this table has no cells".

    ``tables_only`` is used for text-layer PDFs: their text is better than OCR,
    so only the table geometry is taken from the engine.
    """
    doc.meta["needs_ocr"] = False
    doc.meta["needs_visual"] = False
    doc.engine = "paddle-structure"
    pages = payload.get("pages") or []
    # The visual pass may cover fewer pages than the text layer; the document's
    # own count must not shrink because the engine was capped.
    doc.page_count = max(doc.page_count or 0, len(pages)) or doc.page_count

    produced = 0
    for page in pages:
        number = page.get("page_index", 0) + 1
        if not tables_only:
            for block in page.get("blocks") or []:
                produced += 1
                doc.blocks.append(
                    Block(
                        kind=block.get("kind", "paragraph"),
                        text=block.get("text", ""),
                        page=number,
                        bbox=block.get("bbox"),
                    )
                )
        for position, table in enumerate(page.get("tables") or []):
            cells = table.get("cells") or []
            if not cells:
                doc.add_issue(
                    "TABLE_HAS_NO_CELLS",
                    f"page {number} table {position}: engine returned no cell geometry",
                    severity="WARNING",
                )
                continue
            produced += 1
            doc.tables.append(
                Table(
                    index=len(doc.tables),
                    n_rows=int(table.get("n_rows") or 0),
                    n_cols=int(table.get("n_cols") or 0),
                    cells=cells,
                    html=table.get("html"),
                    source="paddle-structure",
                )
            )
    if not produced and not tables_only:
        # An empty visual result used to look exactly like an empty page. In
        # tables_only mode zero tables is a normal outcome, not an empty read.
        doc.add_issue(
            "VISUAL_ENGINE_EMPTY",
            "engine returned no blocks and no tables for this file",
            severity="WARNING",
        )
    for issue in payload.get("issues") or []:
        doc.issues.append(
            Issue(
                code=issue.get("code", "VISUAL_ENGINE_ISSUE"),
                message=issue.get("message", ""),
                severity=issue.get("severity", "WARNING"),
                detail=issue.get("detail"),
            )
        )
