from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, delete, select

from app.core.config import load_config
from app.core.database import create_db_engine, create_session_factory
from app.core.ids import compute_sha256
from app.models.file import KbFileContent, KbFileInstance
from app.services.inventory_scanner import InventoryScanner
from tests._db_env import (
    MYSQL_PASSWORD as DEFAULT_MYSQL_PASSWORD,
    mysql_config_block,
    mysql_url,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = PROJECT_ROOT / "backend" / "tests" / "fixtures"
# Inventory scanner E2E uses only the Chinese-path txt pair. Do not scan all of
# FIXTURES_ROOT — sibling dirs/files (e.g. 009 parse_quality_report_*.json) are
# document candidates for other specs and would pollute scan counts.
INVENTORY_FIXTURES_ROOT = FIXTURES_ROOT / "中文路径"


def _mysql_session_factory(password: str = DEFAULT_MYSQL_PASSWORD):
    engine = create_engine(
        mysql_url(password),
        pool_pre_ping=True,
        future=True,
    )
    return create_session_factory(engine)


def _write_test_config(config_path: Path, reports_root: Path) -> None:
    config_path.write_text(
        f"""app:
  name: personal-kb
  timezone: Asia/Shanghai
  pipeline_version: v1.1

storage:
  source_registry_root: {reports_root / "source_registry"}
  raw_vault_root: {reports_root / "raw_vault"}
  parsed_root: {reports_root / "parsed"}
  curated_root: {reports_root / "curated"}
  quarantine_root: {reports_root / "quarantine"}
  reports_root: {reports_root}

mysql:
{mysql_config_block(DEFAULT_MYSQL_PASSWORD)}

raw:
  original_files_readonly: true
  copy_unique_content_to_vault: true
""",
        encoding="utf-8",
    )


def _cleanup_scan_paths(prefixes: list[str], sha256_values: list[str] | None = None) -> None:
    session_factory = _mysql_session_factory()
    with session_factory() as session:
        for prefix in prefixes:
            session.execute(
                delete(KbFileInstance).where(KbFileInstance.source_path.like(f"{prefix}%"))
            )
        if sha256_values:
            for sha256 in sha256_values:
                session.execute(delete(KbFileContent).where(KbFileContent.sha256 == sha256))
        session.commit()


@pytest.fixture
def scanner(tmp_path: Path) -> InventoryScanner:
    reports_root = tmp_path / "reports"
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, reports_root)
    config = load_config(config_path)
    try:
        engine = create_db_engine(config)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL unavailable for inventory tests: {exc}")
    return InventoryScanner(config)


@pytest.fixture(autouse=True)
def cleanup_pytest_db_rows() -> None:
    yield
    try:
        _cleanup_scan_paths(["/tmp/pytest-of-"])
    except Exception:
        pass


def test_scan_normal_files(scanner: InventoryScanner, tmp_path: Path) -> None:
    prefix = tmp_path.as_posix()
    sample = tmp_path / "sample.txt"
    sample.write_text("hello inventory", encoding="utf-8")
    expected_sha = compute_sha256(sample)
    _cleanup_scan_paths([prefix], [expected_sha])

    result = scanner.scan(tmp_path)

    assert result.scanned_files == 1
    assert result.new_instances == 1
    assert result.new_contents == 1
    assert result.errors == []

    session_factory = create_session_factory(create_db_engine(scanner.config))
    with session_factory() as session:
        instance = session.scalar(
            select(KbFileInstance).where(KbFileInstance.source_path.like(f"{prefix}%"))
        )
        content = session.scalar(
            select(KbFileContent).where(KbFileContent.sha256 == expected_sha)
        )
        assert instance is not None
        assert content is not None
        assert instance.status == "DISCOVERED"
        assert instance.sha256 == content.sha256

    _cleanup_scan_paths([prefix], [expected_sha])


def test_scan_idempotent(scanner: InventoryScanner, tmp_path: Path) -> None:
    prefix = tmp_path.as_posix()
    sample = tmp_path / "repeat.txt"
    sample.write_text("repeat me", encoding="utf-8")
    expected_sha = compute_sha256(sample)
    _cleanup_scan_paths([prefix], [expected_sha])

    first = scanner.scan(tmp_path)
    second = scanner.scan(tmp_path)

    assert first.new_instances == 1
    assert second.new_instances == 0
    assert second.updated_instances == 1

    session_factory = create_session_factory(create_db_engine(scanner.config))
    with session_factory() as session:
        count = len(
            session.scalars(
                select(KbFileInstance).where(KbFileInstance.source_path.like(f"{prefix}%"))
            ).all()
        )
        assert count == 1

    _cleanup_scan_paths([prefix], [expected_sha])


def test_scan_chinese_path(scanner: InventoryScanner, tmp_path: Path) -> None:
    chinese_dir = tmp_path / "中文路径" / "银行项目"
    chinese_dir.mkdir(parents=True)
    sample = chinese_dir / "方案.txt"
    sample.write_text("中文内容", encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(sample)
    _cleanup_scan_paths([prefix], [expected_sha])

    result = scanner.scan(tmp_path)

    assert result.errors == []
    assert result.new_instances == 1

    session_factory = create_session_factory(create_db_engine(scanner.config))
    with session_factory() as session:
        instance = session.scalar(
            select(KbFileInstance).where(KbFileInstance.source_path.like(f"{prefix}%"))
        )
        assert instance is not None
        assert "中文路径" in instance.source_path
        assert instance.file_name == "方案.txt"

    _cleanup_scan_paths([prefix], [expected_sha])


def test_scan_duplicate_content(scanner: InventoryScanner, tmp_path: Path) -> None:
    prefix = tmp_path.as_posix()
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("same content", encoding="utf-8")
    second.write_text("same content", encoding="utf-8")
    expected_sha = compute_sha256(first)
    _cleanup_scan_paths([prefix], [expected_sha])

    result = scanner.scan(tmp_path)

    assert result.scanned_files == 2
    assert result.new_instances == 2
    assert result.new_contents == 1
    assert result.duplicate_instances == 1

    session_factory = create_session_factory(create_db_engine(scanner.config))
    with session_factory() as session:
        instances = session.scalars(
            select(KbFileInstance).where(KbFileInstance.source_path.like(f"{prefix}%"))
        ).all()
        content = session.scalar(
            select(KbFileContent).where(KbFileContent.sha256 == expected_sha)
        )
        assert len(instances) == 2
        assert content is not None
        duplicate_flags = sorted(item.is_duplicate_instance for item in instances)
        assert duplicate_flags == [0, 1]

    _cleanup_scan_paths([prefix], [expected_sha])


def test_scan_single_file_error_continues(
    scanner: InventoryScanner, tmp_path: Path
) -> None:
    prefix = tmp_path.as_posix()
    good = tmp_path / "good.txt"
    bad = tmp_path / "bad.txt"
    good.write_text("ok", encoding="utf-8")
    bad.write_text("locked", encoding="utf-8")
    expected_sha = compute_sha256(good)
    _cleanup_scan_paths([prefix], [expected_sha])

    def sha256_side_effect(path: Path) -> str:
        if path.name == "bad.txt":
            raise PermissionError("simulated read failure")
        return compute_sha256(path)

    try:
        with patch(
            "app.services.inventory_scanner.compute_sha256",
            side_effect=sha256_side_effect,
        ):
            result = scanner.scan(tmp_path)
        assert result.scanned_files == 2
        assert len(result.errors) == 1
        assert result.new_instances == 1

        session_factory = create_session_factory(create_db_engine(scanner.config))
        with session_factory() as session:
            discovered = session.scalars(
                select(KbFileInstance).where(
                    KbFileInstance.source_path.like(f"{prefix}%"),
                    KbFileInstance.status == "DISCOVERED",
                )
            ).all()
            assert len(discovered) == 1
    finally:
        _cleanup_scan_paths([prefix], [expected_sha])


def test_original_files_unchanged(scanner: InventoryScanner, tmp_path: Path) -> None:
    prefix = tmp_path.as_posix()
    sample = tmp_path / "protected.txt"
    sample.write_text("do not touch", encoding="utf-8")
    before_stat = sample.stat()
    before_hash = compute_sha256(sample)

    scanner.scan(tmp_path)

    after_stat = sample.stat()
    after_hash = compute_sha256(sample)
    assert before_stat.st_size == after_stat.st_size
    assert before_stat.st_mtime == after_stat.st_mtime
    assert before_hash == after_hash

    _cleanup_scan_paths([prefix], [before_hash])


def test_scan_project_fixtures(scanner: InventoryScanner) -> None:
    if not INVENTORY_FIXTURES_ROOT.is_dir():
        pytest.skip("Inventory scanner fixtures directory is missing")

    prefix = INVENTORY_FIXTURES_ROOT.as_posix()
    fixture_files = list(INVENTORY_FIXTURES_ROOT.rglob("*.txt"))
    sha_values = [compute_sha256(path) for path in fixture_files]
    unique_sha = list(dict.fromkeys(sha_values))
    _cleanup_scan_paths([prefix], unique_sha)

    result = scanner.scan(INVENTORY_FIXTURES_ROOT)

    assert result.scanned_files == 2
    assert result.new_instances == 2
    assert result.new_contents == 1
    assert result.duplicate_instances == 1
    assert result.errors == []

    second = scanner.scan(INVENTORY_FIXTURES_ROOT)
    assert second.new_instances == 0
    assert second.duplicate_instances == 1

    fixture_files = list(INVENTORY_FIXTURES_ROOT.rglob("*.txt"))
    sha_values = [compute_sha256(path) for path in fixture_files]
    unique_sha = list(dict.fromkeys(sha_values))
    _cleanup_scan_paths([prefix], unique_sha)
