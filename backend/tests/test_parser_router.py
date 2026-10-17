from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, select, update

from app.core.config import load_config
from app.core.database import create_db_engine, create_session_factory
from app.core.ids import compute_sha256
from app.core.parser_routing import (
    FutureParserHint,
    RouteType,
    match_route_type,
)
from app.core.vault_paths import VAULT_NOT_COPIED
from app.models.file import KbFileContent, KbFileInstance
from app.models.vault import KbRawVaultObject
from app.services.file_content_vault import FileContentVaultService
from app.services.inventory_scanner import InventoryScanner
from app.services.parser_router import ParserRouterService
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


def _cleanup_route_data(
    prefixes: list[str],
    sha256_values: list[str] | None = None,
    vault_root: Path | None = None,
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


@pytest.fixture
def router_env(
    tmp_path: Path,
) -> tuple[InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path]:
    workspace_root = tmp_path / "workspace"
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, workspace_root)
    config = load_config(config_path)
    try:
        engine = create_db_engine(config)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL unavailable for parser router tests: {exc}")

    scanner = InventoryScanner(config)
    vault_service = FileContentVaultService(config)
    router_service = ParserRouterService(config)
    return (
        scanner,
        vault_service,
        router_service,
        workspace_root / "raw_vault",
        workspace_root / "reports",
    )


# --- Pure routing rule tests (no MySQL) ---


def test_match_route_txt_to_text_or_markdown() -> None:
    route_type, decision, rule_name, hint, _reason = match_route_type(
        file_ext=".txt", mime_type="text/plain"
    )
    assert route_type == RouteType.TEXT_OR_MARKDOWN
    assert decision == "ROUTE"
    assert rule_name == "ext_text"
    assert hint == FutureParserHint.DIRECT_TEXT.value


def test_match_route_md_to_text_or_markdown() -> None:
    route_type, decision, _, hint, _ = match_route_type(file_ext=".md", mime_type=None)
    assert route_type == RouteType.TEXT_OR_MARKDOWN
    assert decision == "ROUTE"
    assert hint == FutureParserHint.DIRECT_TEXT.value


def test_match_route_office_ext() -> None:
    for ext, expected in [
        (".docx", RouteType.DOCX),
        (".pptx", RouteType.PPTX),
        (".xlsx", RouteType.XLSX),
    ]:
        route_type, decision, _, hint, _ = match_route_type(file_ext=ext, mime_type=None)
        assert route_type == expected
        assert decision == "ROUTE"
        assert hint == FutureParserHint.MARKITDOWN_FAMILY.value


def test_match_route_pdf_to_pdf_digital() -> None:
    route_type, decision, rule_name, hint, _ = match_route_type(
        file_ext=".pdf", mime_type="application/pdf"
    )
    assert route_type == RouteType.PDF_DIGITAL
    assert decision == "ROUTE"
    assert rule_name == "ext_pdf_digital"
    assert hint == FutureParserHint.MINERU_FAMILY.value


def test_match_route_image_ext() -> None:
    route_type, decision, _, hint, _ = match_route_type(file_ext=".png", mime_type="image/png")
    assert route_type == RouteType.IMAGE
    assert decision == "ROUTE"
    assert hint == FutureParserHint.MINERU_FAMILY.value


def test_match_route_legacy_office_unsupported() -> None:
    route_type, decision, rule_name, hint, _ = match_route_type(file_ext=".doc", mime_type=None)
    assert route_type == RouteType.UNSUPPORTED
    assert decision == "UNSUPPORTED"
    assert rule_name == "ext_legacy_office"
    assert hint == FutureParserHint.NONE.value


def test_match_route_unknown_missing_ext() -> None:
    route_type, decision, rule_name, hint, _ = match_route_type(
        file_ext=None, mime_type=None, fallback_ext=None
    )
    assert route_type == RouteType.UNKNOWN
    assert decision == "UNKNOWN"
    assert rule_name == "ext_missing"
    assert hint == FutureParserHint.NONE.value


def test_match_route_pdf_image_mime_conflict_unknown() -> None:
    route_type, decision, rule_name, hint, reason = match_route_type(
        file_ext=".pdf", mime_type="image/png"
    )
    assert route_type == RouteType.UNKNOWN
    assert decision == "UNKNOWN"
    assert rule_name == "ext_mime_conflict"
    assert hint == FutureParserHint.NONE.value
    assert "conflicts" in reason


# --- MySQL integration tests ---


def _prepare_copied_content(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
    filename: str,
    content_text: str,
) -> str:
    scanner, vault_service, _router, vault_root, _reports = router_env
    sample = tmp_path / filename
    sample.write_text(content_text, encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(sample)
    _cleanup_route_data([prefix], [expected_sha], vault_root)
    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    return expected_sha


def test_route_project_fixtures_integration(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
) -> None:
    if not FIXTURES_ROOT.is_dir():
        pytest.skip("Project fixtures directory is missing")

    scanner, vault_service, router_service, vault_root, reports_root = router_env
    prefix = FIXTURES_ROOT.as_posix()
    fixture_files = list(FIXTURES_ROOT.rglob("*.txt"))
    sha_values = list(dict.fromkeys(compute_sha256(path) for path in fixture_files))
    _cleanup_route_data([prefix], sha_values, vault_root)

    scanner.scan(FIXTURES_ROOT)
    vault_result = vault_service.copy_to_vault(sha256=sha_values[0])
    assert vault_result.errors == []

    result = router_service.route_parsers(sha256=sha_values[0])

    assert result.routed >= 1
    assert result.errors == []
    assert result.report_path is not None
    assert result.report_path.exists()

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "parser_route_report"
    assert payload["summary"]["routed"] >= 1
    decision = payload["decisions"][0]
    assert decision["route_type"] == RouteType.TEXT_OR_MARKDOWN.value
    assert decision["future_parser_hint"] == FutureParserHint.DIRECT_TEXT.value
    assert "future_parser_hint" in decision
    assert "suggested_parser" not in payload["decisions"][0]

    _cleanup_route_data([prefix], sha_values, vault_root)


def test_route_sha256_filter(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    _scanner, _vault, router_service, vault_root, _reports = router_env
    expected_sha = _prepare_copied_content(router_env, tmp_path, "filter.txt", "filter me")
    prefix = tmp_path.as_posix()

    result = router_service.route_parsers(sha256=expected_sha)

    assert result.candidates == 1
    assert result.routed == 1
    assert result.decisions[0].sha256 == expected_sha

    _cleanup_route_data([prefix], [expected_sha], vault_root)


def test_route_content_uid_filter(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    _scanner, _vault, router_service, vault_root, _reports = router_env
    expected_sha = _prepare_copied_content(router_env, tmp_path, "uid.txt", "uid filter")
    prefix = tmp_path.as_posix()

    result = router_service.route_parsers(content_uid=expected_sha)

    assert result.candidates == 1
    assert result.routed == 1

    _cleanup_route_data([prefix], [expected_sha], vault_root)


def test_route_limit(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, router_service, vault_root, _reports = router_env
    first = tmp_path / "limit_a.txt"
    second = tmp_path / "limit_b.txt"
    first.write_text("limit a", encoding="utf-8")
    second.write_text("limit b", encoding="utf-8")
    sha_a = compute_sha256(first)
    sha_b = compute_sha256(second)
    prefix = tmp_path.as_posix()
    _cleanup_route_data([prefix], [sha_a, sha_b], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=sha_a)
    vault_service.copy_to_vault(sha256=sha_b)

    session_factory = create_session_factory(create_db_engine(router_service.config))
    with session_factory() as session:
        test_contents = list(
            session.scalars(
                select(KbFileContent).where(KbFileContent.sha256.in_([sha_a, sha_b]))
            ).all()
        )

    def _load_limited(**kwargs: object) -> list[KbFileContent]:
        items = sorted(test_contents, key=lambda item: item.id)
        limit = kwargs.get("limit")
        if limit is not None:
            return items[: int(limit)]  # type: ignore[arg-type]
        return items

    router_service._load_candidates = _load_limited  # type: ignore[method-assign]
    result = router_service.route_parsers(limit=1)

    assert result.candidates == 1
    assert len(result.decisions) == 1

    _cleanup_route_data([prefix], [sha_a, sha_b], vault_root)


def test_route_sha256_not_copied_empty_candidates(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, _vault, router_service, vault_root, _reports = router_env
    sample = tmp_path / "not_copied.txt"
    sample.write_text("not copied yet", encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(sample)
    _cleanup_route_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    result = router_service.route_parsers(sha256=expected_sha)

    assert result.candidates == 0
    assert result.routed == 0
    assert result.decisions == []

    _cleanup_route_data([prefix], [expected_sha], vault_root)


def test_route_idempotent(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    _scanner, _vault, router_service, vault_root, _reports = router_env
    expected_sha = _prepare_copied_content(router_env, tmp_path, "idem.txt", "idem route")
    prefix = tmp_path.as_posix()

    first = router_service.route_parsers(sha256=expected_sha)
    second = router_service.route_parsers(sha256=expected_sha)

    assert first.errors == []
    assert second.errors == []
    assert len(first.decisions) == 1
    assert len(second.decisions) == 1
    assert first.decisions[0].route_type == second.decisions[0].route_type
    assert first.decisions[0].rule_name == second.decisions[0].rule_name
    assert first.decisions[0].reason == second.decisions[0].reason
    assert first.skipped == 0
    assert second.skipped == 0

    _cleanup_route_data([prefix], [expected_sha], vault_root)


def test_route_single_error_continues(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, router_service, vault_root, _reports = router_env
    first = tmp_path / "err_a.txt"
    second = tmp_path / "err_b.txt"
    first.write_text("error a", encoding="utf-8")
    second.write_text("error b", encoding="utf-8")
    sha_a = compute_sha256(first)
    sha_b = compute_sha256(second)
    prefix = tmp_path.as_posix()
    _cleanup_route_data([prefix], [sha_a, sha_b], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=sha_a)
    vault_service.copy_to_vault(sha256=sha_b)

    session_factory = create_session_factory(create_db_engine(router_service.config))
    with session_factory() as session:
        test_contents = list(
            session.scalars(
                select(KbFileContent).where(KbFileContent.sha256.in_([sha_a, sha_b]))
            ).all()
        )

    def _load_only_test_candidates(**kwargs: object) -> list[KbFileContent]:
        return test_contents

    router_service._load_candidates = _load_only_test_candidates  # type: ignore[method-assign]

    original_route = router_service._route_content

    def _route_with_failure(content: KbFileContent, **kwargs: object):
        if content.sha256 == sha_a:
            raise RuntimeError("injected route failure")
        return original_route(content, **kwargs)

    router_service._route_content = _route_with_failure  # type: ignore[method-assign]
    result = router_service.route_parsers()

    assert len(result.errors) == 1
    assert result.errors[0].sha256 == sha_a
    assert result.routed == 1
    assert result.decisions[0].sha256 == sha_b

    _cleanup_route_data([prefix], [sha_a, sha_b], vault_root)


def test_original_files_unchanged(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, router_service, vault_root, _reports = router_env
    sample = tmp_path / "protected_route.txt"
    sample.write_text("protected route", encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(sample)
    _cleanup_route_data([prefix], [expected_sha], vault_root)

    before_stat = sample.stat()
    before_hash = compute_sha256(sample)
    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    router_service.route_parsers(sha256=expected_sha)

    after_stat = sample.stat()
    after_hash = compute_sha256(sample)
    assert before_stat.st_size == after_stat.st_size
    assert before_stat.st_mtime == after_stat.st_mtime
    assert before_hash == after_hash

    _cleanup_route_data([prefix], [expected_sha], vault_root)


def test_raw_vault_unchanged(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, router_service, vault_root, _reports = router_env
    sample = tmp_path / "vault_route.txt"
    sample.write_text("vault route", encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(sample)
    _cleanup_route_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)

    before_bins = {
        path.as_posix(): compute_sha256(path)
        for path in vault_root.rglob("original.bin")
    }
    before_listing = sorted(path.as_posix() for path in vault_root.rglob("*") if path.is_file())

    router_service.route_parsers(sha256=expected_sha)

    after_bins = {
        path.as_posix(): compute_sha256(path)
        for path in vault_root.rglob("original.bin")
    }
    after_listing = sorted(path.as_posix() for path in vault_root.rglob("*") if path.is_file())
    assert before_bins == after_bins
    assert before_listing == after_listing

    _cleanup_route_data([prefix], [expected_sha], vault_root)


def test_no_parsed_curated_quarantine_writes(
    router_env: tuple[
        InventoryScanner, FileContentVaultService, ParserRouterService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    parsed_root = workspace_root / "parsed"
    curated_root = workspace_root / "curated"
    quarantine_root = workspace_root / "quarantine"
    for directory in (parsed_root, curated_root, quarantine_root):
        directory.mkdir(parents=True, exist_ok=True)

    _scanner, _vault, router_service, vault_root, _reports = router_env
    expected_sha = _prepare_copied_content(router_env, tmp_path, "no_write.txt", "no parsed")
    prefix = tmp_path.as_posix()

    before = {
        "parsed": sorted(parsed_root.rglob("*")),
        "curated": sorted(curated_root.rglob("*")),
        "quarantine": sorted(quarantine_root.rglob("*")),
    }
    router_service.route_parsers(sha256=expected_sha)
    after = {
        "parsed": sorted(parsed_root.rglob("*")),
        "curated": sorted(curated_root.rglob("*")),
        "quarantine": sorted(quarantine_root.rglob("*")),
    }
    assert before == after

    _cleanup_route_data([prefix], [expected_sha], vault_root)


def test_cli_route_parsers_help() -> None:
    from typer.testing import CliRunner

    from app.cli.main import app

    result = CliRunner().invoke(app, ["route-parsers", "--help"])
    assert result.exit_code == 0
    assert "route-parsers" in result.stdout
    assert "--sha256" in result.stdout
    assert "--content-uid" in result.stdout
    assert "--limit" in result.stdout
