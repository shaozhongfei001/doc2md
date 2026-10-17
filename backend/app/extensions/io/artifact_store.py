"""Atomic artifact store: single writer, staging, durable commit, pointer CAS.

Contract basis: ``01_SDD.md`` §4.1/§13.1, ``02_LOOP_ENGINEERING.md`` T103.

Guarantees
----------
1. **One writer per key.** A ``flock``-based exclusive lock is taken on
   ``<extension_root>/.locks/<key>.lock`` and held across read-check-write, so two
   processes cannot interleave a staging/commit sequence or advance a pointer
   twice.
2. **Nothing half-built is visible.** Payloads are written into
   ``<extension_root>/.staging/<attempt_id>/``, fsynced file-by-file and
   directory-by-directory, and only then renamed into the immutable
   ``objects/…/<generation_id>/`` location. A reader that finds a directory also
   finds a complete manifest.
3. **Compare-and-swap pointers.** ``HEAD.json`` / ``CURRENT.json`` are replaced
   only when the caller's ``expected_current`` matches, via temp-file + fsync +
   rename + parent-directory fsync. A stale writer gets ``E_VERSION_CONFLICT``;
   it is never silently rebased.
4. **Re-runs are idempotent.** Committing the same generation twice returns
   ``ALREADY_COMMITTED``; committing different bytes under the same
   ``generation_id`` is a hard conflict.
5. **Faults are injectable.** ``injected_fault`` raises at named points so tests
   can crash before/after staging, before the manifest, after the rename and
   around the pointer update, then prove recovery leaves no half product.
6. **Recovery never deletes committed work.** Orphan staging directories are
   removed; committed generations are *verified*, and a damaged one is reported
   rather than repaired or dropped.

``fsync`` is called on real directories; on platforms where that is not permitted
this raises, because silently skipping the barrier would make the durability
claim false.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .. import ExtensionError
from .paths import Roots, assert_writable, safe_join
from .read_bridge import sha256_file

__all__ = [
    "FAULT_POINTS",
    "CommitReceipt",
    "CommitResult",
    "StagedFile",
    "FileLock",
    "ArtifactStore",
]

#: Injection points used by the recovery tests.
FAULT_POINTS = (
    "after_staging",
    "before_manifest",
    "after_rename",
    "before_pointer",
    "after_pointer",
)

LOCK_DIR = ".locks"
STAGING_DIR = ".staging"


def _fsync_dir(path: Path) -> None:
    """fsync a directory so a rename inside it survives a crash."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_file_synced(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_dir(path.parent)


@dataclass(frozen=True)
class StagedFile:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CommitReceipt:
    """Result of a successful commit; the payload itself stays immutable."""

    generation_id: str
    directory: str
    manifest_sha256: str
    files: tuple[StagedFile, ...]
    already_committed: bool = False

    @property
    def state(self) -> str:
        return "ALREADY_COMMITTED" if self.already_committed else "COMMITTED"


@dataclass(frozen=True)
class CommitResult:
    receipt: CommitReceipt
    pointer: dict[str, Any] | None = None


@dataclass
class RecoveryReport:
    removed_staging: list[str] = field(default_factory=list)
    verified_generations: list[str] = field(default_factory=list)
    damaged_generations: list[str] = field(default_factory=list)
    orphan_generations: list[str] = field(default_factory=list)


class FileLock:
    """Exclusive, non-blocking-by-default advisory lock scoped to one key."""

    def __init__(self, path: Path, *, timeout: float = 0.0) -> None:
        self.path = path
        self.timeout = timeout
        self._handle = None

    def acquire(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        deadline = time.monotonic() + max(self.timeout, 0.0)
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise ExtensionError(
                        "E_LOCK_BUSY",
                        f"another writer holds the lock for {self.path.name}",
                        detail={"lock": str(self.path)},
                    )
                time.sleep(0.02)
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self.release()


class ArtifactStore:
    """File-ledger store limited to ``extension_root`` (never a database)."""

    def __init__(
        self,
        roots: Roots,
        *,
        injected_fault: str | None = None,
        write_guard: object | None = None,
    ) -> None:
        self.roots = roots
        if injected_fault is not None and injected_fault not in FAULT_POINTS:
            raise ExtensionError(
                "E_INPUT_INVALID", f"unknown fault injection point {injected_fault!r}"
            )
        self.injected_fault = injected_fault
        #: ``runner.WriteGuard`` in the CLI path; every write below asks it first so
        #: a dry-run cannot create a directory, a staging file or a lock by accident.
        self.write_guard = write_guard

    def _ensure_write_allowed(self, target: Path) -> None:
        guard = self.write_guard
        if guard is not None and hasattr(guard, "check"):
            guard.check(str(target))

    # ------------------------------------------------------------------- layout
    @property
    def extension_root(self) -> Path:
        return self.roots.extension

    def lock_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_")
        return self.extension_root / LOCK_DIR / f"{safe_key}.lock"

    def lock(self, key: str, *, timeout: float = 0.0) -> FileLock:
        path = self.lock_path(key)
        self._ensure_write_allowed(path)
        return FileLock(path, timeout=timeout)

    def _fail(self, point: str) -> None:
        if self.injected_fault == point:
            raise ExtensionError(
                "E_COMMIT_INCOMPLETE",
                f"injected crash at {point}; the store must stay consistent",
                detail={"injected_fault": point},
            )

    # ------------------------------------------------------------------ staging
    def new_attempt_id(self) -> str:
        return "attempt-" + uuid.uuid4().hex

    def staging_dir(self, attempt_id: str) -> Path:
        target = safe_join(self.extension_root, f"{STAGING_DIR}/{attempt_id}", field="attempt_id")
        assert_writable(target, self.roots)
        self._ensure_write_allowed(target)
        return target

    def write_staged(self, attempt_id: str, relative_path: str, data: bytes) -> StagedFile:
        target = safe_join(
            self.staging_dir(attempt_id), relative_path, field="staged.relative_path"
        )
        assert_writable(target, self.roots)
        self._ensure_write_allowed(target)
        _write_file_synced(target, data)
        return StagedFile(
            relative_path=relative_path,
            sha256=sha256_file(target),
            size_bytes=len(data),
        )

    def staged_files(self, attempt_id: str) -> list[StagedFile]:
        directory = self.staging_dir(attempt_id)
        if not directory.is_dir():
            return []
        found: list[StagedFile] = []
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                found.append(
                    StagedFile(
                        relative_path=str(path.relative_to(directory)).replace("\\", "/"),
                        sha256=sha256_file(path),
                        size_bytes=path.stat().st_size,
                    )
                )
        return found

    # ------------------------------------------------------------------- commit
    def generation_dir(self, content_sha256: str, generation_id: str) -> Path:
        if len(content_sha256) < 4:
            raise ExtensionError("E_INPUT_INVALID", "content sha too short for path derivation")
        return safe_join(
            self.extension_root,
            f"objects/by_hash/{content_sha256[:2]}/{content_sha256[2:4]}/{content_sha256}/{generation_id}",
            field="generation_dir",
        )

    def commit_generation(
        self,
        attempt_id: str,
        *,
        content_sha256: str,
        generation_id: str,
        expected_files: Mapping[str, str],
        manifest_relative_path: str = "manifest.json",
        lock_key: str | None = None,
    ) -> CommitReceipt:
        """Atomically publish ``.staging/<attempt>`` as an immutable generation.

        ``expected_files`` maps relative path → declared sha256. Anything missing
        or differing aborts the commit *before* the rename, so a reader never sees
        a partial generation.
        """
        staging = self.staging_dir(attempt_id)
        if not staging.is_dir():
            raise ExtensionError(
                "E_COMMIT_INCOMPLETE", f"staging directory does not exist: {attempt_id}"
            )

        key = lock_key or f"generation_{generation_id[:16]}"
        with self.lock(key):
            actual = {item.relative_path: item.sha256 for item in self.staged_files(attempt_id)}
            for relative_path, declared in expected_files.items():
                if relative_path not in actual:
                    raise ExtensionError(
                        "E_COMMIT_INCOMPLETE",
                        f"declared file is missing from staging: {relative_path}",
                    )
                if actual[relative_path] != declared:
                    raise ExtensionError(
                        "E_EVIDENCE_MISMATCH",
                        f"staged file {relative_path} does not match its declared digest",
                        detail={"expected": declared, "actual": actual[relative_path]},
                    )
            if manifest_relative_path not in actual:
                raise ExtensionError(
                    "E_COMMIT_INCOMPLETE",
                    f"manifest {manifest_relative_path} was not staged; refusing to publish",
                )

            target = self.generation_dir(content_sha256, generation_id)
            manifest_sha = actual[manifest_relative_path]

            if target.exists():
                existing_manifest = target / manifest_relative_path
                if not existing_manifest.is_file():
                    raise ExtensionError(
                        "E_VERSION_CONFLICT",
                        f"generation directory exists without a manifest: {target}",
                    )
                if sha256_file(existing_manifest) == manifest_sha:
                    return CommitReceipt(
                        generation_id=generation_id,
                        directory=str(target),
                        manifest_sha256=manifest_sha,
                        files=tuple(StagedFile(p, s, 0) for p, s in sorted(actual.items())),
                        already_committed=True,
                    )
                raise ExtensionError(
                    "E_VERSION_CONFLICT",
                    "the same generation_id already exists with different content",
                    detail={"generation_id": generation_id, "directory": str(target)},
                )

            self._fail("before_manifest")
            _fsync_dir(staging)
            self._fail("after_staging")

            target.parent.mkdir(parents=True, exist_ok=True)
            assert_writable(target, self.roots)
            os.rename(staging, target)
            _fsync_dir(target.parent)
            self._fail("after_rename")

        return CommitReceipt(
            generation_id=generation_id,
            directory=str(target),
            manifest_sha256=manifest_sha,
            files=tuple(StagedFile(p, s, 0) for p, s in sorted(actual.items())),
        )

    # ------------------------------------------------------------- pointer CAS
    def read_pointer(self, relative_path: str) -> dict[str, Any] | None:
        target = safe_join(self.extension_root, relative_path, field="pointer.relative_path")
        if not target.is_file():
            return None
        with target.open("rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ExtensionError("E_COMMIT_INCOMPLETE", f"pointer {relative_path} is not an object")
        return payload

    def compare_and_swap_pointer(
        self,
        relative_path: str,
        *,
        expected_current: str | None,
        new_current: str,
        extra: Mapping[str, Any] | None = None,
        lock_key: str | None = None,
        check_point: str = "before_pointer",
    ) -> CommitResult:
        """Replace a pointer only if it still holds ``expected_current``."""
        target = safe_join(self.extension_root, relative_path, field="pointer.relative_path")
        assert_writable(target, self.roots)
        self._ensure_write_allowed(target)
        key = lock_key or f"pointer_{relative_path.replace('/', '_')}"
        with self.lock(key):
            current_payload = self.read_pointer(relative_path)
            current = None if current_payload is None else current_payload.get("current")
            if current != expected_current:
                raise ExtensionError(
                    "E_VERSION_CONFLICT",
                    "pointer moved since the caller last read it",
                    detail={
                        "pointer": relative_path,
                        "expected_current": expected_current,
                        "actual_current": current,
                    },
                )
            self._fail(check_point)
            epoch = 0 if current_payload is None else int(current_payload.get("epoch", 0))
            payload: dict[str, Any] = {"current": new_current, "epoch": epoch + 1}
            if extra:
                payload.update(extra)
            temp = target.with_suffix(target.suffix + ".tmp")
            _write_file_synced(temp, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            os.replace(temp, target)
            _fsync_dir(target.parent)
            self._fail("after_pointer")
        return CommitResult(
            receipt=CommitReceipt(
                generation_id=new_current,
                directory=str(target),
                manifest_sha256="",
                files=(),
            ),
            pointer=payload,
        )

    # ----------------------------------------------------------------- recovery
    def recover(self) -> RecoveryReport:
        """Remove orphan staging, verify committed generations, never repair silently."""
        report = RecoveryReport()
        staging_root = self.extension_root / STAGING_DIR
        if staging_root.is_dir():
            for entry in sorted(staging_root.iterdir()):
                if entry.is_dir():
                    shutil.rmtree(entry)
                    report.removed_staging.append(entry.name)

        objects_root = self.extension_root / "objects"
        if objects_root.is_dir():
            for manifest in sorted(objects_root.rglob("manifest.json")):
                generation_dir = manifest.parent
                relative = str(generation_dir.relative_to(self.extension_root))
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    report.damaged_generations.append(relative)
                    continue
                declared = {
                    item["file"]["relative_path"]: item["file"]["sha256"]
                    for item in payload.get("files") or []
                    if isinstance(item, dict) and isinstance(item.get("file"), dict)
                }
                intact = True
                for relative_path, declared_sha in declared.items():
                    candidate = generation_dir / relative_path
                    if not candidate.is_file() or sha256_file(candidate) != declared_sha:
                        intact = False
                        break
                (report.verified_generations if intact else report.damaged_generations).append(relative)
        return report

    def load_generation(self, content_sha256: str, generation_id: str) -> tuple[dict[str, Any], Path]:
        """Read a generation only after verifying it against its own manifest."""
        directory = self.generation_dir(content_sha256, generation_id)
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            raise ExtensionError(
                "E_BASE_MISSING", f"generation has no manifest: {directory}"
            )
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("execution_state") != "VALIDATED":
            raise ExtensionError(
                "E_COMMIT_INCOMPLETE",
                f"generation {generation_id} is not in VALIDATED state",
                detail={"execution_state": payload.get("execution_state")},
            )
        declared = {
            item["file"]["relative_path"]: item["file"]["sha256"]
            for item in payload.get("files") or []
            if isinstance(item, dict) and isinstance(item.get("file"), dict)
        }
        for relative_path, declared_sha in declared.items():
            candidate = directory / relative_path
            if not candidate.is_file() or sha256_file(candidate) != declared_sha:
                raise ExtensionError(
                    "E_EVIDENCE_MISMATCH",
                    f"generation {generation_id} artifact {relative_path} is damaged",
                )
        return payload, directory

    def iter_generations(self) -> Iterable[Path]:
        objects_root = self.extension_root / "objects"
        if not objects_root.is_dir():
            return []
        return (p.parent for p in sorted(objects_root.rglob("manifest.json")))
