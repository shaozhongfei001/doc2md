"""Decide which reader handles a path, from bytes as well as from the name.

A suffix is a claim; the first bytes are evidence. A ``.pdf`` that does not start
with ``%PDF`` is still routed by suffix but carries ``SUFFIX_CONTENT_MISMATCH``,
because a reader that trusts the name will fail with a confusing error later.
"""

from __future__ import annotations

import pathlib

DOCX = "docx"
PDF = "pdf"
IMAGE = "image"
TEXT = "text"
OFFICE = "office"
UNSUPPORTED = "unsupported"

_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
)
_TEXT_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv",
        ".log", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".xml", ".html", ".htm",
        ".rst", ".py", ".js", ".ts", ".sql",
    }
)
_OFFICE_SUFFIXES = frozenset({".pptx", ".xlsx", ".xlsm"})
_LEGACY_OFFICE_SUFFIXES = frozenset({".doc", ".ppt", ".xls", ".rtf", ".odt"})

_MAGIC = {
    b"%PDF": PDF,
    b"PK\x03\x04": None,  # zip container: docx/pptx/xlsx share it, suffix decides
    b"\x89PNG": IMAGE,
    b"\xff\xd8\xff": IMAGE,
    b"GIF8": IMAGE,
    b"BM": IMAGE,
    b"II*\x00": IMAGE,
    b"MM\x00*": IMAGE,
}


def route(path: str | pathlib.Path) -> tuple[str, list[dict[str, str]]]:
    """Return ``(kind, notes)``; notes are facts, not failures."""
    p = pathlib.Path(path)
    suffix = p.suffix.lower()
    notes: list[dict[str, str]] = []

    head = b""
    try:
        with p.open("rb") as handle:
            head = handle.read(16)
    except OSError as exc:
        notes.append({"code": "UNREADABLE_HEAD", "message": str(exc)})

    if suffix == ".docx":
        return DOCX, notes
    if suffix == ".pdf":
        if head and not head.startswith(b"%PDF"):
            notes.append(
                {
                    "code": "SUFFIX_CONTENT_MISMATCH",
                    "message": ".pdf does not start with %PDF; reader may fail",
                }
            )
        return PDF, notes
    if suffix in _IMAGE_SUFFIXES:
        return IMAGE, notes
    if suffix in _TEXT_SUFFIXES:
        return TEXT, notes
    if suffix in _OFFICE_SUFFIXES:
        return OFFICE, notes
    if suffix in _LEGACY_OFFICE_SUFFIXES:
        notes.append(
            {
                "code": "LEGACY_OFFICE_NOT_SUPPORTED",
                "message": f"{suffix} is a legacy binary format; convert to OOXML first",
            }
        )
        return UNSUPPORTED, notes

    for magic, kind in _MAGIC.items():
        if head.startswith(magic) and kind:
            notes.append(
                {
                    "code": "ROUTED_BY_MAGIC",
                    "message": f"no known suffix; routed as {kind} from file signature",
                }
            )
            return kind, notes

    notes.append(
        {"code": "UNKNOWN_FORMAT", "message": f"no reader claims suffix {suffix!r}"}
    )
    return UNSUPPORTED, notes
