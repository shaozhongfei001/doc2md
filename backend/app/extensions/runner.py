"""Thin orchestration for the extension CLI (L01 scope).

What this module is
-------------------
A *thin* layer: it resolves configuration, builds the read-only bridge, computes
identities and assembles a plan/report. It contains no parsing, no OCR, no model
call and no business policy — those arrive in L02+.

What L01 can honestly do
------------------------
* ``--dry-run`` for every contract command performs the real read-only work that
  the command needs to *decide* something (config/root validation, source-version
  contract validation, base-artifact resolution, read-set hashing, provisional
  ``job_key``) and prints the plan. It creates no directory, no lock file and no
  report, and it calls no engine or network endpoint.
* A non-dry-run execution of a command whose engine lands in a later Loop raises
  ``E_NOT_IMPLEMENTED`` (exit 3). It never returns 0 for work it did not do.

The dry-run guard is not decoration: every writer in the store and bridge is
inside ``extension_root`` and the CLI passes a ``WriteGuard`` that raises on the
first attempt, so "dry-run wrote nothing" is an enforced property that the tests
can falsify by mutating the guard.

Honesty about locks
-------------------
A provisional ``job_key`` needs a profile digest and an engine lock digest. When
the configuration does not pin them, the plan reports explicit blockers and the
CLI exits 3. It does not fabricate a digest to look complete.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import ExtensionError
from . import identity
from .contracts import ContractError, validate_root
from .io.config import ExtensionConfig, load_extension_config
from .io.paths import Roots
from .io.read_bridge import ReadBridge, sha256_file

__all__ = ["WriteGuard", "PlanResult", "ExtensionRunner"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class WriteGuard:
    """Blocks every write while ``dry_run`` is set.

    Wired into the CLI so a dry-run cannot allocate a staging directory or take a
    lock even by accident. Tests set ``allow=True`` to simulate a mistaken writer
    and prove the guard actually fires.
    """

    dry_run: bool
    allow: bool = False
    attempts: list[str] = field(default_factory=list)

    def check(self, target: str) -> None:
        if self.dry_run and not self.allow:
            self.attempts.append(target)
            raise ExtensionError(
                "E_DRY_RUN_WRITE_BLOCKED",
                f"dry-run must not write or lock: {target}",
                detail={"attempts": list(self.attempts)},
            )


@dataclass
class PlanResult:
    command: str
    executed: bool
    dry_run: bool
    exit_code: int
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": "doc2md-19cap-plan/1.0",
                "command": self.command,
                "executed": self.executed,
                "dry_run": self.dry_run,
                "exit_code": self.exit_code,
                **self.payload,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


class ExtensionRunner:
    """Resolves config/roots and executes the L01-safe part of each command."""

    def __init__(
        self,
        *,
        extension_config_path: str | os.PathLike[str],
        base_config_path: str | os.PathLike[str] | None = None,
        dry_run: bool = True,
        config_base_dir: Path | None = None,
        legacy_config_loader=None,
    ) -> None:
        self.dry_run = dry_run
        self.guard = WriteGuard(dry_run=dry_run)
        self.config: ExtensionConfig = load_extension_config(
            extension_config_path, base_dir=config_base_dir
        )
        self.legacy_config = self._load_legacy_config(base_config_path, legacy_config_loader)
        self.roots = Roots(
            extension=self.config.extension_root,
            raw_vault=self._legacy_path("raw_vault_root"),
            parsed=self._legacy_path("parsed_root"),
            curated=self._legacy_path("curated_root"),
        )
        self.bridge = ReadBridge(self.roots)

    # ------------------------------------------------------------------ config
    @staticmethod
    def _load_legacy_config(path, loader):
        if path is None:
            return None
        if loader is None:  # pragma: no cover - import guard, exercised indirectly
            from app.core.config import load_config as loader  # noqa: PLC0415
        return loader(Path(path))

    def _legacy_path(self, attribute: str) -> Path | None:
        if self.legacy_config is None:
            return None
        storage = getattr(self.legacy_config, "storage", None)
        if storage is None:
            return None
        value = getattr(storage, attribute, None)
        return Path(value) if value is not None else None

    def store(self, *, injected_fault: str | None = None):
        """Build an ``ArtifactStore`` wired to this run's write guard.

        Every later Loop must obtain its store through here, so a ``--dry-run``
        process cannot create a staging directory, a lock file or a pointer even
        if a code path forgets to check the flag itself.
        """
        from .io.artifact_store import ArtifactStore  # local import keeps CLI startup light

        return ArtifactStore(self.roots, injected_fault=injected_fault, write_guard=self.guard)

    def require_parsed_root(self) -> Path:
        if self.roots.parsed is None:
            raise ExtensionError(
                "E_CONFIG_INVALID",
                "no legacy base config supplied: the read bridge needs storage.parsed_root "
                "(pass --base-config <repo>/config/app.yaml)",
            )
        return self.roots.parsed

    # ------------------------------------------------------------------ blockers
    def _locked_digest(self, reference: str | None) -> tuple[str | None, str | None]:
        """Hash a lock reference; return ``(digest, blocker)``."""
        if not reference:
            return None, "ENGINE_LOCK_NOT_CONFIGURED"
        candidate = Path(reference)
        if not candidate.is_absolute():
            candidate = (self.config.extension_root.parent / candidate).resolve()
        if not candidate.is_file():
            return None, f"ENGINE_LOCK_FILE_MISSING:{reference}"
        return sha256_file(candidate), None

    def _profile_digest(self) -> tuple[str | None, str | None]:
        reference = (self.config.raw.get("worker") or {}).get("profile_ref")
        if not reference:
            return None, "PROFILE_NOT_CONFIGURED"
        candidate = Path(reference)
        if not candidate.is_absolute():
            candidate = (self.config.extension_root.parent / candidate).resolve()
        if not candidate.is_file():
            return None, f"PROFILE_FILE_MISSING:{reference}"
        return sha256_file(candidate), None

    # ------------------------------------------------------------------- plans
    def plan_source_register(self, source_version_file: str) -> PlanResult:
        path = Path(source_version_file)
        if not path.is_file():
            raise ExtensionError(
                "E_INPUT_INVALID", f"source-version payload not found: {source_version_file}"
            )
        try:
            version = validate_root("source-version", path.read_text(encoding="utf-8"))
        except ContractError as exc:
            raise ExtensionError("E_CONTRACT_INVALID", exc.message, detail=exc.errors) from exc

        payload = version.model_dump(mode="json")
        computed = identity.source_version_id(
            {
                "source_id": payload["source_id"],
                "external_object_id": payload["external_object_id"],
                "version_kind": payload["version_kind"],
                "source_revision": payload["source_revision"],
                "snapshot_id": payload["snapshot_id"],
                "content_sha256": payload["content_sha256"],
                "legacy_query_ref_hash": payload["legacy_query_ref"],
            }
        )
        matches = computed == payload["source_version_id"]
        # A plan writes nothing, so the guard is not consulted here; it is wired into
        # ArtifactStore so any future write path still fails closed under --dry-run.
        return PlanResult(
            command="source-register",
            executed=False,
            dry_run=True,
            exit_code=0 if matches else 5,
            payload={
                "plan": "VALIDATE_AND_REGISTER_SOURCE_VERSION",
                "computed_source_version_id": computed,
                "declared_source_version_id": payload["source_version_id"],
                "identity_matches": matches,
                "access_key": identity.source_access_key(
                    payload["source_id"], payload["external_object_id"]
                ),
                "blockers": [] if matches else ["SOURCE_VERSION_ID_MISMATCH"],
                "note": "Registration persistence lands in L02; this plan performs no write.",
            },
        )

    def plan_enhance(
        self,
        content_sha256: str,
        *,
        source_version_ids: list[str] | None = None,
    ) -> PlanResult:
        self.require_parsed_root()
        blockers: list[str] = []
        artifacts = self.bridge.resolve_base(content_sha256)

        profile_sha, profile_blocker = self._profile_digest()
        engine_sha, engine_blocker = self._locked_digest(
            ((self.config.raw.get("engines") or {}).get("structure") or {}).get(
                "exact_environment_lock_ref"
            )
        )
        if profile_blocker:
            blockers.append(profile_blocker)
        if engine_blocker:
            blockers.append(engine_blocker)

        normalizer_version = (self.config.raw.get("worker") or {}).get(
            "normalizer_version", "candidate-1.0"
        )
        patchset_ref = (self.config.raw.get("worker") or {}).get("patchset_ref")
        patchset_sha, patchset_blocker = self._locked_digest(patchset_ref)
        if patchset_blocker:
            blockers.append("PATCHSET_NOT_CONFIGURED" if not patchset_ref else patchset_blocker)

        job_key = None
        if not blockers:
            job_key = identity.job_key(
                {
                    "source_version_ids": identity.sorted_set_values(source_version_ids or []),
                    "content_sha256": content_sha256,
                    "read_set": artifacts.read_set_digest_input(),
                    "access_revisions": {},
                    "profile_sha256": profile_sha,
                    "engine_lock_sha256": engine_sha,
                    "normalizer_version": normalizer_version,
                    "patchset_sha256": patchset_sha,
                }
            )

        return PlanResult(
            command="enhance",
            executed=False,
            dry_run=True,
            exit_code=0 if not blockers else 3,
            payload={
                "plan": "RESOLVE_BASE_AND_PREPARE_GENERATION",
                "content_sha256": content_sha256,
                "parsed_dir": str(artifacts.parsed_dir),
                "read_set": [entry.as_identity_entry() for entry in artifacts.read_set],
                "read_set_size_bytes": sum(e.size_bytes for e in artifacts.read_set),
                "asset_count": artifacts.asset_count,
                "ir_source_count": artifacts.ir_source_count,
                "ir_available": artifacts.ir_available,
                "manifest_parser": artifacts.manifest.get("parser_name"),
                "manifest_status": artifacts.manifest.get("status"),
                "job_key": job_key,
                "normalizer_version": normalizer_version,
                "blockers": blockers,
                "note": "Enhancement engines land in L03+; this plan performs no write and no engine call.",
            },
        )

    def plan_not_implemented(self, command: str, *, stage: str, reason: str) -> None:
        raise ExtensionError(
            "E_NOT_IMPLEMENTED",
            f"{command} is declared in the CLI contract but its engine lands in {stage}; "
            f"refusing to report success. {reason}",
            detail={"command": command, "implemented_in": stage},
        )
