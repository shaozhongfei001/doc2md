"""Read-only bridge onto the legacy triple artifacts (T102 read-set + T105).

Why a bridge instead of reuse by import
--------------------------------------
The legacy parsers own their output. This layer must never rewrite
``parsed_text.md``, ``parsed_metadata.json`` or ``parse_manifest.json`` and must
never call the legacy *writer* services (``ParseRegistryService.register_*``,
``EvidenceChainService.build``, …). It only needs the bytes on disk, verified.

What is guaranteed here
-----------------------
* **No implicit empty result.** A missing directory, a missing member, a
  non-``SUCCESS`` manifest or an unreadable manifest all raise
  ``NEEDS_BASE_PARSE`` instead of inventing an empty document.
* **Digest cross-checks.** ``content_uid == sha256 == requested sha``,
  ``output_hash == sha256(parsed_text.md)``, every declared asset and
  ``ir_source`` entry is re-hashed. A mismatch is ``E_EVIDENCE_MISMATCH``; it is
  never downgraded to "use the old cache".
* **Stable read-set.** The same directory yields the same ordered
  ``FileRef`` list, so ``job_key`` is reproducible.
* **Mid-read change detection.** The manifest is read twice; if its digest moved
  while the read-set was being hashed, the caller gets ``E_BASE_MISSING``
  ("the base moved under you") instead of a half-consistent snapshot.
* **Read-only access.** Files are opened ``rb``; the source tree is never opened
  for writing and the original inode is never ``touch``-ed.

Optional IR
-----------
``document_ir.json`` is read only when present. Its absence is recorded as
``ir_available=False``; it is not an error, because IR is a 017 deliverable that
this workflow must not depend on.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .. import ExtensionError
from .paths import Roots, assert_writable, safe_join

__all__ = [
    "TRIPLE_ARTIFACTS",
    "OPTIONAL_IR",
    "BaseArtifacts",
    "ReadSetEntry",
    "ReadBridge",
    "sha256_file",
    "parsed_dir_for",
]

TRIPLE_ARTIFACTS: tuple[str, ...] = (
    "parsed_text.md",
    "parsed_metadata.json",
    "parse_manifest.json",
)
OPTIONAL_IR = "document_ir.json"
_SHA_LEN = 64


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:  # read-only, always
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def parsed_dir_for(parsed_root: Path, content_sha256: str) -> Path:
    """``parsed/by_hash/<ab>/<cd>/<sha>/`` — the legacy derivation, unchanged."""
    if not isinstance(content_sha256, str) or len(content_sha256) != _SHA_LEN:
        raise ExtensionError(
            "E_INPUT_INVALID", f"content sha must be {_SHA_LEN} hex chars, got {content_sha256!r}"
        )
    try:
        int(content_sha256, 16)
    except ValueError as exc:
        raise ExtensionError(
            "E_INPUT_INVALID", f"content sha is not hexadecimal: {content_sha256!r}"
        ) from exc
    return Path(parsed_root) / "by_hash" / content_sha256[:2] / content_sha256[2:4] / content_sha256


@dataclass(frozen=True)
class ReadSetEntry:
    """One verified input byte-range source."""

    root: str
    relative_path: str
    sha256: str
    size_bytes: int

    def as_file_ref(self) -> dict[str, str]:
        return {"root": self.root, "relative_path": self.relative_path, "sha256": self.sha256}

    def as_identity_entry(self) -> dict[str, str]:
        """Ordering-sensitive part of the read-set used by ``job_key``."""
        return {"root": self.root, "relative_path": self.relative_path, "sha256": self.sha256}


@dataclass(frozen=True)
class BaseArtifacts:
    """Verified legacy artifacts for one content sha."""

    content_sha256: str
    parsed_dir: Path
    parsed_text: Path
    parsed_metadata: Path
    parse_manifest: Path
    manifest: dict[str, Any]
    metadata: dict[str, Any]
    read_set: tuple[ReadSetEntry, ...]
    ir_path: Path | None
    ir_available: bool
    asset_count: int
    ir_source_count: int

    def read_set_digest_input(self) -> list[dict[str, str]]:
        """Deterministic read-set payload for ``identity.job_key``."""
        return [entry.as_identity_entry() for entry in self.read_set]


class ReadBridge:
    """Read-only access to the legacy artifacts plus read-set snapshotting."""

    def __init__(self, roots: Roots) -> None:
        if roots.parsed is None:
            raise ExtensionError(
                "E_CONFIG_INVALID", "read bridge requires a configured parsed root"
            )
        self.roots = roots

    # ------------------------------------------------------------------ resolve
    def locate(self, content_sha256: str) -> Path:
        return parsed_dir_for(self.roots.parsed, content_sha256)

    def has_base(self, content_sha256: str) -> bool:
        directory = self.locate(content_sha256)
        return all((directory / name).is_file() for name in TRIPLE_ARTIFACTS)

    def resolve_base(self, content_sha256: str) -> BaseArtifacts:
        """Return verified base artifacts or raise ``NEEDS_BASE_PARSE``."""
        directory = self.locate(content_sha256)
        if not directory.is_dir():
            raise ExtensionError(
                "NEEDS_BASE_PARSE",
                f"no parsed output for content {content_sha256}; run the legacy parse "
                "pipeline first (this layer never invokes it implicitly)",
                detail={"parsed_dir": str(directory)},
            )
        missing = [name for name in TRIPLE_ARTIFACTS if not (directory / name).is_file()]
        if missing:
            raise ExtensionError(
                "NEEDS_BASE_PARSE",
                f"parsed directory is incomplete for {content_sha256}: missing {missing}",
                detail={"parsed_dir": str(directory), "missing": missing},
            )

        manifest_path = directory / "parse_manifest.json"
        manifest_first_hash = sha256_file(manifest_path)
        manifest = self._load_json(manifest_path, "parse_manifest.json")
        metadata = self._load_json(directory / "parsed_metadata.json", "parsed_metadata.json")

        self._check_manifest_identity(manifest, content_sha256, directory)

        text_path = directory / "parsed_text.md"
        text_sha = sha256_file(text_path)
        declared_output = manifest.get("output_hash")
        if declared_output and declared_output != text_sha:
            raise ExtensionError(
                "E_EVIDENCE_MISMATCH",
                "parse_manifest.output_hash does not match parsed_text.md; refusing to "
                "build on a base whose content moved",
                detail={"declared": declared_output, "actual": text_sha},
            )

        entries: list[ReadSetEntry] = []
        for name in TRIPLE_ARTIFACTS:
            path = directory / name
            entries.append(
                ReadSetEntry(
                    root="PARSED",
                    relative_path=self._relative_to_parsed(path),
                    sha256=sha256_file(path),
                    size_bytes=path.stat().st_size,
                )
            )

        asset_count = 0
        for asset in manifest.get("asset_files") or []:
            entry = self._verify_declared(directory, asset, kind="asset")
            entries.append(entry)
            asset_count += 1

        ir_source_count = 0
        for item in manifest.get("ir_source_files") or []:
            entry = self._verify_declared(directory, item, kind="ir_source")
            entries.append(entry)
            ir_source_count += 1

        manifest_second_hash = sha256_file(manifest_path)
        if manifest_second_hash != manifest_first_hash:
            raise ExtensionError(
                "E_BASE_MISSING",
                "parse_manifest.json changed while the read-set was being hashed; the base "
                "is not stable, retry against a settled parse",
            )

        ir_path = directory / OPTIONAL_IR
        ir_available = ir_path.is_file()
        if ir_available:
            entries.append(
                ReadSetEntry(
                    root="PARSED",
                    relative_path=self._relative_to_parsed(ir_path),
                    sha256=sha256_file(ir_path),
                    size_bytes=ir_path.stat().st_size,
                )
            )

        ordered = tuple(sorted(entries, key=lambda e: (e.root, e.relative_path)))
        return BaseArtifacts(
            content_sha256=content_sha256,
            parsed_dir=directory,
            parsed_text=text_path,
            parsed_metadata=directory / "parsed_metadata.json",
            parse_manifest=manifest_path,
            manifest=manifest,
            metadata=metadata,
            read_set=ordered,
            ir_path=ir_path if ir_available else None,
            ir_available=ir_available,
            asset_count=asset_count,
            ir_source_count=ir_source_count,
        )

    # ------------------------------------------------------------------ snapshot
    def snapshot_read_set(
        self,
        artifacts: BaseArtifacts,
        destination: Path,
        *,
        expect_sha256: str | None = None,
    ) -> list[ReadSetEntry]:
        """Copy the verified read-set into ``extension_root`` and re-verify it.

        The copy exists because the legacy ``parsed/`` tree can legitimately be
        rebuilt with ``--force``; a generation must keep the exact bytes it was
        produced from (SDD §17). Copies stay inside ``extension_root`` and are
        still subject to the same source access checks — copying is not a way to
        escape a revocation.
        """
        assert_writable(destination, self.roots)
        if expect_sha256 is not None and expect_sha256 != artifacts.content_sha256:
            raise ExtensionError(
                "E_EVIDENCE_MISMATCH",
                "requested content sha does not match the resolved base artifacts",
            )
        destination.mkdir(parents=True, exist_ok=True)
        copied: list[ReadSetEntry] = []
        for entry in artifacts.read_set:
            source = self.roots.parsed / entry.relative_path
            target = safe_join(destination, entry.relative_path, field="readset.relative_path")
            assert_writable(target, self.roots)
            actual = sha256_file(source)
            if actual != entry.sha256:
                raise ExtensionError(
                    "E_EVIDENCE_MISMATCH",
                    f"source {entry.relative_path} changed before it could be snapshotted",
                    detail={"expected": entry.sha256, "actual": actual},
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied_sha = sha256_file(target)
            if copied_sha != entry.sha256:
                raise ExtensionError(
                    "E_EVIDENCE_MISMATCH",
                    f"snapshot of {entry.relative_path} does not match the source digest",
                    detail={"expected": entry.sha256, "actual": copied_sha},
                )
            copied.append(
                ReadSetEntry(
                    root="EXTENSION",
                    relative_path=str(target.relative_to(self.roots.extension)),
                    sha256=copied_sha,
                    size_bytes=target.stat().st_size,
                )
            )
        return copied

    # ------------------------------------------------------------------ helpers
    def _relative_to_parsed(self, path: Path) -> str:
        return str(Path(path).relative_to(self.roots.parsed)).replace("\\", "/")

    def _load_json(self, path: Path, label: str) -> dict[str, Any]:
        """An unreadable member means there is no usable base, so this is
        ``NEEDS_BASE_PARSE`` (fix the legacy parse), not a silent empty result."""
        try:
            with path.open("rb") as handle:
                payload = json.loads(handle.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExtensionError(
                "NEEDS_BASE_PARSE", f"{label} is unreadable or not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ExtensionError("NEEDS_BASE_PARSE", f"{label} must be a JSON object")
        return payload

    def _check_manifest_identity(
        self, manifest: dict[str, Any], content_sha256: str, directory: Path
    ) -> None:
        status = manifest.get("status")
        if status != "SUCCESS":
            raise ExtensionError(
                "NEEDS_BASE_PARSE",
                f"parse_manifest.status is {status!r}, not SUCCESS; there is no usable base",
                detail={"parsed_dir": str(directory), "status": status},
            )
        identity = manifest.get("content_uid") or manifest.get("sha256")
        if identity != content_sha256:
            raise ExtensionError(
                "E_EVIDENCE_MISMATCH",
                "parse_manifest does not describe the requested content sha",
                detail={"manifest_content_uid": identity, "requested": content_sha256},
            )

    def _verify_declared(
        self, directory: Path, item: Any, *, kind: str
    ) -> ReadSetEntry:
        if not isinstance(item, dict):
            raise ExtensionError(
                "E_EVIDENCE_MISMATCH", f"{kind} entry is not an object: {item!r}"
            )
        relative_path = item.get("relative_path")
        declared = item.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(declared, str):
            raise ExtensionError(
                "E_EVIDENCE_MISMATCH",
                f"{kind} entry lacks relative_path/sha256: {item!r}",
            )
        target = safe_join(directory, relative_path, field=f"{kind}.relative_path")
        if not target.is_file():
            raise ExtensionError(
                "E_EVIDENCE_MISMATCH",
                f"declared {kind} is missing on disk: {relative_path}",
                detail={"declared_sha256": declared},
            )
        actual = sha256_file(target)
        if actual != declared:
            raise ExtensionError(
                "E_EVIDENCE_MISMATCH",
                f"declared {kind} digest does not match the bytes on disk: {relative_path}",
                detail={"expected": declared, "actual": actual},
            )
        return ReadSetEntry(
            root="PARSED",
            relative_path=self._relative_to_parsed(target),
            sha256=actual,
            size_bytes=target.stat().st_size,
        )


def read_set_from_file_refs(refs: Iterable[Any]) -> list[dict[str, str]]:
    """Normalise validated ``FileRef`` objects into an ordered identity payload."""
    payload = [
        {"root": ref.root, "relative_path": ref.relative_path, "sha256": ref.sha256}
        for ref in refs
    ]
    return sorted(payload, key=lambda item: (item["root"], item["relative_path"]))
