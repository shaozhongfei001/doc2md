"""Read-only, fail-closed access to the OOXML parts of a ``.docx`` container.

Design rules (SDD §7 step 1):

* the original file is only ever opened ``"rb"``; nothing in this module can
  write, rename or lock the source. ``source_sha256`` is computed from the bytes
  read at open time and re-checked on every part read, so "read the original,
  leave the original byte-identical" is enforced rather than promised;
* a container is rejected *before* any XML is parsed when its member names can
  escape the archive (absolute paths, ``..``, backslashes, drive letters), when
  it exceeds the member/size/compression-ratio ceilings, or when an XML part
  carries a ``<!DOCTYPE`` / ``<!ENTITY`` declaration (DTD + external entity);
* macros, fields and external relationships are never executed: this module
  hands out bytes only and parses with ``resolve_entities=False``.

Anything rejected here raises ``E_INPUT_INVALID`` (SDD §14); there is no
"best effort" open, because a partially trusted container would poison every
downstream evidence id.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from lxml import etree

from app.extensions import ExtensionError

__all__ = [
    "DOCX_MAX_MEMBERS",
    "DOCX_MAX_MEMBER_BYTES",
    "DOCX_MAX_TOTAL_BYTES",
    "DOCX_MAX_COMPRESSION_RATIO",
    "DocxPackage",
    "open_docx",
    "parse_xml_part",
]

#: Hard ceilings. ``members``/``total`` mirror ``limits.max_docx_members`` and
#: ``limits.max_docx_expanded_bytes`` of ``config/doc2md_extensions.example.yaml``;
#: the per-member and compression-ratio bounds are reader-local guards so the
#: container can never be opened without a bound at all.
DOCX_MAX_MEMBERS = 10_000
DOCX_MAX_MEMBER_BYTES = 100 * 1024 * 1024
DOCX_MAX_TOTAL_BYTES = 512 * 1024 * 1024
DOCX_MAX_COMPRESSION_RATIO = 200.0

#: Main document part; without it the container is not a Word document.
MAIN_PART = "word/document.xml"

_READ_CHUNK = 1024 * 1024
#: Rolling window kept so a ``<!DOCTYPE`` split across two reads is still caught.
_DTD_TAIL = 32
_DTD_RE = re.compile(rb"<!\s*(?i:doctype|entity)")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _reject(message: str, detail: object = None) -> None:
    raise ExtensionError("E_INPUT_INVALID", message, detail=detail)


def _check_member_name(name: str) -> None:
    """Reject member names that could resolve outside the container."""
    if not name:
        _reject("docx member with an empty name", detail={"member": name})
    if "\x00" in name:
        _reject("docx member name contains a NUL byte", detail={"member": repr(name)})
    if name.startswith("/") or name.startswith("\\"):
        _reject("docx member uses an absolute path", detail={"member": name})
    if name.startswith("//") or name.startswith("\\\\"):
        _reject("docx member uses a UNC path", detail={"member": name})
    if "\\" in name:
        _reject("docx member name contains a backslash", detail={"member": name})
    if _DRIVE_RE.match(name):
        _reject("docx member name starts with a drive letter", detail={"member": name})
    if ".." in name.split("/"):
        _reject("docx member name escapes the container", detail={"member": name})


def _is_xml_like(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".xml") or lowered.endswith(".rels") or lowered.endswith(".vml")


def _scan_members(path: Path) -> tuple[tuple[str, ...], dict[str, "_Member"], str]:
    """One bounded pass over the archive: names, sizes, digests, DTD check."""
    file_digest = hashlib.sha256()
    members: dict[str, _Member] = {}
    order: list[str] = []

    try:
        handle = path.open("rb")
    except OSError as exc:  # pragma: no cover - permission edge, still fail closed
        _reject(f"cannot open docx: {exc}", detail={"path": str(path)})

    with handle as raw:
        while True:
            chunk = raw.read(_READ_CHUNK)
            if not chunk:
                break
            file_digest.update(chunk)

    try:
        archive = zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        _reject(f"not a readable docx (zip) container: {exc}", detail={"path": str(path)})

    with archive:
        infos = archive.infolist()
        if not infos:
            _reject("docx container has no members", detail={"path": str(path)})
        if len(infos) > DOCX_MAX_MEMBERS:
            _reject(
                "docx member count exceeds the configured limit",
                detail={"members": len(infos), "limit": DOCX_MAX_MEMBERS},
            )

        total = 0
        for info in infos:
            name = info.filename
            _check_member_name(name)
            if name in members:
                _reject("docx container declares a duplicate member name", detail={"member": name})

            digest = hashlib.sha256()
            size = 0
            tail = b""
            try:
                with archive.open(info, "r") as part:
                    while True:
                        chunk = part.read(_READ_CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > DOCX_MAX_MEMBER_BYTES:
                            _reject(
                                "docx member expands beyond the per-member limit",
                                detail={
                                    "member": name,
                                    "limit": DOCX_MAX_MEMBER_BYTES,
                                },
                            )
                        total += len(chunk)
                        if total > DOCX_MAX_TOTAL_BYTES:
                            _reject(
                                "docx expands beyond the total byte limit",
                                detail={"limit": DOCX_MAX_TOTAL_BYTES},
                            )
                        digest.update(chunk)
                        if _is_xml_like(name):
                            window = tail + chunk
                            if _DTD_RE.search(window):
                                _reject(
                                    "docx XML part declares a DTD or entity "
                                    "(external entities are not allowed)",
                                    detail={"member": name},
                                )
                            tail = window[-_DTD_TAIL:]
            except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
                _reject(f"docx member could not be read: {exc}", detail={"member": name})

            compressed = info.compress_size
            if size:
                ratio = size / compressed if compressed > 0 else float("inf")
                if ratio > DOCX_MAX_COMPRESSION_RATIO:
                    _reject(
                        "docx member compression ratio exceeds the zip-bomb limit",
                        detail={
                            "member": name,
                            "ratio": round(ratio, 3),
                            "limit": DOCX_MAX_COMPRESSION_RATIO,
                        },
                    )
            members[name] = _Member(name=name, size=size, sha256=digest.hexdigest())
            order.append(name)

    if MAIN_PART not in members:
        _reject(
            "docx container has no main document part",
            detail={"expected": MAIN_PART},
        )
    return tuple(order), members, file_digest.hexdigest()


@dataclass(frozen=True)
class _Member:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True, eq=False)
class DocxPackage:
    """An opened, already-validated ``.docx`` container.

    ``member_names`` keeps the archive order; every read goes back to the file
    (never to a cache) and is re-hashed against the digest taken at open time, so
    a container swapped underneath the reader is reported instead of silently
    serving different bytes.
    """

    path: Path
    member_names: tuple[str, ...]
    _members: Mapping[str, _Member] = field(repr=False, compare=False)
    _source_sha256: str = field(repr=False, compare=False)

    def has_part(self, name: str) -> bool:
        return name in self._members

    def member_size(self, name: str) -> int:
        """Decompressed size of ``name`` as measured at open time."""
        member = self._members.get(name)
        if member is None:
            _reject("docx part is not present", detail={"part": name})
        return member.size

    def read_part(self, name: str) -> bytes:
        member = self._members.get(name)
        if member is None:
            _reject("docx part is not present", detail={"part": name})
        try:
            with zipfile.ZipFile(self.path, "r") as archive:
                with archive.open(name, "r") as part:
                    data = part.read(member.size + 1)
        except (KeyError, zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
            _reject(f"docx part could not be read: {exc}", detail={"part": name})
        if len(data) != member.size or hashlib.sha256(data).hexdigest() != member.sha256:
            _reject(
                "docx part changed after it was opened; re-open the source",
                detail={"part": name},
            )
        return data

    def part_sha256(self, name: str) -> str:
        member = self._members.get(name)
        if member is None:
            _reject("docx part is not present", detail={"part": name})
        return member.sha256

    @property
    def source_sha256(self) -> str:
        return self._source_sha256


def open_docx(path: Path | str) -> DocxPackage:
    """Validate and open ``path`` for read-only OOXML access."""
    source = Path(path)
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        _reject(f"docx source does not exist or is unreadable: {exc}", detail={"path": str(source)})
    if not resolved.is_file():
        _reject("docx source is not a regular file", detail={"path": str(resolved)})
    if not zipfile.is_zipfile(resolved):
        _reject("docx source is not a zip container", detail={"path": str(resolved)})

    names, members, digest = _scan_members(resolved)
    return DocxPackage(
        path=resolved,
        member_names=names,
        _members=members,
        _source_sha256=digest,
    )


def parse_xml_part(package: DocxPackage, name: str) -> etree._Element:
    """Parse an XML part with external entities and DTDs disabled.

    Bytes come from :meth:`DocxPackage.read_part`, so the same digest check that
    protects plain reads protects parsing too.
    """
    data = package.read_part(name)
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        huge_tree=False,
        remove_blank_text=False,
    )
    try:
        return etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        _reject(
            f"docx part is not well-formed XML: {exc}",
            detail={"part": name},
        )
