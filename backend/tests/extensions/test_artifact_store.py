"""Artifact store tests (L01 / T103).

These tests are the durability contract. Each of them fails if the corresponding
guard is weakened:

* a second writer must be locked out (not silently interleaved);
* a half-written staging directory must never become a visible generation;
* a crash at any injected point must leave either "nothing" or "a complete
  generation" — never something in between;
* re-running the same generation must be idempotent, but re-using a generation id
  for different bytes must be a hard conflict;
* the pointer is compare-and-swap, so a stale writer cannot clobber a newer one;
* recovery removes orphans and *reports* damage instead of repairing it silently.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.io.artifact_store import (
    FAULT_POINTS,
    STAGING_DIR,
    ArtifactStore,
    FileLock,
)
from app.extensions.io.paths import Roots

CONTENT_SHA = hashlib.sha256(b"artifact-store-content").hexdigest()
GENERATION_ID = hashlib.sha256(b"generation-1").hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store(tmp_path: Path, **kwargs) -> ArtifactStore:
    root = tmp_path / "ext"
    root.mkdir(parents=True, exist_ok=True)
    return ArtifactStore(Roots(extension=root), **kwargs)


def _manifest(files: dict[str, str], generation_id: str = GENERATION_ID) -> dict:
    return {
        "schema_version": "extension-manifest/1.0",
        "job_key": "a" * 64,
        "generation_id": generation_id,
        "input_manifest": [],
        "producer_locks": [],
        "files": [
            {"artifact_type": "ENHANCEMENT", "file": {"root": "EXTENSION", "relative_path": p, "sha256": s}}
            for p, s in sorted(files.items())
        ],
        "dependency_manifest_sha256": "b" * 64,
        "execution_state": "VALIDATED",
        "created_at": "2026-10-18T00:00:00Z",
    }


def _stage(store: ArtifactStore, attempt: str, payloads: dict[str, bytes]) -> dict[str, str]:
    """Stage payloads plus a manifest that lists them; return declared digests."""
    declared: dict[str, str] = {}
    for relative, body in payloads.items():
        declared[relative] = store.write_staged(attempt, relative, body).sha256
    manifest = _manifest(declared)
    body = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    declared["manifest.json"] = store.write_staged(attempt, "manifest.json", body).sha256
    return declared


# ---------------------------------------------------------------------- locking
def test_second_writer_is_locked_out(tmp_path: Path):
    store = _store(tmp_path)
    with store.lock("objects-key"):
        with pytest.raises(ExtensionError) as excinfo:
            store.lock("objects-key").acquire()
    assert excinfo.value.code == "E_LOCK_BUSY"


def test_lock_is_released_after_the_context(tmp_path: Path):
    store = _store(tmp_path)
    with store.lock("objects-key"):
        pass
    with store.lock("objects-key"):
        pass


def test_lock_file_lives_under_the_extension_root(tmp_path: Path):
    store = _store(tmp_path)
    with store.lock("some/key"):
        assert store.lock_path("some/key").is_file()
        assert store.lock_path("some/key").parent.parent == tmp_path / "ext"


def test_lock_scopes_are_independent(tmp_path: Path):
    store = _store(tmp_path)
    with store.lock("key-a"):
        with store.lock("key-b"):
            pass


# ---------------------------------------------------------------------- staging
def test_staging_is_invisible_before_commit(tmp_path: Path):
    store = _store(tmp_path)
    attempt = store.new_attempt_id()
    _stage(store, attempt, {"enhancement.json": b'{"ok":true}'})

    generation_dir = store.generation_dir(CONTENT_SHA, GENERATION_ID)
    assert not generation_dir.exists()
    assert (tmp_path / "ext" / STAGING_DIR / attempt).is_dir()


def test_commit_publishes_the_generation(tmp_path: Path):
    store = _store(tmp_path)
    attempt = store.new_attempt_id()
    declared = _stage(store, attempt, {"enhancement.json": b'{"ok":true}'})

    receipt = store.commit_generation(
        attempt,
        content_sha256=CONTENT_SHA,
        generation_id=GENERATION_ID,
        expected_files=declared,
    )
    assert receipt.state == "COMMITTED"
    generation_dir = Path(receipt.directory)
    assert (generation_dir / "enhancement.json").is_file()
    assert (generation_dir / "manifest.json").is_file()
    assert not (tmp_path / "ext" / STAGING_DIR / attempt).exists()


def test_re_commit_is_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    first = store.new_attempt_id()
    declared = _stage(store, first, {"enhancement.json": b'{"ok":true}'})
    store.commit_generation(
        first, content_sha256=CONTENT_SHA, generation_id=GENERATION_ID, expected_files=declared
    )
    second = store.new_attempt_id()
    declared_again = _stage(store, second, {"enhancement.json": b'{"ok":true}'})
    receipt = store.commit_generation(
        second,
        content_sha256=CONTENT_SHA,
        generation_id=GENERATION_ID,
        expected_files=declared_again,
    )
    assert receipt.already_committed is True
    assert receipt.state == "ALREADY_COMMITTED"


def test_same_generation_id_with_different_bytes_conflicts(tmp_path: Path):
    store = _store(tmp_path)
    first = store.new_attempt_id()
    declared = _stage(store, first, {"enhancement.json": b'{"ok":true}'})
    store.commit_generation(
        first, content_sha256=CONTENT_SHA, generation_id=GENERATION_ID, expected_files=declared
    )
    second = store.new_attempt_id()
    other = _stage(store, second, {"enhancement.json": b'{"ok":false}'})
    with pytest.raises(ExtensionError) as excinfo:
        store.commit_generation(
            second,
            content_sha256=CONTENT_SHA,
            generation_id=GENERATION_ID,
            expected_files=other,
        )
    assert excinfo.value.code == "E_VERSION_CONFLICT"


def test_commit_refuses_a_missing_declared_file(tmp_path: Path):
    store = _store(tmp_path)
    attempt = store.new_attempt_id()
    declared = _stage(store, attempt, {"enhancement.json": b"{}"})
    declared["missing.json"] = _sha(b"never staged")
    with pytest.raises(ExtensionError) as excinfo:
        store.commit_generation(
            attempt,
            content_sha256=CONTENT_SHA,
            generation_id=GENERATION_ID,
            expected_files=declared,
        )
    assert excinfo.value.code == "E_COMMIT_INCOMPLETE"
    assert not store.generation_dir(CONTENT_SHA, GENERATION_ID).exists()


def test_commit_refuses_a_digest_mismatch(tmp_path: Path):
    store = _store(tmp_path)
    attempt = store.new_attempt_id()
    declared = _stage(store, attempt, {"enhancement.json": b"{}"})
    declared["enhancement.json"] = _sha(b"something else")
    with pytest.raises(ExtensionError) as excinfo:
        store.commit_generation(
            attempt,
            content_sha256=CONTENT_SHA,
            generation_id=GENERATION_ID,
            expected_files=declared,
        )
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"
    assert not store.generation_dir(CONTENT_SHA, GENERATION_ID).exists()


def test_commit_refuses_without_a_manifest(tmp_path: Path):
    store = _store(tmp_path)
    attempt = store.new_attempt_id()
    declared = {"enhancement.json": store.write_staged(attempt, "enhancement.json", b"{}").sha256}
    with pytest.raises(ExtensionError) as excinfo:
        store.commit_generation(
            attempt,
            content_sha256=CONTENT_SHA,
            generation_id=GENERATION_ID,
            expected_files=declared,
        )
    assert excinfo.value.code == "E_COMMIT_INCOMPLETE"
    assert "manifest" in excinfo.value.message


def test_commit_of_an_unknown_attempt_is_refused(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ExtensionError) as excinfo:
        store.commit_generation(
            "attempt-does-not-exist",
            content_sha256=CONTENT_SHA,
            generation_id=GENERATION_ID,
            expected_files={},
        )
    assert excinfo.value.code == "E_COMMIT_INCOMPLETE"


def test_staged_write_outside_the_root_is_refused(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ExtensionError) as excinfo:
        store.write_staged("../escape", "file.json", b"{}")
    assert excinfo.value.code == "E_PATH_ESCAPE"


# ------------------------------------------------------------- crash / recovery
@pytest.mark.parametrize("point", ["before_manifest", "after_staging"])
def test_crash_before_rename_leaves_no_visible_generation(tmp_path: Path, point: str):
    store = _store(tmp_path, injected_fault=point)
    attempt = store.new_attempt_id()
    declared = _stage(store, attempt, {"enhancement.json": b'{"ok":true}'})
    with pytest.raises(ExtensionError) as excinfo:
        store.commit_generation(
            attempt,
            content_sha256=CONTENT_SHA,
            generation_id=GENERATION_ID,
            expected_files=declared,
        )
    assert excinfo.value.code == "E_COMMIT_INCOMPLETE"
    assert not store.generation_dir(CONTENT_SHA, GENERATION_ID).exists()
    assert (tmp_path / "ext" / STAGING_DIR / attempt).is_dir(), "staging is kept for recovery"


def test_crash_after_rename_leaves_a_complete_generation(tmp_path: Path):
    store = _store(tmp_path, injected_fault="after_rename")
    attempt = store.new_attempt_id()
    declared = _stage(store, attempt, {"enhancement.json": b'{"ok":true}'})
    with pytest.raises(ExtensionError):
        store.commit_generation(
            attempt,
            content_sha256=CONTENT_SHA,
            generation_id=GENERATION_ID,
            expected_files=declared,
        )
    # The rename already happened, so the published generation must be complete.
    manifest, directory = store.load_generation(CONTENT_SHA, GENERATION_ID)
    assert manifest["generation_id"] == GENERATION_ID
    assert (directory / "enhancement.json").is_file()


def test_crash_before_pointer_leaves_the_pointer_untouched(tmp_path: Path):
    store = _store(tmp_path, injected_fault="before_pointer")
    with pytest.raises(ExtensionError) as excinfo:
        store.compare_and_swap_pointer("routes/CURRENT.json", expected_current=None, new_current="r1")
    assert excinfo.value.code == "E_COMMIT_INCOMPLETE"
    assert store.read_pointer("routes/CURRENT.json") is None


def test_recovery_removes_orphan_staging_and_keeps_committed_work(tmp_path: Path):
    store = _store(tmp_path)
    attempt = store.new_attempt_id()
    declared = _stage(store, attempt, {"enhancement.json": b'{"ok":true}'})
    store.commit_generation(
        attempt, content_sha256=CONTENT_SHA, generation_id=GENERATION_ID, expected_files=declared
    )
    orphan = store.new_attempt_id()
    store.write_staged(orphan, "partial.json", b"{}")

    report = store.recover()
    assert orphan in report.removed_staging
    assert not (tmp_path / "ext" / STAGING_DIR / orphan).exists()
    assert report.verified_generations, "the committed generation must be verified"
    assert report.damaged_generations == []
    store.load_generation(CONTENT_SHA, GENERATION_ID)


def test_recovery_reports_damage_instead_of_repairing_it(tmp_path: Path):
    store = _store(tmp_path)
    attempt = store.new_attempt_id()
    declared = _stage(store, attempt, {"enhancement.json": b'{"ok":true}'})
    receipt = store.commit_generation(
        attempt, content_sha256=CONTENT_SHA, generation_id=GENERATION_ID, expected_files=declared
    )
    (Path(receipt.directory) / "enhancement.json").write_bytes(b'{"tampered":true}')

    report = store.recover()
    assert report.damaged_generations, "a tampered generation must be reported"
    assert (Path(receipt.directory) / "enhancement.json").is_file(), "never delete on damage"
    with pytest.raises(ExtensionError) as excinfo:
        store.load_generation(CONTENT_SHA, GENERATION_ID)
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"


def test_recovery_on_an_empty_root_is_a_no_op(tmp_path: Path):
    store = _store(tmp_path)
    report = store.recover()
    assert report == report.__class__()


# ------------------------------------------------------------------ pointer CAS
def test_pointer_create_then_swap(tmp_path: Path):
    store = _store(tmp_path)
    first = store.compare_and_swap_pointer(
        "routes/CURRENT.json", expected_current=None, new_current="release-1"
    )
    assert first.pointer["current"] == "release-1"
    assert first.pointer["epoch"] == 1

    second = store.compare_and_swap_pointer(
        "routes/CURRENT.json", expected_current="release-1", new_current="release-2"
    )
    assert second.pointer["current"] == "release-2"
    assert second.pointer["epoch"] == 2


def test_pointer_swap_with_stale_expectation_conflicts(tmp_path: Path):
    store = _store(tmp_path)
    store.compare_and_swap_pointer("routes/CURRENT.json", expected_current=None, new_current="r1")
    with pytest.raises(ExtensionError) as excinfo:
        store.compare_and_swap_pointer(
            "routes/CURRENT.json", expected_current=None, new_current="r2"
        )
    assert excinfo.value.code == "E_VERSION_CONFLICT"
    assert store.read_pointer("routes/CURRENT.json")["current"] == "r1"


def test_pointer_write_outside_the_root_is_refused(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ExtensionError) as excinfo:
        store.compare_and_swap_pointer("../outside.json", expected_current=None, new_current="r1")
    assert excinfo.value.code == "E_PATH_ESCAPE"


def test_pointer_extra_fields_are_preserved(tmp_path: Path):
    store = _store(tmp_path)
    result = store.compare_and_swap_pointer(
        "sources/s1/HEAD.json",
        expected_current=None,
        new_current="g1",
        extra={"source_id": "s1"},
    )
    assert result.pointer == {"current": "g1", "epoch": 1, "source_id": "s1"}


# ------------------------------------------------------------- generation reads
def test_load_generation_requires_validated_state(tmp_path: Path):
    store = _store(tmp_path)
    attempt = store.new_attempt_id()
    declared = _stage(store, attempt, {"enhancement.json": b"{}"})
    store.commit_generation(
        attempt, content_sha256=CONTENT_SHA, generation_id=GENERATION_ID, expected_files=declared
    )
    manifest_path = store.generation_dir(CONTENT_SHA, GENERATION_ID) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["execution_state"] = "RUNNING"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ExtensionError) as excinfo:
        store.load_generation(CONTENT_SHA, GENERATION_ID)
    assert excinfo.value.code == "E_COMMIT_INCOMPLETE"


def test_load_missing_generation_is_base_missing(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ExtensionError) as excinfo:
        store.load_generation(CONTENT_SHA, "9" * 64)
    assert excinfo.value.code == "E_BASE_MISSING"


def test_fault_points_are_a_closed_set(tmp_path: Path):
    root = tmp_path / "ext"
    root.mkdir()
    with pytest.raises(ExtensionError) as excinfo:
        ArtifactStore(Roots(extension=root), injected_fault="not-a-point")
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert set(FAULT_POINTS) == {
        "after_staging",
        "before_manifest",
        "after_rename",
        "before_pointer",
        "after_pointer",
    }


def test_file_lock_can_be_used_directly(tmp_path: Path):
    path = tmp_path / "ext" / ".locks" / "direct.lock"
    with FileLock(path) as lock:
        assert lock.path == path
        assert path.is_file()
