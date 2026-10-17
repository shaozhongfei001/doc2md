from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.yaml"
FALLBACK_CONFIG_PATH = PROJECT_ROOT / "config" / "app.example.yaml"


DEFAULT_SNIFF_CACHE_ROOT = "./sniff_cache"


@dataclass(frozen=True)
class StorageConfig:
    source_registry_root: Path
    raw_vault_root: Path
    parsed_root: Path
    curated_root: Path
    quarantine_root: Path
    reports_root: Path
    # 014：PDF 嗅探派生缓存根（可整体删除重建；未配置时默认 ./sniff_cache）。
    sniff_cache_root: Path = Path(DEFAULT_SNIFF_CACHE_ROOT)


@dataclass(frozen=True)
class MysqlConfig:
    host: str
    port: int
    database: str
    username: str
    password: str
    charset: str


@dataclass(frozen=True)
class RawConfig:
    original_files_readonly: bool
    copy_unique_content_to_vault: bool


@dataclass(frozen=True)
class AppConfig:
    pipeline_version: str
    storage: StorageConfig
    mysql: MysqlConfig
    raw: RawConfig


def _resolve_storage_path(value: str, base: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def load_config(config_path: Path | None = None) -> AppConfig:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.is_file():
        path = FALLBACK_CONFIG_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"Config not found: {DEFAULT_CONFIG_PATH} or {FALLBACK_CONFIG_PATH}"
        )

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    base = path.parent.parent
    storage_data = data["storage"]
    mysql_data = data["mysql"]
    raw_data = data.get("raw", {})
    app_data = data.get("app", {})

    storage = StorageConfig(
        source_registry_root=_resolve_storage_path(
            storage_data["source_registry_root"], base
        ),
        raw_vault_root=_resolve_storage_path(storage_data["raw_vault_root"], base),
        parsed_root=_resolve_storage_path(storage_data["parsed_root"], base),
        curated_root=_resolve_storage_path(storage_data["curated_root"], base),
        quarantine_root=_resolve_storage_path(storage_data["quarantine_root"], base),
        reports_root=_resolve_storage_path(storage_data["reports_root"], base),
        sniff_cache_root=_resolve_storage_path(
            storage_data.get("sniff_cache_root", DEFAULT_SNIFF_CACHE_ROOT), base
        ),
    )
    mysql = MysqlConfig(
        host=mysql_data["host"],
        port=int(mysql_data["port"]),
        database=mysql_data["database"],
        username=mysql_data["username"],
        password=mysql_data["password"],
        charset=mysql_data.get("charset", "utf8mb4"),
    )
    raw = RawConfig(
        original_files_readonly=bool(raw_data.get("original_files_readonly", True)),
        copy_unique_content_to_vault=bool(
            raw_data.get("copy_unique_content_to_vault", True)
        ),
    )
    return AppConfig(
        pipeline_version=str(app_data.get("pipeline_version", "v1.1")),
        storage=storage,
        mysql=mysql,
        raw=raw,
    )


def ensure_readonly(config: AppConfig) -> None:
    if not config.raw.original_files_readonly:
        raise RuntimeError("original_files_readonly must be true")
