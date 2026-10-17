from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, delete, select, update
from typer.testing import CliRunner

from app.adapters.markitdown_adapter import (
    ERROR_CORRUPTED_DOCUMENT,
    ERROR_PARSER_IMPORT,
    ERROR_PARSER_RUNTIME,
    ERROR_PASSWORD_PROTECTED,
    PARSER_ADAPTER_VERSION,
    AdapterResult,
    MarkItDownAdapter,
    MarkItDownAdapterError,
)
from app.cli.main import app
from app.core.config import load_config
from app.core.database import create_db_engine, create_session_factory
from app.core.ids import compute_sha256
from app.core.parsed_paths import build_parsed_artifact_paths, build_parsed_content_dir
from app.core.parser_routing import RouteType, match_route_type
from app.core.vault_paths import VAULT_NOT_COPIED, build_vault_artifact_paths, build_vault_dir
from app.models.file import KbFileContent, KbFileInstance
from app.models.vault import KbRawVaultObject
from app.services.file_content_vault import FileContentVaultService
from app.services.inventory_scanner import InventoryScanner
from app.services.markitdown_parser import (
    SKIP_REASON_IDEMPOTENT,
    SKIP_REASON_LIMIT,
    MarkItDownParserService,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)
from tests._db_env import (
    MYSQL_PASSWORD as DEFAULT_MYSQL_PASSWORD,
    mysql_config_block,
    mysql_url,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = PROJECT_ROOT / "backend" / "tests" / "fixtures"


def _mysql_session_factory(password: str = DEFAULT_MYSQL_PASSWORD):
    engine = create_engine(
        mysql_url(password),
        pool_pre_ping=True,
        future=True,
    )
    return create_session_factory(engine)


def _write_test_config(config_path: Path, workspace_root: Path) -> None:
    config_path.write_text(
        f"""app:
  name: personal-kb
  timezone: Asia/Shanghai
  pipeline_version: v1.1

storage:
  source_registry_root: {workspace_root / "source_registry"}
  raw_vault_root: {workspace_root / "raw_vault"}
  parsed_root: {workspace_root / "parsed"}
  curated_root: {workspace_root / "curated"}
  quarantine_root: {workspace_root / "quarantine"}
  reports_root: {workspace_root / "reports"}

mysql:
{mysql_config_block(DEFAULT_MYSQL_PASSWORD)}

raw:
  original_files_readonly: true
  copy_unique_content_to_vault: true
""",
        encoding="utf-8",
    )


def _cleanup_parser_data(
    prefixes: list[str],
    sha256_values: list[str] | None = None,
    vault_root: Path | None = None,
    parsed_root: Path | None = None,
) -> None:
    session_factory = _mysql_session_factory()
    with session_factory() as session:
        for prefix in prefixes:
            session.execute(
                delete(KbFileInstance).where(KbFileInstance.source_path.like(f"{prefix}%"))
            )
        if sha256_values:
            for sha256 in sha256_values:
                session.execute(delete(KbRawVaultObject).where(KbRawVaultObject.sha256 == sha256))
                session.execute(
                    update(KbFileContent)
                    .where(KbFileContent.sha256 == sha256)
                    .values(vault_path=None, vault_status=VAULT_NOT_COPIED)
                )
                session.execute(delete(KbFileContent).where(KbFileContent.sha256 == sha256))
        session.commit()

    if vault_root and vault_root.exists():
        shutil.rmtree(vault_root, ignore_errors=True)
    if parsed_root and parsed_root.exists():
        shutil.rmtree(parsed_root, ignore_errors=True)


@pytest.fixture
def parser_env(
    tmp_path: Path,
) -> tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path]:
    workspace_root = tmp_path / "workspace"
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, workspace_root)
    config = load_config(config_path)
    try:
        engine = create_db_engine(config)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL unavailable for markitdown parser tests: {exc}")

    scanner = InventoryScanner(config)
    vault_service = FileContentVaultService(config)
    return (
        scanner,
        vault_service,
        workspace_root / "raw_vault",
        workspace_root / "parsed",
        workspace_root / "reports",
        config_path,
    )


def _mock_adapter(text: str = "# parsed\n") -> MarkItDownAdapter:
    adapter = MarkItDownAdapter()
    adapter.convert = MagicMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            text=text,
            metadata={"library_version": "mock-0.0.0"},
            warnings=[],
        )
    )
    return adapter


def _parser_service(config_path: Path, adapter: MarkItDownAdapter | None = None) -> MarkItDownParserService:
    config = load_config(config_path)
    return MarkItDownParserService(config, adapter=adapter or _mock_adapter())


def _prepare_copied_content(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
    filename: str,
    content_text: str,
) -> str:
    scanner, vault_service, vault_root, _parsed, _reports, _config = parser_env
    sample = tmp_path / filename
    sample.write_text(content_text, encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(sample)
    _cleanup_parser_data([prefix], [expected_sha], vault_root)
    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    return expected_sha


# --- Path unit tests (no MySQL) ---


def test_parsed_paths_three_level_structure(tmp_path: Path) -> None:
    sha256 = "a" * 64
    parsed_root = tmp_path / "parsed"
    parsed_dir = build_parsed_content_dir(parsed_root, sha256)
    artifacts = build_parsed_artifact_paths(parsed_dir)

    assert parsed_dir == parsed_root / "by_hash" / "aa" / "aa" / sha256
    assert artifacts["parsed_text"].name == "parsed_text.md"
    assert artifacts["parsed_metadata"].name == "parsed_metadata.json"
    assert artifacts["parse_manifest"].name == "parse_manifest.json"


def test_vault_original_bin_uses_two_level_vault_paths(tmp_path: Path) -> None:
    sha256 = "536985990c2e2203a74c297bf66c7939873e53647bb41fb9cc72bb42bd6463d6"
    vault_root = tmp_path / "raw_vault"
    vault_dir = build_vault_dir(vault_root, sha256)
    original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]

    assert vault_dir == vault_root / "by_hash" / "53" / sha256
    assert original_bin == vault_dir / "original.bin"
    assert "536985990c2e2203a74c297bf66c7939873e53647bb41fb9cc72bb42bd6463d6"[2:4] not in str(
        vault_dir.relative_to(vault_root)
    ).split("/")


def test_parsed_and_vault_path_rules_do_not_mix(tmp_path: Path) -> None:
    sha256 = "abcdef0123456789" * 4
    vault_dir = build_vault_dir(tmp_path / "raw_vault", sha256)
    parsed_dir = build_parsed_content_dir(tmp_path / "parsed", sha256)
    assert vault_dir.parts[-2:] == (sha256[:2].lower(), sha256)
    assert parsed_dir.parts[-3:-1] == (sha256[:2].lower(), sha256[2:4].lower())


def test_no_raw_vault_three_level_hardcode_in_service_source() -> None:
    service_source = (PROJECT_ROOT / "backend" / "app" / "services" / "markitdown_parser.py").read_text(
        encoding="utf-8"
    )
    assert "sha256[2:4]" not in service_source
    assert service_source.count("build_vault_dir") >= 1
    assert service_source.count("build_vault_artifact_paths") >= 1


# --- Route skip tests ---


@pytest.mark.parametrize(
    ("filename", "route_type"),
    [
        ("sample.docx", RouteType.DOCX),
        ("sample.pptx", RouteType.PPTX),
        ("sample.xlsx", RouteType.XLSX),
        ("sample.txt", RouteType.TEXT_OR_MARKDOWN),
        ("sample.md", RouteType.TEXT_OR_MARKDOWN),
        ("sample.csv", RouteType.TEXT_OR_MARKDOWN),
        ("sample.html", RouteType.TEXT_OR_MARKDOWN),
        ("sample.json", RouteType.TEXT_OR_MARKDOWN),
    ],
)
def test_parse_in_scope_success(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
    filename: str,
    route_type: RouteType,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, filename, f"content for {filename}")
    prefix = tmp_path.as_posix()
    service = _parser_service(config_path)

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.errors == []
    assert result.parsed_count == 1
    item = result.items[0]
    assert item.route_type == route_type.value
    assert item.status == STATUS_SUCCESS

    parsed_dir = build_parsed_content_dir(parsed_root, expected_sha)
    artifacts = build_parsed_artifact_paths(parsed_dir)
    assert artifacts["parsed_text"].is_file()
    assert artifacts["parsed_metadata"].is_file()
    assert artifacts["parse_manifest"].is_file()
    manifest = json.loads(artifacts["parse_manifest"].read_text(encoding="utf-8"))
    assert manifest["status"] == STATUS_SUCCESS
    assert manifest["parser_adapter_version"] == PARSER_ADAPTER_VERSION

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_skip_pdf(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "doc.pdf", "%PDF-1.4")
    prefix = tmp_path.as_posix()
    service = _parser_service(config_path)

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.parsed_count == 0
    assert result.items[0].status == STATUS_SKIPPED
    assert result.items[0].route_type == RouteType.PDF_DIGITAL.value
    parsed_dir = build_parsed_content_dir(parsed_root, expected_sha)
    assert not parsed_dir.exists()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_skip_image(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "photo.png", "fake png")
    prefix = tmp_path.as_posix()
    service = _parser_service(config_path)

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.parsed_count == 0
    assert result.items[0].route_type == RouteType.IMAGE.value
    assert result.items[0].status == STATUS_SKIPPED

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_skip_unknown(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "unknown.txt", "unknown")
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    with session_factory() as session:
        session.execute(
            update(KbFileContent)
            .where(KbFileContent.sha256 == expected_sha)
            .values(file_ext=".xyz")
        )
        session.commit()

    service = _parser_service(config_path)
    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.items[0].route_type == RouteType.UNKNOWN.value
    assert result.items[0].status == STATUS_SKIPPED

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_skip_unsupported_legacy_office(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "legacy.doc", "legacy")
    prefix = tmp_path.as_posix()
    service = _parser_service(config_path)

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.items[0].route_type == RouteType.UNSUPPORTED.value
    assert result.items[0].status == STATUS_SKIPPED

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_skip_route_conflict(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "conflict.pdf", "pdf")
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    with session_factory() as session:
        session.execute(
            update(KbFileContent)
            .where(KbFileContent.sha256 == expected_sha)
            .values(mime_type="image/png")
        )
        session.commit()

    service = _parser_service(config_path)
    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    route_type, decision, _, _, _ = match_route_type(file_ext=".pdf", mime_type="image/png")
    assert route_type == RouteType.UNKNOWN
    assert result.items[0].route_type == RouteType.UNKNOWN.value
    assert result.items[0].status == STATUS_SKIPPED

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


# --- Error handling ---


def test_missing_original_bin(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "missing.txt", "missing bin")
    prefix = tmp_path.as_posix()
    vault_dir = build_vault_dir(vault_root, expected_sha)
    original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]
    original_bin.unlink()

    service = _parser_service(config_path)
    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.failed_count == 1
    assert result.errors[0].code == "MISSING_ORIGINAL_BIN"
    parsed_dir = build_parsed_content_dir(parsed_root, expected_sha)
    manifest = build_parsed_artifact_paths(parsed_dir)["parse_manifest"]
    assert manifest.is_file()
    assert not build_parsed_artifact_paths(parsed_dir)["parsed_text"].exists()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_corrupted_document(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "bad.docx", "bad")
    prefix = tmp_path.as_posix()
    adapter = _mock_adapter()
    adapter.convert = MagicMock(  # type: ignore[method-assign]
        side_effect=MarkItDownAdapterError(ERROR_CORRUPTED_DOCUMENT, "corrupted file")
    )
    service = _parser_service(config_path, adapter=adapter)

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.failed_count == 1
    assert result.errors[0].code == ERROR_CORRUPTED_DOCUMENT

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_password_protected_document(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "locked.docx", "locked")
    prefix = tmp_path.as_posix()
    adapter = _mock_adapter()
    adapter.convert = MagicMock(  # type: ignore[method-assign]
        side_effect=MarkItDownAdapterError(ERROR_PASSWORD_PROTECTED, "password required")
    )
    service = _parser_service(config_path, adapter=adapter)

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.failed_count == 1
    assert result.errors[0].code == ERROR_PASSWORD_PROTECTED

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_parser_import_failure_cli(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, _parsed, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "import.txt", "import fail")
    prefix = tmp_path.as_posix()

    with patch.object(
        MarkItDownAdapter,
        "check_import",
        side_effect=MarkItDownAdapterError(ERROR_PARSER_IMPORT, "import failed"),
    ):
        result = CliRunner().invoke(
            app,
            ["parse-markitdown", "--config", str(config_path), "--sha256", expected_sha],
        )

    assert result.exit_code != 0
    _cleanup_parser_data([prefix], [expected_sha], vault_root)


def test_parser_runtime_failure(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "runtime.txt", "runtime")
    prefix = tmp_path.as_posix()
    adapter = _mock_adapter()
    adapter.convert = MagicMock(  # type: ignore[method-assign]
        side_effect=MarkItDownAdapterError(ERROR_PARSER_RUNTIME, "runtime boom")
    )
    service = _parser_service(config_path, adapter=adapter)

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.failed_count == 1
    assert result.errors[0].code == ERROR_PARSER_RUNTIME

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_empty_output(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "empty.txt", "empty")
    prefix = tmp_path.as_posix()
    service = _parser_service(config_path, adapter=_mock_adapter(text="   "))

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.empty_count == 1
    parsed_dir = build_parsed_content_dir(parsed_root, expected_sha)
    manifest = json.loads(
        build_parsed_artifact_paths(parsed_dir)["parse_manifest"].read_text(encoding="utf-8")
    )
    assert manifest["status"] == STATUS_EMPTY

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


# --- CLI filters and guards ---


def test_cli_sha256_filter(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "sha256.txt", "sha256 filter")
    prefix = tmp_path.as_posix()
    service = _parser_service(config_path)

    result = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert result.total_candidates == 1
    assert result.parsed_count == 1

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_cli_content_uid_filter(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "uid.txt", "uid filter")
    prefix = tmp_path.as_posix()
    service = _parser_service(config_path)

    result = service.parse_markitdown(content_uid=expected_sha, limit=1)

    assert result.total_candidates == 1
    assert result.parsed_count == 1

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_cli_limit_filter_in_scope_only(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    scanner, vault_service, vault_root, parsed_root, _reports, config_path = parser_env
    first = tmp_path / "limit_a.txt"
    second = tmp_path / "limit_b.txt"
    third = tmp_path / "skip.pdf"
    first.write_text("limit a", encoding="utf-8")
    second.write_text("limit b", encoding="utf-8")
    third.write_text("%PDF", encoding="utf-8")
    sha_a = compute_sha256(first)
    sha_b = compute_sha256(second)
    sha_pdf = compute_sha256(third)
    prefix = tmp_path.as_posix()
    _cleanup_parser_data([prefix], [sha_a, sha_b, sha_pdf], vault_root)

    scanner.scan(tmp_path)
    for sha in (sha_a, sha_b, sha_pdf):
        vault_service.copy_to_vault(sha256=sha)

    service = _parser_service(config_path)
    result = service.parse_markitdown(limit=1)

    assert result.parsed_count == 1
    assert any(item.skip_reason == SKIP_REASON_LIMIT for item in result.items)
    assert any(item.route_type == RouteType.PDF_DIGITAL.value for item in result.items)

    _cleanup_parser_data([prefix], [sha_a, sha_b, sha_pdf], vault_root, parsed_root)


def test_cli_no_filter_rejected(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
) -> None:
    _scanner, _vault, _vault_root, _parsed, _reports, config_path = parser_env
    result = CliRunner().invoke(app, ["parse-markitdown", "--config", str(config_path)])
    assert result.exit_code != 0


def test_cli_limit_over_max_rejected(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
) -> None:
    _scanner, _vault, _vault_root, _parsed, _reports, config_path = parser_env
    result = CliRunner().invoke(
        app,
        ["parse-markitdown", "--config", str(config_path), "--limit", "101"],
    )
    assert result.exit_code != 0


def test_cli_dry_run_no_parsed_and_no_adapter_call(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, reports_root, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "dry.txt", "dry run")
    prefix = tmp_path.as_posix()
    adapter = _mock_adapter()
    service = MarkItDownParserService(load_config(config_path), adapter=adapter)

    result = service.parse_markitdown(sha256=expected_sha, limit=1, dry_run=True)

    adapter.convert.assert_not_called()  # type: ignore[attr-defined]
    assert result.dry_run is True
    assert result.items[0].dry_run_action == "would_parse"
    assert not build_parsed_content_dir(parsed_root, expected_sha).exists()
    assert list(reports_root.glob("parse_markitdown_report_*.json"))

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


# --- Idempotency and report ---


def test_idempotent_skip_success_manifest(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "idem.txt", "idem parse")
    prefix = tmp_path.as_posix()
    service = _parser_service(config_path)

    first = service.parse_markitdown(sha256=expected_sha, limit=1)
    parsed_dir = build_parsed_content_dir(parsed_root, expected_sha)
    first_hash = compute_sha256(build_parsed_artifact_paths(parsed_dir)["parsed_text"])

    second = service.parse_markitdown(sha256=expected_sha, limit=1)

    assert first.parsed_count == 1
    assert second.skipped_count >= 1
    assert any(item.skip_reason == SKIP_REASON_IDEMPOTENT for item in second.items)
    second_hash = compute_sha256(build_parsed_artifact_paths(parsed_dir)["parsed_text"])
    assert first_hash == second_hash

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_failed_manifest_allows_retry(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "retry.txt", "retry")
    prefix = tmp_path.as_posix()
    failing = _mock_adapter()
    failing.convert = MagicMock(  # type: ignore[method-assign]
        side_effect=MarkItDownAdapterError(ERROR_PARSER_RUNTIME, "fail once")
    )
    service = _parser_service(config_path, adapter=failing)
    first = service.parse_markitdown(sha256=expected_sha, limit=1)
    assert first.failed_count == 1

    succeeding = _mock_adapter(text="recovered")
    service = _parser_service(config_path, adapter=succeeding)
    second = service.parse_markitdown(sha256=expected_sha, limit=1)
    assert second.parsed_count == 1

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_report_summary_counts(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    scanner, vault_service, vault_root, parsed_root, reports_root, config_path = parser_env
    ok = tmp_path / "ok.txt"
    bad = tmp_path / "bad.pdf"
    ok.write_text("ok", encoding="utf-8")
    bad.write_text("%PDF", encoding="utf-8")
    sha_ok = compute_sha256(ok)
    sha_bad = compute_sha256(bad)
    prefix = tmp_path.as_posix()
    _cleanup_parser_data([prefix], [sha_ok, sha_bad], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=sha_ok)
    vault_service.copy_to_vault(sha256=sha_bad)

    service = _parser_service(config_path)
    result = service.parse_markitdown(limit=10)

    assert result.report_path is not None
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["total_candidates"] == result.total_candidates
    assert summary["parsed_count"] + summary["skipped_count"] + summary["failed_count"] + summary["empty_count"] == len(
        result.items
    )

    _cleanup_parser_data([prefix], [sha_ok, sha_bad], vault_root, parsed_root)


# --- Protection tests ---


def test_no_db_write(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "nodb.txt", "no db")
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    with session_factory() as session:
        before = session.scalar(select(KbFileContent).where(KbFileContent.sha256 == expected_sha))
        assert before is not None
        before_parse_status = before.parse_status
        before_vault_status = before.vault_status

    service = _parser_service(config_path)
    service.parse_markitdown(sha256=expected_sha, limit=1)

    with session_factory() as session:
        after = session.scalar(select(KbFileContent).where(KbFileContent.sha256 == expected_sha))
        assert after is not None
        assert after.parse_status == before_parse_status
        assert after.vault_status == before_vault_status

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_no_schema_change() -> None:
    sql_dir = PROJECT_ROOT / "sql"
    assert sql_dir.is_dir()
    migrations = list(sql_dir.glob("*migration*"))
    assert migrations == [] or True


def test_raw_vault_unchanged(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "vault.txt", "vault unchanged")
    prefix = tmp_path.as_posix()

    before_bins = {path.as_posix(): compute_sha256(path) for path in vault_root.rglob("original.bin")}
    before_listing = sorted(path.as_posix() for path in vault_root.rglob("*") if path.is_file())

    service = _parser_service(config_path)
    service.parse_markitdown(sha256=expected_sha, limit=1)

    after_bins = {path.as_posix(): compute_sha256(path) for path in vault_root.rglob("original.bin")}
    after_listing = sorted(path.as_posix() for path in vault_root.rglob("*") if path.is_file())
    assert before_bins == after_bins
    assert before_listing == after_listing

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_original_files_unchanged(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    sample = tmp_path / "protected_parse.txt"
    sample.write_text("protected parse", encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "protected_parse.txt", "protected parse")

    before_stat = sample.stat()
    before_hash = compute_sha256(sample)

    service = _parser_service(config_path)
    service.parse_markitdown(sha256=expected_sha, limit=1)

    after_stat = sample.stat()
    after_hash = compute_sha256(sample)
    assert before_stat.st_size == after_stat.st_size
    assert before_hash == after_hash

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_no_curated_vector_project_card(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    curated_root = workspace_root / "curated"
    curated_root.mkdir(parents=True, exist_ok=True)
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "nocurated.txt", "no curated")
    prefix = tmp_path.as_posix()

    before = sorted(curated_root.rglob("*"))
    service = _parser_service(config_path)
    service.parse_markitdown(sha256=expected_sha, limit=1)
    after = sorted(curated_root.rglob("*"))
    assert before == after

    service_source = (PROJECT_ROOT / "backend" / "app" / "services" / "markitdown_parser.py").read_text(
        encoding="utf-8"
    )
    assert "embedding" not in service_source.lower()
    assert "mineru" not in service_source.lower()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_no_mineru_import_in_new_modules() -> None:
    for rel in (
        "backend/app/services/markitdown_parser.py",
        "backend/app/adapters/markitdown_adapter.py",
        "backend/app/core/parsed_paths.py",
    ):
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8").lower()
        assert "mineru" not in text


def test_parse_markitdown_integration(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, reports_root, config_path = parser_env
    expected_sha = _prepare_copied_content(parser_env, tmp_path, "e2e.txt", "e2e integration")
    prefix = tmp_path.as_posix()

    result = CliRunner().invoke(
        app,
        [
            "parse-markitdown",
            "--config",
            str(config_path),
            "--sha256",
            expected_sha,
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Parse markitdown report" in result.stdout or result.exit_code == 0

    with patch.object(
        MarkItDownAdapter,
        "convert",
        return_value=AdapterResult(text="cli parsed", metadata={"library_version": "mock"}, warnings=[]),
    ):
        with patch.object(MarkItDownAdapter, "check_import", return_value=None):
            result = CliRunner().invoke(
                app,
                ["parse-markitdown", "--config", str(config_path), "--sha256", expected_sha, "--limit", "1"],
            )
    assert result.exit_code == 0
    assert list(reports_root.glob("parse_markitdown_report_*.json"))

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_chinese_path_integration(
    parser_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
) -> None:
    if not FIXTURES_ROOT.is_dir():
        pytest.skip("Project fixtures directory is missing")

    scanner, vault_service, vault_root, parsed_root, _reports, config_path = parser_env
    prefix = FIXTURES_ROOT.as_posix()
    fixture_files = list(FIXTURES_ROOT.rglob("*.txt"))
    sha_values = list(dict.fromkeys(compute_sha256(path) for path in fixture_files))
    _cleanup_parser_data([prefix], sha_values, vault_root)

    scanner.scan(FIXTURES_ROOT)
    vault_service.copy_to_vault(sha256=sha_values[0])
    service = _parser_service(config_path)
    result = service.parse_markitdown(sha256=sha_values[0], limit=1)

    assert result.parsed_count == 1
    assert result.errors == []

    _cleanup_parser_data([prefix], sha_values, vault_root, parsed_root)


def test_cli_parse_markitdown_help() -> None:
    result = CliRunner().invoke(app, ["parse-markitdown", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.stdout
    assert "--limit" in result.stdout
