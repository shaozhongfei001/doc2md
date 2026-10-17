from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, select, update

from app.core.config import load_config
from app.core.database import create_db_engine, create_session_factory
from app.core.ids import compute_sha256
from app.core.vault_paths import VAULT_COPIED, VAULT_NOT_COPIED, build_vault_dir
from app.models.file import KbFileContent, KbFileInstance
from app.models.vault import KbRawVaultObject
from app.services.file_content_vault import FileContentVaultService
from app.services.inventory_scanner import InventoryScanner
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


def _cleanup_vault_data(
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
def vault_env(tmp_path: Path) -> tuple[InventoryScanner, FileContentVaultService, Path]:
    workspace_root = tmp_path / "workspace"
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, workspace_root)
    config = load_config(config_path)
    try:
        engine = create_db_engine(config)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL unavailable for vault tests: {exc}")

    scanner = InventoryScanner(config)
    service = FileContentVaultService(config)
    return scanner, service, workspace_root / "raw_vault"


def test_copy_normal_content(vault_env: tuple[InventoryScanner, FileContentVaultService, Path], tmp_path: Path) -> None:
    scanner, service, vault_root = vault_env
    prefix = tmp_path.as_posix()
    sample = tmp_path / "sample.txt"
    sample.write_text("vault sample", encoding="utf-8")
    expected_sha = compute_sha256(sample)
    _cleanup_vault_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    result = service.copy_to_vault(sha256=expected_sha)

    assert result.copied == 1
    assert result.errors == []
    vault_dir = build_vault_dir(vault_root, expected_sha)
    assert (vault_dir / "original.bin").is_file()
    assert (vault_dir / "original_name.txt").read_text(encoding="utf-8") == "sample.txt"
    assert compute_sha256(vault_dir / "original.bin") == expected_sha

    session_factory = create_session_factory(create_db_engine(service.config))
    with session_factory() as session:
        content = session.scalar(
            select(KbFileContent).where(KbFileContent.sha256 == expected_sha)
        )
        vault_object = session.scalar(
            select(KbRawVaultObject).where(KbRawVaultObject.sha256 == expected_sha)
        )
        assert content is not None
        assert content.vault_status == VAULT_COPIED
        assert vault_object is not None
        assert vault_object.copy_status == "COPIED"

    _cleanup_vault_data([prefix], [expected_sha], vault_root)


def test_copy_idempotent(vault_env: tuple[InventoryScanner, FileContentVaultService, Path], tmp_path: Path) -> None:
    scanner, service, vault_root = vault_env
    prefix = tmp_path.as_posix()
    sample = tmp_path / "repeat.txt"
    sample.write_text("repeat vault", encoding="utf-8")
    expected_sha = compute_sha256(sample)
    _cleanup_vault_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    first = service.copy_to_vault(sha256=expected_sha)
    second = service.copy_to_vault(sha256=expected_sha)

    assert first.copied == 1
    assert second.copied == 0
    assert second.skipped == 1

    session_factory = create_session_factory(create_db_engine(service.config))
    with session_factory() as session:
        count = session.scalar(
            select(KbRawVaultObject).where(KbRawVaultObject.sha256 == expected_sha)
        )
        assert count is not None

    _cleanup_vault_data([prefix], [expected_sha], vault_root)


def test_copy_chinese_master_path(
    vault_env: tuple[InventoryScanner, FileContentVaultService, Path], tmp_path: Path
) -> None:
    scanner, service, vault_root = vault_env
    chinese_dir = tmp_path / "中文路径" / "银行项目"
    chinese_dir.mkdir(parents=True)
    sample = chinese_dir / "方案.txt"
    sample.write_text("中文 vault", encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(sample)
    _cleanup_vault_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    result = service.copy_to_vault(sha256=expected_sha)

    assert result.errors == []
    vault_dir = build_vault_dir(vault_root, expected_sha)
    assert (vault_dir / "original_name.txt").read_text(encoding="utf-8") == "方案.txt"
    source_paths = json.loads((vault_dir / "source_paths.json").read_text(encoding="utf-8"))
    assert "中文路径" in source_paths["instances"][0]["source_path"]

    _cleanup_vault_data([prefix], [expected_sha], vault_root)


def test_copy_duplicate_instances_one_bin(
    vault_env: tuple[InventoryScanner, FileContentVaultService, Path], tmp_path: Path
) -> None:
    scanner, service, vault_root = vault_env
    prefix = tmp_path.as_posix()
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("same vault content", encoding="utf-8")
    second.write_text("same vault content", encoding="utf-8")
    expected_sha = compute_sha256(first)
    _cleanup_vault_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    result = service.copy_to_vault(sha256=expected_sha)

    assert result.copied == 1
    vault_dir = build_vault_dir(vault_root, expected_sha)
    assert len(list(vault_root.rglob("original.bin"))) == 1
    source_paths = json.loads((vault_dir / "source_paths.json").read_text(encoding="utf-8"))
    assert len(source_paths["instances"]) == 2

    _cleanup_vault_data([prefix], [expected_sha], vault_root)


def test_copy_source_missing_continues(
    vault_env: tuple[InventoryScanner, FileContentVaultService, Path], tmp_path: Path
) -> None:
    scanner, service, vault_root = vault_env
    prefix = tmp_path.as_posix()
    good = tmp_path / "good.txt"
    bad = tmp_path / "bad.txt"
    good.write_text("good vault", encoding="utf-8")
    bad.write_text("bad vault", encoding="utf-8")
    good_sha = compute_sha256(good)
    bad_sha = compute_sha256(bad)
    _cleanup_vault_data([prefix], [good_sha, bad_sha], vault_root)

    scanner.scan(tmp_path)
    bad.unlink()

    session_factory = create_session_factory(create_db_engine(service.config))
    with session_factory() as session:
        candidates = list(
            session.scalars(
                select(KbFileContent).where(KbFileContent.sha256.in_([good_sha, bad_sha]))
            ).all()
        )

    def _load_only_test_candidates(**kwargs: object) -> list[KbFileContent]:
        return candidates

    service._load_candidates = _load_only_test_candidates  # type: ignore[method-assign]
    result = service.copy_to_vault()

    assert result.copied == 1
    assert len(result.errors) == 1
    assert result.errors[0].sha256 == bad_sha

    session_factory = create_session_factory(create_db_engine(service.config))
    with session_factory() as session:
        good_content = session.scalar(
            select(KbFileContent).where(KbFileContent.sha256 == good_sha)
        )
        bad_content = session.scalar(
            select(KbFileContent).where(KbFileContent.sha256 == bad_sha)
        )
        assert good_content is not None
        assert good_content.vault_status == VAULT_COPIED
        assert bad_content is not None
        assert bad_content.vault_status == "COPY_ERROR"

    _cleanup_vault_data([prefix], [good_sha, bad_sha], vault_root)


def test_original_files_unchanged(
    vault_env: tuple[InventoryScanner, FileContentVaultService, Path], tmp_path: Path
) -> None:
    scanner, service, vault_root = vault_env
    prefix = tmp_path.as_posix()
    sample = tmp_path / "protected.txt"
    sample.write_text("protected vault", encoding="utf-8")
    expected_sha = compute_sha256(sample)
    _cleanup_vault_data([prefix], [expected_sha], vault_root)

    before_stat = sample.stat()
    before_hash = compute_sha256(sample)
    scanner.scan(tmp_path)
    service.copy_to_vault(sha256=expected_sha)

    after_stat = sample.stat()
    after_hash = compute_sha256(sample)
    assert before_stat.st_size == after_stat.st_size
    assert before_stat.st_mtime == after_stat.st_mtime
    assert before_hash == after_hash

    _cleanup_vault_data([prefix], [expected_sha], vault_root)


def test_copy_project_fixtures_integration(
    vault_env: tuple[InventoryScanner, FileContentVaultService, Path],
) -> None:
    if not FIXTURES_ROOT.is_dir():
        pytest.skip("Project fixtures directory is missing")

    scanner, service, vault_root = vault_env
    prefix = FIXTURES_ROOT.as_posix()
    fixture_files = list(FIXTURES_ROOT.rglob("*.txt"))
    sha_values = list(dict.fromkeys(compute_sha256(path) for path in fixture_files))
    _cleanup_vault_data([prefix], sha_values, vault_root)

    scanner.scan(FIXTURES_ROOT)
    result = service.copy_to_vault(sha256=sha_values[0])

    assert result.copied == 1
    assert result.errors == []
    vault_dir = build_vault_dir(vault_root, sha_values[0])
    assert (vault_dir / "original.bin").is_file()
    metadata = json.loads((vault_dir / "file_metadata.json").read_text(encoding="utf-8"))
    assert metadata["master_file_name"] == "方案.txt"
    assert metadata["pipeline_version"] == "v1.1"

    second = service.copy_to_vault(sha256=sha_values[0])
    assert second.skipped == 1

    _cleanup_vault_data([prefix], sha_values, vault_root)
