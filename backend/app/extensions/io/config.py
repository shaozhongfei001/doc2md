"""Extension configuration loading.

The shipped example lives at ``config/doc2md_extensions.example.yaml`` and mirrors
``contracts/doc2md-19cap/v1.0.0/config.example.yaml``. Values in it are *engineering
candidates*, not bank-measured thresholds, and it must never contain a real endpoint
or secret.

Fail-closed rules implemented here:

* an unknown top-level key is rejected (a typo must not silently disable a policy);
* ``extension_root`` defaults to ``disabled``: without an explicit value the layer
  will not write anywhere;
* every engine block defaults to ``enabled: false``; enabling an engine without an
  environment lock reference is refused, because "I have the model" is not evidence;
* ``allow_external_network`` and ``real_bank_cutover_enabled`` must stay ``false``
  for this workflow; flipping them needs a separate authorisation, not a config edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import ExtensionError
from .paths import Roots, resolve_extension_root

try:  # PyYAML is a hard dependency of the legacy project
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise ImportError("PyYAML is required to load the extension configuration") from exc

__all__ = [
    "ExtensionConfig",
    "StorageRoots",
    "load_extension_config",
    "load_storage_roots",
    "ALLOWED_TOP_LEVEL_KEYS",
]

ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "enabled",
        "extension_root",
        "identity_mode",
        "allow_external_network",
        "worker",
        "sources",
        "engines",
        "policy",
        "thresholds",
        "limits",
        "release",
    }
)

_CONFIG_SCHEMA = "doc2md-extensions-config/1.0"


@dataclass(frozen=True)
class ExtensionConfig:
    """Validated extension configuration."""

    schema_version: str
    enabled: bool
    extension_root: Path
    identity_mode: str
    allow_external_network: bool
    raw: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None

    def roots(self, *, raw_vault: Path | None = None, parsed: Path | None = None,
              curated: Path | None = None) -> Roots:
        return Roots(extension=self.extension_root, raw_vault=raw_vault,
                     parsed=parsed, curated=curated)

    def require_root(self) -> Path:
        """Create the extension root on demand (never called during ``--dry-run``)."""
        if not self.enabled:
            raise ExtensionError(
                "E_CONFIG_INVALID",
                "extension layer is disabled (enabled: false); refusing to use extension_root",
            )
        self.extension_root.mkdir(parents=True, exist_ok=True)
        return self.extension_root


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExtensionError("E_CONFIG_INVALID", f"{name} must be a mapping")
    return value


def _require_bool(value: Any, name: str, *, expected: bool | None = None) -> bool:
    if not isinstance(value, bool):
        raise ExtensionError("E_CONFIG_INVALID", f"{name} must be a boolean, got {value!r}")
    if expected is not None and value is not expected:
        raise ExtensionError(
            "E_CONFIG_INVALID",
            f"{name} must remain {expected} for workflow DOC2MD-19CAP; "
            "changing it requires a separate authorisation",
        )
    return value


def _check_engine(engines: dict[str, Any], name: str, *, needs_lock_refs: bool) -> None:
    block = engines.get(name)
    if block is None:
        return
    _require_mapping(block, f"engines.{name}")
    enabled = block.get("enabled", False)
    _require_bool(enabled, f"engines.{name}.enabled")
    if not enabled:
        return
    if needs_lock_refs:
        for key in ("exact_environment_lock_ref", "weights_hash_manifest_ref"):
            if not block.get(key):
                raise ExtensionError(
                    "E_CONFIG_INVALID",
                    f"engines.{name}.enabled requires {key}: an engine is not usable "
                    "without a locked environment and verified weights",
                )
    else:
        for key in ("exact_environment_lock_ref", "model_id", "model_revision",
                    "endpoint_config_ref"):
            if not block.get(key):
                raise ExtensionError(
                    "E_CONFIG_INVALID",
                    f"engines.{name}.enabled requires {key}",
                )


def load_extension_config(
    path: str | Path,
    *,
    base_dir: Path | None = None,
    project_root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> ExtensionConfig:
    """Load and validate the extension configuration.

    ``base_dir`` is the directory against which a relative ``extension_root`` is
    resolved; it defaults to the repository root derived from the config location
    (``<repo>/config/<file>``), not the current working directory, so the result
    does not depend on where the CLI was started.

    ``project_root`` is the repository root used to anchor the forbidden write roots.
    It must be passed by the caller when the configuration file may live outside the
    repository, otherwise an absolute ``extension_root`` could point back into
    ``<repo>/sql`` without any component name revealing it
    (defect FAIL-2026-10-18-01).
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ExtensionError("E_CONFIG_INVALID", f"extension config not found: {config_path}")

    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ExtensionError("E_CONFIG_INVALID", f"extension config is not valid YAML: {exc}") from exc

    data = _require_mapping(document, "config")
    if overrides:
        data = {**data, **overrides}

    unknown = set(data) - ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        raise ExtensionError(
            "E_CONFIG_INVALID",
            f"unknown top-level configuration keys: {sorted(unknown)}",
        )

    schema_version = data.get("schema_version")
    if schema_version != _CONFIG_SCHEMA:
        raise ExtensionError(
            "E_CONFIG_INVALID",
            f"schema_version must be {_CONFIG_SCHEMA!r}, got {schema_version!r}",
        )

    enabled = _require_bool(data.get("enabled", False), "enabled")
    allow_external_network = _require_bool(
        data.get("allow_external_network", False), "allow_external_network", expected=False
    )

    policy = _require_mapping(data.get("policy", {}), "policy")
    if policy.get("numeric_chart_estimates_enabled", False):
        # DX-06 / OI05: numeric estimation stays OFF until the Owner decides.
        raise ExtensionError(
            "E_CONFIG_INVALID",
            "policy.numeric_chart_estimates_enabled must stay false until the Owner "
            "decides the numeric-estimate policy (open input OI05 / DX-06)",
        )
    if policy.get("allow_bank_approved_label_with_local_identity", False):
        raise ExtensionError(
            "E_CONFIG_INVALID",
            "local identity can never produce a bank-approved label "
            "(policy.allow_bank_approved_label_with_local_identity must be false)",
        )

    release = _require_mapping(data.get("release", {}), "release")
    _require_bool(
        release.get("real_bank_cutover_enabled", False),
        "release.real_bank_cutover_enabled",
        expected=False,
    )

    engines = _require_mapping(data.get("engines", {}), "engines")
    _check_engine(engines, "structure", needs_lock_refs=True)
    _check_engine(engines, "chart", needs_lock_refs=True)
    _check_engine(engines, "vision", needs_lock_refs=False)

    resolved_base = Path(base_dir) if base_dir is not None else config_path.resolve().parent.parent

    extension_root_value = data.get("extension_root")
    if extension_root_value is None:
        raise ExtensionError("E_CONFIG_INVALID", "extension_root is required")
    extension_root = resolve_extension_root(
        extension_root_value, base_dir=resolved_base, project_root=project_root
    )

    return ExtensionConfig(
        schema_version=schema_version,
        enabled=enabled,
        extension_root=extension_root,
        identity_mode=str(data.get("identity_mode", "local_os_subject")),
        allow_external_network=allow_external_network,
        raw=dict(data),
        source_path=config_path,
    )


@dataclass(frozen=True)
class StorageRoots:
    """Only the legacy ``storage`` section — deliberately no credentials.

    The legacy ``load_config`` parses the ``mysql`` block unconditionally, which
    pulls a plaintext password into the process memory of a component that never
    opens a database connection. This narrow reader keeps the boundary honest and,
    as a side effect, removes the extension layer's only import of legacy code.
    """

    source_registry_root: Path | None = None
    raw_vault_root: Path | None = None
    parsed_root: Path | None = None
    curated_root: Path | None = None
    quarantine_root: Path | None = None
    reports_root: Path | None = None
    config_path: Path | None = None

    def to_kwargs(self) -> dict[str, Path]:
        return {
            "raw_vault": self.raw_vault_root,
            "parsed": self.parsed_root,
            "curated": self.curated_root,
        }


_STORAGE_KEYS = (
    "source_registry_root",
    "raw_vault_root",
    "parsed_root",
    "curated_root",
    "quarantine_root",
    "reports_root",
)


def load_storage_roots(app_yaml_path: str | Path) -> StorageRoots:
    """Read only ``storage.*`` from a legacy ``app.yaml``.

    * Relative values resolve against ``<config_dir>/..`` exactly like the legacy
      loader, so both readers agree on paths.
    * A missing ``storage`` section is a controlled error, not a ``KeyError``.
    * The ``mysql`` section is never read, so no secret enters this process.
    """
    path = Path(app_yaml_path)
    if not path.is_file():
        raise ExtensionError("E_CONFIG_INVALID", f"legacy base config not found: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ExtensionError("E_CONFIG_INVALID", f"legacy base config is not valid YAML: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("storage"), dict):
        raise ExtensionError(
            "E_CONFIG_INVALID",
            f"legacy base config has no storage section: {path}",
        )
    storage = document["storage"]
    base = path.parent.parent
    resolved: dict[str, Path | None] = {}
    for key in _STORAGE_KEYS:
        value = storage.get(key)
        if value is None:
            resolved[key] = None
            continue
        candidate = Path(str(value))
        resolved[key] = candidate if candidate.is_absolute() else (base / candidate).resolve()
    return StorageRoots(config_path=path, **resolved)
