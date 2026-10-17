from __future__ import annotations

import inspect
import json
import re
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, delete, func, inspect as sa_inspect, select, text, update
from typer.testing import CliRunner

from app.adapters.markitdown_adapter import PARSER_ADAPTER_VERSION, PARSER_NAME
from app.cli.main import app
from app.core.config import load_config
from app.core.database import create_db_engine, create_session_factory
from app.core.ids import compute_sha256
from app.core.parsed_paths import build_parsed_artifact_paths, build_parsed_content_dir
from app.models.document import KbDocument
from app.models.file import KbFileContent, KbFileInstance
from app.models.parse_registry import KbParseResult, KbParsedArtifact, KbParseRun
from app.services.parse_registry import (
    ARTIFACT_TYPE_PARSE_MANIFEST,
    ARTIFACT_TYPE_PARSE_REPORT,
    ARTIFACT_TYPE_PARSED_METADATA,
    ARTIFACT_TYPE_PARSED_TEXT,
    ERROR_INVALID_DRY_RUN_REPORT,
    PARSE_REGISTRY_MAX_LIMIT,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_UID_PATTERN,
    ParseRegistryError,
    ParseRegistryService,
    generate_run_uid,
)
from tests._db_env import (
    MYSQL_PASSWORD as DEFAULT_MYSQL_PASSWORD,
    mysql_config_block,
    mysql_url,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SQL = PROJECT_ROOT / "sql" / "migrations" / "006_parse_registry_v1.sql"
cli_runner = CliRunner()


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


def _apply_migration(engine) -> None:
    sql_text = MIGRATION_SQL.read_text(encoding="utf-8")
    statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def _count_registry_rows(session_factory) -> dict[str, int]:
    with session_factory() as session:
        return {
            "kb_parse_run": session.scalar(select(func.count()).select_from(KbParseRun)) or 0,
            "kb_parse_result": session.scalar(select(func.count()).select_from(KbParseResult)) or 0,
            "kb_parsed_artifact": session.scalar(select(func.count()).select_from(KbParsedArtifact))
            or 0,
            "kb_document": session.scalar(select(func.count()).select_from(KbDocument)) or 0,
            "parse_status_set": session.scalar(
                select(func.count()).select_from(KbFileContent).where(
                    KbFileContent.parse_status.isnot(None)
                )
            )
            or 0,
        }


def _cleanup_registry_data(
    content_uids: list[str] | None = None,
    run_uids: list[str] | None = None,
) -> None:
    session_factory = _mysql_session_factory()
    with session_factory() as session:
        if run_uids:
            for run_uid in run_uids:
                session.execute(
                    delete(KbParsedArtifact).where(KbParsedArtifact.run_uid == run_uid)
                )
            result_ids = list(
                session.scalars(
                    select(KbParseResult.id).where(KbParseResult.run_uid.in_(run_uids))
                )
            )
            if result_ids:
                session.execute(
                    update(KbParseResult)
                    .where(KbParseResult.retry_of_result_id.in_(result_ids))
                    .values(retry_of_result_id=None)
                )
            for run_uid in run_uids:
                session.execute(delete(KbParseResult).where(KbParseResult.run_uid == run_uid))
                session.execute(delete(KbParseRun).where(KbParseRun.run_uid == run_uid))
        if content_uids:
            for content_uid in content_uids:
                session.execute(delete(KbDocument).where(KbDocument.content_uid == content_uid))
                session.execute(
                    delete(KbFileContent).where(KbFileContent.content_uid == content_uid)
                )
                session.execute(
                    delete(KbFileInstance).where(KbFileInstance.content_uid == content_uid)
                )
        session.commit()


def _insert_test_content(
    session_factory,
    *,
    content_uid: str,
    sha256: str,
) -> None:
    with session_factory() as session:
        existing = session.scalar(
            select(KbFileContent).where(KbFileContent.content_uid == content_uid)
        )
        if existing is not None:
            return
        session.add(
            KbFileContent(
                content_uid=content_uid,
                sha256=sha256,
                file_ext=".txt",
                mime_type="text/plain",
                vault_status="COPIED",
                status="CONTENT_REGISTERED",
            )
        )
        session.commit()


def _write_success_manifest(
    parsed_root: Path,
    *,
    content_uid: str,
    sha256: str,
    status: str = "SUCCESS",
    text: str = "# parsed\n",
) -> dict[str, Path]:
    parsed_dir = build_parsed_content_dir(parsed_root, sha256)
    artifacts = build_parsed_artifact_paths(parsed_dir)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    artifacts["parsed_text"].write_text(text, encoding="utf-8")
    output_hash = compute_sha256(artifacts["parsed_text"])
    output_size = artifacts["parsed_text"].stat().st_size
    metadata = {
        "parser_name": PARSER_NAME,
        "parser_adapter_version": PARSER_ADAPTER_VERSION,
    }
    artifacts["parsed_metadata"].write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "content_uid": content_uid,
        "sha256": sha256,
        "route_type": "TEXT_OR_MARKDOWN",
        "parser_name": PARSER_NAME,
        "parser_adapter_version": PARSER_ADAPTER_VERSION,
        "source_vault_path": f"/tmp/raw_vault/{sha256}/original.bin",
        "parsed_text_path": artifacts["parsed_text"].as_posix(),
        "parsed_metadata_path": artifacts["parsed_metadata"].as_posix(),
        "status": status,
        "output_hash": output_hash,
        "output_size_bytes": output_size,
    }
    artifacts["parse_manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return artifacts


def _write_parse_report(
    reports_root: Path,
    *,
    items: list[dict],
    dry_run: bool = False,
) -> Path:
    reports_root.mkdir(parents=True, exist_ok=True)
    report_path = reports_root / "parse_markitdown_report_test.json"
    payload = {
        "report_type": "parse_markitdown_report",
        "parser_name": PARSER_NAME,
        "parser_adapter_version": PARSER_ADAPTER_VERSION,
        "pipeline_version": "v1.1",
        "generated_at": "2026-06-15T12:00:00Z",
        "dry_run": dry_run,
        "filters": {"limit": 1},
        "summary": {
            "total_candidates": len(items),
            "in_scope_candidates": len(items),
            "parsed_count": sum(1 for i in items if i.get("status") == "SUCCESS"),
            "skipped_count": sum(1 for i in items if i.get("status") == "SKIPPED"),
            "failed_count": sum(1 for i in items if i.get("status") == "FAILED"),
            "empty_count": sum(1 for i in items if i.get("status") == "EMPTY"),
        },
        "items": items,
        "errors": [],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report_path


@pytest.fixture
def registry_env(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, workspace_root)
    config = load_config(config_path)
    try:
        engine = create_db_engine(config)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL unavailable for parse registry tests: {exc}")

    _apply_migration(engine)
    session_factory = create_session_factory(engine)
    service = ParseRegistryService(config)
    parsed_root = workspace_root / "parsed"
    reports_root = workspace_root / "reports"
    raw_vault_root = workspace_root / "raw_vault"
    parsed_root.mkdir(parents=True, exist_ok=True)
    reports_root.mkdir(parents=True, exist_ok=True)
    raw_vault_root.mkdir(parents=True, exist_ok=True)

    yield {
        "config": config,
        "config_path": config_path,
        "service": service,
        "session_factory": session_factory,
        "parsed_root": parsed_root,
        "reports_root": reports_root,
        "raw_vault_root": raw_vault_root,
        "engine": engine,
    }


def test_migration_sql_contains_three_tables():
    sql_text = MIGRATION_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS kb_parse_run" in sql_text
    assert "CREATE TABLE IF NOT EXISTS kb_parse_result" in sql_text
    assert "CREATE TABLE IF NOT EXISTS kb_parsed_artifact" in sql_text


def test_migration_sql_does_not_create_kb_parse_job():
    sql_text = MIGRATION_SQL.read_text(encoding="utf-8")
    assert "kb_parse_job" not in sql_text


def test_migration_idempotent(registry_env):
    _apply_migration(registry_env["engine"])
    _apply_migration(registry_env["engine"])


def test_kb_parsed_artifact_unique_includes_run_uid():
    sql_text = MIGRATION_SQL.read_text(encoding="utf-8")
    assert "uk_artifact_scope (run_uid, content_uid, artifact_type, parser_name, parser_adapter_version)" in sql_text
    assert "uk_artifact_content_type" not in sql_text


def test_orm_models_align_with_migration(registry_env):
    inspector = sa_inspect(registry_env["engine"])
    run_columns = {col["name"] for col in inspector.get_columns("kb_parse_run")}
    assert "run_uid" in run_columns
    artifact_uniques = inspector.get_unique_constraints("kb_parsed_artifact")
    scope = next(c for c in artifact_uniques if c["name"] == "uk_artifact_scope")
    assert scope["column_names"] == [
        "run_uid",
        "content_uid",
        "artifact_type",
        "parser_name",
        "parser_adapter_version",
    ]


def test_create_parse_run(registry_env):
    service: ParseRegistryService = registry_env["service"]
    session_factory = registry_env["session_factory"]
    with session_factory() as session:
        run = KbParseRun(
            run_uid=generate_run_uid(),
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
            status="PENDING",
        )
        service.create_parse_run(session=session, run=run, status=RUN_STATUS_RUNNING)
        session.commit()
        assert run.status == RUN_STATUS_RUNNING
        assert run.started_at is not None
    _cleanup_registry_data(run_uids=[run.run_uid])


def test_finish_parse_run(registry_env):
    service: ParseRegistryService = registry_env["service"]
    session_factory = registry_env["session_factory"]
    run_uid = generate_run_uid()
    with session_factory() as session:
        run = KbParseRun(
            run_uid=run_uid,
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
            status=RUN_STATUS_RUNNING,
        )
        session.add(run)
        session.commit()
        service.finish_parse_run(session=session, run=run, status=RUN_STATUS_COMPLETED)
        session.commit()
        assert run.status == RUN_STATUS_COMPLETED
        assert run.finished_at is not None
    _cleanup_registry_data(run_uids=[run_uid])


def test_fail_parse_run(registry_env):
    service: ParseRegistryService = registry_env["service"]
    session_factory = registry_env["session_factory"]
    run_uid = generate_run_uid()
    with session_factory() as session:
        run = KbParseRun(
            run_uid=run_uid,
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
            status=RUN_STATUS_RUNNING,
        )
        session.add(run)
        session.commit()
        service.fail_parse_run(session=session, run=run, error_message="boom")
        session.commit()
        assert run.status == RUN_STATUS_FAILED
        assert run.error_message == "boom"
    _cleanup_registry_data(run_uids=[run_uid])


def test_record_success_result(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    run_uid = generate_run_uid()
    content_uid = "content_success_001"
    sha256 = "a" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    with session_factory() as session:
        session.add(
            KbParseRun(
                run_uid=run_uid,
                parser_name=PARSER_NAME,
                parser_adapter_version=PARSER_ADAPTER_VERSION,
                status=RUN_STATUS_RUNNING,
            )
        )
        session.commit()
        result = service.record_parse_result(
            session=session,
            run_uid=run_uid,
            content_uid=content_uid,
            sha256=sha256,
            status="SUCCESS",
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
        )
        session.commit()
        assert result.status == "SUCCESS"
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[run_uid])


def test_record_empty_result(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    run_uid = generate_run_uid()
    content_uid = "content_empty_001"
    sha256 = "b" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    with session_factory() as session:
        session.add(
            KbParseRun(
                run_uid=run_uid,
                parser_name=PARSER_NAME,
                parser_adapter_version=PARSER_ADAPTER_VERSION,
                status=RUN_STATUS_RUNNING,
            )
        )
        session.commit()
        result = service.record_parse_result(
            session=session,
            run_uid=run_uid,
            content_uid=content_uid,
            sha256=sha256,
            status="EMPTY",
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
        )
        session.commit()
        assert result.status == "EMPTY"
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[run_uid])


def test_record_failed_result(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    run_uid = generate_run_uid()
    content_uid = "content_failed_001"
    sha256 = "c" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    with session_factory() as session:
        session.add(
            KbParseRun(
                run_uid=run_uid,
                parser_name=PARSER_NAME,
                parser_adapter_version=PARSER_ADAPTER_VERSION,
                status=RUN_STATUS_RUNNING,
            )
        )
        session.commit()
        result = service.record_parse_result(
            session=session,
            run_uid=run_uid,
            content_uid=content_uid,
            sha256=sha256,
            status="FAILED",
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
            error_code="PARSER_RUNTIME",
            error_message="failed",
        )
        session.commit()
        assert result.status == "FAILED"
        assert result.error_code == "PARSER_RUNTIME"
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[run_uid])


def test_record_skipped_result(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    run_uid = generate_run_uid()
    content_uid = "content_skipped_001"
    sha256 = "d" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    with session_factory() as session:
        session.add(
            KbParseRun(
                run_uid=run_uid,
                parser_name=PARSER_NAME,
                parser_adapter_version=PARSER_ADAPTER_VERSION,
                status=RUN_STATUS_RUNNING,
            )
        )
        session.commit()
        result = service.record_parse_result(
            session=session,
            run_uid=run_uid,
            content_uid=content_uid,
            sha256=sha256,
            status="SKIPPED",
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
        )
        session.commit()
        assert result.status == "SKIPPED"
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[run_uid])


def test_skipped_without_manifest_creates_no_artifact(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    content_uid = "content_skip_no_manifest"
    sha256 = "e" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    parsed_dir = build_parsed_content_dir(parsed_root, sha256)
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SKIPPED",
                "parsed_dir": parsed_dir.as_posix(),
            }
        ],
    )
    result = service.register_parse_report(report_path=report_path, dry_run=False)
    with session_factory() as session:
        artifacts = list(
            session.scalars(
                select(KbParsedArtifact).where(
                    KbParsedArtifact.run_uid == result.run_uid,
                    KbParsedArtifact.content_uid == content_uid,
                )
            )
        )
        assert len(artifacts) == 0
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[result.run_uid or ""])


def test_record_parsed_text_artifact(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    run_uid = generate_run_uid()
    content_uid = "content_text_art"
    sha256 = "f" * 64
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    with session_factory() as session:
        session.add(
            KbParseRun(
                run_uid=run_uid,
                parser_name=PARSER_NAME,
                parser_adapter_version=PARSER_ADAPTER_VERSION,
                status=RUN_STATUS_RUNNING,
            )
        )
        session.commit()
        artifact = service.record_parsed_artifact(
            session=session,
            run_uid=run_uid,
            content_uid=content_uid,
            sha256=sha256,
            artifact_type=ARTIFACT_TYPE_PARSED_TEXT,
            artifact_path=artifacts["parsed_text"].as_posix(),
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
        )
        session.commit()
        assert artifact.artifact_type == ARTIFACT_TYPE_PARSED_TEXT
    _cleanup_registry_data(run_uids=[run_uid])


def test_record_parsed_metadata_artifact(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    run_uid = generate_run_uid()
    content_uid = "content_meta_art"
    sha256 = "0" * 64
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    with session_factory() as session:
        session.add(
            KbParseRun(
                run_uid=run_uid,
                parser_name=PARSER_NAME,
                parser_adapter_version=PARSER_ADAPTER_VERSION,
                status=RUN_STATUS_RUNNING,
            )
        )
        session.commit()
        artifact = service.record_parsed_artifact(
            session=session,
            run_uid=run_uid,
            content_uid=content_uid,
            sha256=sha256,
            artifact_type=ARTIFACT_TYPE_PARSED_METADATA,
            artifact_path=artifacts["parsed_metadata"].as_posix(),
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
        )
        session.commit()
        assert artifact.artifact_type == ARTIFACT_TYPE_PARSED_METADATA
    _cleanup_registry_data(run_uids=[run_uid])


def test_record_parse_manifest_artifact(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    run_uid = generate_run_uid()
    content_uid = "content_manifest_art"
    sha256 = "1" * 64
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    with session_factory() as session:
        session.add(
            KbParseRun(
                run_uid=run_uid,
                parser_name=PARSER_NAME,
                parser_adapter_version=PARSER_ADAPTER_VERSION,
                status=RUN_STATUS_RUNNING,
            )
        )
        session.commit()
        artifact = service.record_parsed_artifact(
            session=session,
            run_uid=run_uid,
            content_uid=content_uid,
            sha256=sha256,
            artifact_type=ARTIFACT_TYPE_PARSE_MANIFEST,
            artifact_path=artifacts["parse_manifest"].as_posix(),
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
        )
        session.commit()
        assert artifact.artifact_type == ARTIFACT_TYPE_PARSE_MANIFEST
    _cleanup_registry_data(run_uids=[run_uid])


def test_record_parse_report_artifact(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    reports_root = registry_env["reports_root"]
    run_uid = generate_run_uid()
    report_path = reports_root / "sample_report.json"
    report_path.write_text("{}", encoding="utf-8")
    with session_factory() as session:
        session.add(
            KbParseRun(
                run_uid=run_uid,
                parser_name=PARSER_NAME,
                parser_adapter_version=PARSER_ADAPTER_VERSION,
                status=RUN_STATUS_RUNNING,
            )
        )
        session.commit()
        artifact = service.record_parsed_artifact(
            session=session,
            run_uid=run_uid,
            content_uid="",
            sha256=None,
            artifact_type=ARTIFACT_TYPE_PARSE_REPORT,
            artifact_path=report_path.as_posix(),
            parser_name=PARSER_NAME,
            parser_adapter_version=PARSER_ADAPTER_VERSION,
        )
        session.commit()
        assert artifact.artifact_type == ARTIFACT_TYPE_PARSE_REPORT
        assert artifact.content_uid == ""
    _cleanup_registry_data(run_uids=[run_uid])


def test_register_parse_report_success(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    content_uid = "content_register_ok"
    sha256 = "2" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SUCCESS",
                "parsed_dir": artifacts["parsed_dir"].as_posix(),
            }
        ],
    )
    result = service.register_parse_report(report_path=report_path, dry_run=False)
    assert result.run_uid is not None
    assert RUN_UID_PATTERN.match(result.run_uid)
    with session_factory() as session:
        run = session.scalar(select(KbParseRun).where(KbParseRun.run_uid == result.run_uid))
        assert run is not None
        results = list(session.scalars(select(KbParseResult).where(KbParseResult.run_uid == result.run_uid)))
        assert len(results) == 1
        assert results[0].status == "SUCCESS"
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[result.run_uid or ""])


def test_register_dry_run_report_rejected(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    reports_root = registry_env["reports_root"]
    before = _count_registry_rows(session_factory)
    report_path = _write_parse_report(
        reports_root,
        items=[],
        dry_run=True,
    )
    with pytest.raises(ParseRegistryError) as exc_info:
        service.register_parse_report(report_path=report_path, dry_run=False)
    assert exc_info.value.code == ERROR_INVALID_DRY_RUN_REPORT
    after = _count_registry_rows(session_factory)
    assert before == after


def test_registry_dry_run_writes_no_db_rows(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    content_uid = "content_registry_dry"
    sha256 = "3" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SUCCESS",
                "parsed_dir": artifacts["parsed_dir"].as_posix(),
            }
        ],
    )
    before = _count_registry_rows(session_factory)
    result = service.register_parse_report(report_path=report_path, dry_run=True)
    after = _count_registry_rows(session_factory)
    assert result.dry_run is True
    assert before == after
    _cleanup_registry_data(content_uids=[content_uid])


def test_document_uid_equals_content_uid(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    content_uid = "content_doc_bridge"
    sha256 = "4" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SUCCESS",
                "parsed_dir": artifacts["parsed_dir"].as_posix(),
            }
        ],
    )
    result = service.register_parse_report(report_path=report_path, dry_run=False)
    with session_factory() as session:
        document = session.scalar(select(KbDocument).where(KbDocument.content_uid == content_uid))
        assert document is not None
        assert document.document_uid == content_uid
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[result.run_uid or ""])


def test_no_sha256_fallback_document_uid(registry_env):
    source = Path(inspect.getsourcefile(ParseRegistryService)) or Path()
    service_source = source.read_text(encoding="utf-8")
    assert "document_uid = parse_result.content_uid" in service_source
    assert re.search(r"document_uid.*sha256|sha256.*document_uid", service_source) is None


def test_retry_of_result_id_behavior(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    content_uid = "content_retry_chain"
    sha256 = "5" * 64
    run_uids: list[str] = []
    try:
        _cleanup_registry_data(content_uids=[content_uid])
        _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)

        failed_dir = build_parsed_content_dir(parsed_root, sha256)
        failed_artifacts = build_parsed_artifact_paths(failed_dir)
        failed_dir.mkdir(parents=True, exist_ok=True)
        failed_manifest = {
            "content_uid": content_uid,
            "sha256": sha256,
            "status": "FAILED",
            "parser_name": PARSER_NAME,
            "parser_adapter_version": PARSER_ADAPTER_VERSION,
            "error": {"code": "PARSER_RUNTIME", "message": "fail"},
        }
        failed_artifacts["parse_manifest"].write_text(json.dumps(failed_manifest), encoding="utf-8")
        failed_report = _write_parse_report(
            reports_root,
            items=[
                {
                    "content_uid": content_uid,
                    "sha256": sha256,
                    "route_type": "TEXT_OR_MARKDOWN",
                    "decision": "ROUTE",
                    "status": "FAILED",
                    "parsed_dir": failed_dir.as_posix(),
                }
            ],
        )
        first = service.register_parse_report(report_path=failed_report, dry_run=False)
        if first.run_uid:
            run_uids.append(first.run_uid)

        success_artifacts = _write_success_manifest(
            parsed_root, content_uid=content_uid, sha256=sha256
        )
        success_report = _write_parse_report(
            reports_root / "second",
            items=[
                {
                    "content_uid": content_uid,
                    "sha256": sha256,
                    "route_type": "TEXT_OR_MARKDOWN",
                    "decision": "ROUTE",
                    "status": "SUCCESS",
                    "parsed_dir": success_artifacts["parsed_dir"].as_posix(),
                }
            ],
        )
        success_report.parent.mkdir(parents=True, exist_ok=True)
        second = service.register_parse_report(report_path=success_report, dry_run=False)
        if second.run_uid:
            run_uids.append(second.run_uid)

        with session_factory() as session:
            success_result = session.scalar(
                select(KbParseResult).where(
                    KbParseResult.run_uid == second.run_uid,
                    KbParseResult.content_uid == content_uid,
                )
            )
            failed_result = session.scalar(
                select(KbParseResult).where(
                    KbParseResult.run_uid == first.run_uid,
                    KbParseResult.content_uid == content_uid,
                )
            )
            assert success_result is not None
            assert failed_result is not None
            assert success_result.retry_of_result_id == failed_result.id
    finally:
        _cleanup_registry_data(content_uids=[content_uid], run_uids=run_uids)


def test_reconcile_indexes_existing_parsed(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    content_uid = "content_reconcile"
    sha256 = "6" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)

    with patch("app.services.markitdown_parser.MarkItDownParserService") as mock_parser:
        result = service.reconcile_parsed_artifacts(sha256=sha256, dry_run=False)
        mock_parser.assert_not_called()

    assert result.results_recorded == 1
    assert result.artifacts_recorded >= 1
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[result.run_uid or ""])


def test_reconcile_dry_run_writes_no_db(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    content_uid = "content_reconcile_dry"
    sha256 = "7" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    before = _count_registry_rows(session_factory)
    result = service.reconcile_parsed_artifacts(sha256=sha256, dry_run=True)
    after = _count_registry_rows(session_factory)
    assert result.dry_run is True
    assert before == after
    _cleanup_registry_data(content_uids=[content_uid])


def test_no_raw_vault_mutation(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    raw_vault_root = registry_env["raw_vault_root"]
    content_uid = "content_vault_safe"
    sha256 = "8" * 64
    vault_dir = raw_vault_root / "by_hash" / sha256[:2] / sha256
    vault_dir.mkdir(parents=True, exist_ok=True)
    original_bin = vault_dir / "original.bin"
    original_bin.write_bytes(b"vault-bytes")
    before_hash = compute_sha256(original_bin)
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SUCCESS",
                "parsed_dir": artifacts["parsed_dir"].as_posix(),
            }
        ],
    )
    service.register_parse_report(report_path=report_path, dry_run=False)
    service.reconcile_parsed_artifacts(sha256=sha256, dry_run=False)
    assert compute_sha256(original_bin) == before_hash
    _cleanup_registry_data(content_uids=[content_uid])


def test_no_parsed_mutation(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    content_uid = "content_parsed_safe"
    sha256 = "9" * 64
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    before = {
        name: compute_sha256(path)
        for name, path in artifacts.items()
        if isinstance(path, Path) and path.is_file()
    }
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SUCCESS",
                "parsed_dir": artifacts["parsed_dir"].as_posix(),
            }
        ],
    )
    service.register_parse_report(report_path=report_path, dry_run=False)
    service.reconcile_parsed_artifacts(sha256=sha256, dry_run=False)
    after = {
        name: compute_sha256(path)
        for name, path in artifacts.items()
        if isinstance(path, Path) and path.is_file()
    }
    assert before == after
    _cleanup_registry_data(content_uids=[content_uid])


def test_no_parser_execution(registry_env):
    service_source = Path(inspect.getsourcefile(ParseRegistryService)).read_text(encoding="utf-8")
    assert "MarkItDown" not in service_source
    assert "markitdown_adapter" not in service_source
    assert "original.bin" not in service_source


def test_cli_register_parse_report(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    config_path = registry_env["config_path"]
    content_uid = "content_cli_register"
    sha256 = "a1" * 32
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SUCCESS",
                "parsed_dir": artifacts["parsed_dir"].as_posix(),
            }
        ],
    )
    result = cli_runner.invoke(
        app,
        [
            "register-parse-report",
            "--report-path",
            str(report_path),
            "--config",
            str(config_path),
        ],
    )
    assert result.exit_code == 0, result.output
    runs = service.list_parse_runs(limit=10)
    assert any(r.report_path == report_path.as_posix() for r in runs)
    run_uid = next(r.run_uid for r in runs if r.report_path == report_path.as_posix())
    _cleanup_registry_data(content_uids=[content_uid], run_uids=[run_uid])


def test_cli_list_and_show_commands(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    config_path = registry_env["config_path"]
    content_uid = "content_cli_list"
    sha256 = "b1" * 32
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SUCCESS",
                "parsed_dir": artifacts["parsed_dir"].as_posix(),
            }
        ],
    )
    reg = service.register_parse_report(report_path=report_path, dry_run=False)
    assert reg.run_uid is not None

    list_result = cli_runner.invoke(
        app,
        ["list-parse-jobs", "--config", str(config_path), "--limit", "5"],
    )
    assert list_result.exit_code == 0
    assert reg.run_uid in list_result.output

    show_result = cli_runner.invoke(
        app,
        [
            "show-parse-job",
            "--run-uid",
            reg.run_uid,
            "--include-results",
            "--include-artifacts",
            "--config",
            str(config_path),
        ],
    )
    assert show_result.exit_code == 0
    assert content_uid in show_result.output

    results_result = cli_runner.invoke(
        app,
        [
            "list-parse-results",
            "--content-uid",
            content_uid,
            "--config",
            str(config_path),
        ],
    )
    assert results_result.exit_code == 0
    assert "SUCCESS" in results_result.output

    artifacts_result = cli_runner.invoke(
        app,
        [
            "list-parsed-artifacts",
            "--content-uid",
            content_uid,
            "--config",
            str(config_path),
        ],
    )
    assert artifacts_result.exit_code == 0
    assert ARTIFACT_TYPE_PARSED_TEXT in artifacts_result.output

    _cleanup_registry_data(content_uids=[content_uid], run_uids=[reg.run_uid])


def test_cli_dry_run_behavior(registry_env):
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    config_path = registry_env["config_path"]
    content_uid = "content_cli_dry"
    sha256 = "c1" * 32
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    report_path = _write_parse_report(
        reports_root,
        items=[
            {
                "content_uid": content_uid,
                "sha256": sha256,
                "route_type": "TEXT_OR_MARKDOWN",
                "decision": "ROUTE",
                "status": "SUCCESS",
                "parsed_dir": artifacts["parsed_dir"].as_posix(),
            }
        ],
    )
    before = _count_registry_rows(session_factory)
    result = cli_runner.invoke(
        app,
        [
            "register-parse-report",
            "--report-path",
            str(report_path),
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )
    after = _count_registry_rows(session_factory)
    assert result.exit_code == 0, result.output
    assert "Dry run: True" in result.output
    assert before == after

    dry_report = _write_parse_report(reports_root / "dry", items=[], dry_run=True)
    reject = cli_runner.invoke(
        app,
        [
            "register-parse-report",
            "--report-path",
            str(dry_report),
            "--config",
            str(config_path),
        ],
    )
    assert reject.exit_code == 1
    assert ERROR_INVALID_DRY_RUN_REPORT in reject.output
    _cleanup_registry_data(content_uids=[content_uid])


def test_no_forbidden_imports_in_registry_module():
    service_path = PROJECT_ROOT / "backend" / "app" / "services" / "parse_registry.py"
    source = service_path.read_text(encoding="utf-8").lower()
    for forbidden in ("mineru", "ocr", "embedding", "vector", "curated", "project card"):
        assert forbidden not in source


def test_run_uid_format():
    run_uid = generate_run_uid()
    assert RUN_UID_PATTERN.match(run_uid)


def test_artifact_unique_includes_run_uid(registry_env):
    service = registry_env["service"]
    session_factory = registry_env["session_factory"]
    parsed_root = registry_env["parsed_root"]
    reports_root = registry_env["reports_root"]
    content_uid = "content_artifact_unique"
    sha256 = "d1" * 32
    _insert_test_content(session_factory, content_uid=content_uid, sha256=sha256)
    artifacts = _write_success_manifest(parsed_root, content_uid=content_uid, sha256=sha256)
    item = {
        "content_uid": content_uid,
        "sha256": sha256,
        "route_type": "TEXT_OR_MARKDOWN",
        "decision": "ROUTE",
        "status": "SUCCESS",
        "parsed_dir": artifacts["parsed_dir"].as_posix(),
    }
    report1 = _write_parse_report(reports_root / "r1", items=[item])
    report2 = _write_parse_report(reports_root / "r2", items=[item])
    first = service.register_parse_report(report_path=report1, dry_run=False)
    second = service.register_parse_report(report_path=report2, dry_run=False)
    with session_factory() as session:
        first_count = session.scalar(
            select(func.count())
            .select_from(KbParsedArtifact)
            .where(
                KbParsedArtifact.run_uid == first.run_uid,
                KbParsedArtifact.content_uid == content_uid,
            )
        )
        second_count = session.scalar(
            select(func.count())
            .select_from(KbParsedArtifact)
            .where(
                KbParsedArtifact.run_uid == second.run_uid,
                KbParsedArtifact.content_uid == content_uid,
            )
        )
        assert first_count and first_count >= 3
        assert second_count and second_count >= 3
    _cleanup_registry_data(
        content_uids=[content_uid],
        run_uids=[first.run_uid or "", second.run_uid or ""],
    )


def test_reconcile_requires_filter(registry_env):
    config_path = registry_env["config_path"]
    result = cli_runner.invoke(
        app,
        ["reconcile-parsed-artifacts", "--config", str(config_path)],
    )
    assert result.exit_code == 1


def test_reconcile_limit_cap(registry_env):
    config_path = registry_env["config_path"]
    result = cli_runner.invoke(
        app,
        [
            "reconcile-parsed-artifacts",
            "--limit",
            str(PARSE_REGISTRY_MAX_LIMIT + 1),
            "--config",
            str(config_path),
        ],
    )
    assert result.exit_code == 1
