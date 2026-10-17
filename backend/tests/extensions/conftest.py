"""Fixtures for the DOC2MD-19CAP extension tests.

Everything here is synthetic and lives under ``tmp_path``. The tests never touch
the real ``parsed/``, ``raw_vault/`` or ``curated/`` trees, and never write inside
the repository.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = REPO_ROOT / "contracts" / "doc2md-19cap" / "v1.0.0"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"

#: Fixed synthetic content digest used by the parsed-artifact fixtures.
CONTENT_SHA = hashlib.sha256(b"doc2md-19cap-fixture-document").hexdigest()
PARSED_TEXT = "# 示例文档\n\n第一段正文。\n\n| 项目 | 金额 |\n| --- | --- |\n| 日均存款 | 100 |\n"
ASSET_BODY = b"\x89PNG\r\n\x1a\nfixture-asset"
IR_SOURCE_BODY = b'{"fixture":"content_list"}'


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ParsedFixture:
    """A minimal but contract-shaped legacy parsed directory."""

    parsed_root: Path
    content_sha: str
    parsed_dir: Path
    text_path: Path
    manifest_path: Path
    manifest: dict[str, Any]

    def hashes(self) -> dict[str, str]:
        return {
            path.name: sha256_bytes(path.read_bytes())
            for path in sorted(self.parsed_dir.iterdir())
            if path.is_file()
        }


def build_parsed_tree(
    parsed_root: Path,
    *,
    content_sha: str = CONTENT_SHA,
    parsed_text: str = PARSED_TEXT,
    status: str = "SUCCESS",
    with_assets: bool = True,
    with_ir: bool = True,
    asset_body: bytes = ASSET_BODY,
) -> ParsedFixture:
    """Create ``parsed/by_hash/ab/cd/<sha>/`` with the triple + optional extras."""
    parsed_dir = parsed_root / "by_hash" / content_sha[:2] / content_sha[2:4] / content_sha
    (parsed_dir / "images").mkdir(parents=True, exist_ok=True)
    (parsed_dir / "ir_source").mkdir(parents=True, exist_ok=True)

    text_bytes = parsed_text.encode("utf-8")
    (parsed_dir / "parsed_text.md").write_bytes(text_bytes)
    metadata = {
        "parser_name": "markitdown",
        "parser_profile": "markitdown_default_v1",
        "parser_adapter_version": "005_mvp_v1",
        "route_type": "MARKDOWN_TEXT",
        "content_uid": content_sha,
        "sha256": content_sha,
        "source_vault_path": f"/nonexistent/raw_vault/by_hash/{content_sha[:2]}/{content_sha[2:4]}/{content_sha}/original.bin",
        "generated_at": "2026-10-18T00:00:00Z",
        "library_version": "fixture",
    }
    (parsed_dir / "parsed_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    asset_files = []
    if with_assets:
        asset_path = parsed_dir / "images" / "fixture.png"
        asset_path.write_bytes(asset_body)
        asset_files = [
            {
                "relative_path": "images/fixture.png",
                "source_name": "fixture.png",
                "sha256": sha256_bytes(asset_body),
            }
        ]

    ir_source_files = []
    if with_ir:
        ir_source_path = parsed_dir / "ir_source" / "content_list.json"
        ir_source_path.write_bytes(IR_SOURCE_BODY)
        ir_source_files = [
            {
                "name": "content_list.json",
                "relative_path": "ir_source/content_list.json",
                "sha256": sha256_bytes(IR_SOURCE_BODY),
            }
        ]
        (parsed_dir / "document_ir.json").write_text(
            json.dumps({"schema_version": "document-ir/1.0", "fixture": True}), encoding="utf-8"
        )

    manifest = {
        "content_uid": content_sha,
        "sha256": content_sha,
        "route_type": "MARKDOWN_TEXT",
        "parser_name": "markitdown",
        "parser_profile": "markitdown_default_v1",
        "parser_adapter_version": "005_mvp_v1",
        "source_vault_path": metadata["source_vault_path"],
        "parsed_text_path": str(parsed_dir / "parsed_text.md"),
        "parsed_metadata_path": str(parsed_dir / "parsed_metadata.json"),
        "generated_at": "2026-10-18T00:00:00Z",
        "status": status,
        "content_size_bytes": 1234,
        "output_size_bytes": len(text_bytes),
        "output_hash": sha256_bytes(text_bytes),
        "asset_files": asset_files,
        "ir_source_files": ir_source_files,
    }
    manifest_path = parsed_dir / "parse_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return ParsedFixture(
        parsed_root=parsed_root,
        content_sha=content_sha,
        parsed_dir=parsed_dir,
        text_path=parsed_dir / "parsed_text.md",
        manifest_path=manifest_path,
        manifest=manifest,
    )


@pytest.fixture()
def parsed_fixture(tmp_path: Path) -> ParsedFixture:
    parsed_root = tmp_path / "workspace" / "parsed"
    return build_parsed_tree(parsed_root)


@pytest.fixture()
def extension_root(tmp_path: Path) -> Path:
    return tmp_path / "workspace" / "extension_data"


@pytest.fixture()
def extension_config(tmp_path: Path, extension_root: Path) -> Path:
    """A valid extension config pointing at ``extension_root`` inside ``tmp_path``."""
    config_dir = tmp_path / "workspace" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "doc2md-extensions-config/1.0",
        "enabled": True,
        "extension_root": "./extension_data",
        "identity_mode": "local_os_subject",
        "allow_external_network": False,
        "worker": {
            "cpu_parallelism": 2,
            "model_parallelism": 1,
            "hard_timeout_seconds": 180,
            "job_budget_seconds": 600,
            "transport_retry_count": 2,
            "roi_retry_rounds": 1,
            "normalizer_version": "candidate-1.0",
            "profile_ref": None,
            "patchset_ref": None,
        },
        "sources": {"adapter": "local_manifest", "page_size": 100, "access_lease_seconds": None},
        "engines": {
            "structure": {"enabled": False, "provider": "paddle_structure_v3"},
            "chart": {"enabled": False, "provider": "pp_chart2table"},
            "vision": {"enabled": False, "provider": "approved_intranet_service"},
        },
        "policy": {
            "numeric_chart_estimates_enabled": False,
            "require_verified_region_for_exact_visual_claim": True,
            "require_manual_review_for_release": True,
            "allow_bank_approved_label_with_local_identity": False,
        },
        "thresholds": {
            "geometry_roundtrip_epsilon_px": 1.0,
            "token_to_cell_min_coverage": 0.8,
            "token_to_cell_min_margin": 0.15,
        },
        "limits": {
            "max_file_bytes": 104857600,
            "max_docx_expanded_bytes": 536870912,
            "max_docx_members": 10000,
            "max_grid_slots_per_table": 100000,
            "max_image_pixels": 40000000,
        },
        "release": {
            "required_consumers": ["local_candidate_export"],
            "real_bank_cutover_enabled": False,
        },
    }
    path = config_dir / "doc2md_extensions.yaml"
    text = _dump_yaml(payload)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def legacy_app_config(tmp_path: Path, parsed_fixture: ParsedFixture) -> Path:
    """A legacy ``app.yaml`` shape used only to read storage roots (read-only)."""
    workspace = tmp_path / "workspace"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in ("raw_vault", "parsed", "curated", "registry", "quarantine", "reports"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    payload = (
        "app:\n"
        "  pipeline_version: fixture\n"
        "storage:\n"
        "  source_registry_root: ./registry\n"
        "  raw_vault_root: ./raw_vault\n"
        "  parsed_root: ./parsed\n"
        "  curated_root: ./curated\n"
        "  quarantine_root: ./quarantine\n"
        "  reports_root: ./reports\n"
        "mysql:\n"
        "  host: 127.0.0.1\n"
        "  port: 3306\n"
        "  database: fixture\n"
        "  username: fixture\n"
        "  password: fixture\n"
        "  charset: utf8mb4\n"
        "raw:\n"
        "  original_files_readonly: true\n"
        "  copy_unique_content_to_vault: true\n"
    )
    path = config_dir / "app.yaml"
    path.write_text(payload, encoding="utf-8")
    return path


def _dump_yaml(payload: dict[str, Any]) -> str:
    """Tiny YAML writer so fixtures stay readable without depending on PyYAML dumping."""
    import yaml

    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


@pytest.fixture()
def write_json(tmp_path: Path):
    def _write(name: str, payload: Any) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    return _write


@pytest.fixture()
def extension_root_snapshot():
    """Return ``(files, dirs)`` under a root, for "nothing was written" assertions."""

    def _snapshot(root: Path) -> tuple[list[str], list[str]]:
        if not root.exists():
            return ([], [])
        files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        dirs = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_dir())
        return (files, dirs)

    return _snapshot
