from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, func, select, update

from app.core.config import load_config
from app.core.database import create_db_engine, create_session_factory
from app.core.ids import compute_sha256
from app.core.vault_paths import VAULT_NOT_COPIED, build_vault_dir
from app.models.duplicate import KbDuplicateGroup
from app.models.file import KbFileContent, KbFileInstance
from app.models.vault import KbRawVaultObject
from app.services.duplicate_governance import DuplicateGovernanceService
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


def _cleanup_duplicate_data(
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
                session.execute(
                    delete(KbDuplicateGroup).where(KbDuplicateGroup.sha256 == sha256)
                )
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
def govern_env(
    tmp_path: Path,
) -> tuple[InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path]:
    workspace_root = tmp_path / "workspace"
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, workspace_root)
    config = load_config(config_path)
    try:
        engine = create_db_engine(config)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL unavailable for duplicate governance tests: {exc}")

    scanner = InventoryScanner(config)
    vault_service = FileContentVaultService(config)
    govern_service = DuplicateGovernanceService(config)
    return (
        scanner,
        vault_service,
        govern_service,
        workspace_root / "raw_vault",
        workspace_root / "reports",
    )


def _setup_duplicate_pair(tmp_path: Path) -> tuple[str, str]:
    first = tmp_path / "方案.txt"
    second = tmp_path / "方案副本.txt"
    first.write_text("duplicate governance sample", encoding="utf-8")
    second.write_text("duplicate governance sample", encoding="utf-8")
    return tmp_path.as_posix(), compute_sha256(first)


def test_govern_normal_duplicate_group(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, govern_service, vault_root, _reports_root = govern_env
    prefix, expected_sha = _setup_duplicate_pair(tmp_path)
    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    result = govern_service.govern_duplicates(sha256=expected_sha)

    assert result.errors == []
    assert result.groups_upserted == 1
    assert result.instances_linked == 2
    assert result.suggestions_generated == 1

    session_factory = create_session_factory(create_db_engine(govern_service.config))
    with session_factory() as session:
        group = session.scalar(
            select(KbDuplicateGroup).where(KbDuplicateGroup.duplicate_group_uid == expected_sha)
        )
        instances = list(
            session.scalars(
                select(KbFileInstance).where(KbFileInstance.sha256 == expected_sha)
            ).all()
        )
        assert group is not None
        assert group.instance_count == 2
        assert len(instances) == 2
        assert all(item.duplicate_group_uid == expected_sha for item in instances)
        master = next(
            item for item in instances if item.file_instance_uid == group.master_file_instance_uid
        )
        assert master.file_name == "方案.txt"

    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)


def test_govern_master_selection_copy_like_name(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, govern_service, vault_root, _reports_root = govern_env
    prefix, expected_sha = _setup_duplicate_pair(tmp_path)
    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    govern_service.govern_duplicates(sha256=expected_sha)

    session_factory = create_session_factory(create_db_engine(govern_service.config))
    with session_factory() as session:
        group = session.scalar(
            select(KbDuplicateGroup).where(KbDuplicateGroup.duplicate_group_uid == expected_sha)
        )
        instances = list(
            session.scalars(
                select(KbFileInstance).where(KbFileInstance.sha256 == expected_sha)
            ).all()
        )
        assert group is not None
        master = next(
            item for item in instances if item.file_instance_uid == group.master_file_instance_uid
        )
        duplicate = next(
            item for item in instances if item.file_instance_uid != group.master_file_instance_uid
        )
        assert master.file_name == "方案.txt"
        assert duplicate.file_name == "方案副本.txt"

    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)


def test_govern_chinese_path(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, govern_service, vault_root, _reports_root = govern_env
    chinese_dir = tmp_path / "中文路径" / "银行项目"
    chinese_dir.mkdir(parents=True)
    first = chinese_dir / "方案.txt"
    second = chinese_dir / "方案副本.txt"
    first.write_text("中文 duplicate", encoding="utf-8")
    second.write_text("中文 duplicate", encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(first)
    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    result = govern_service.govern_duplicates(sha256=expected_sha)

    assert result.errors == []
    assert result.groups_upserted == 1
    session_factory = create_session_factory(create_db_engine(govern_service.config))
    with session_factory() as session:
        instances = list(
            session.scalars(
                select(KbFileInstance).where(KbFileInstance.sha256 == expected_sha)
            ).all()
        )
        assert len(instances) == 2
        assert all("中文路径" in item.source_path for item in instances)

    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)


def test_govern_single_content_no_group(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, govern_service, vault_root, _reports_root = govern_env
    prefix = tmp_path.as_posix()
    sample = tmp_path / "single.txt"
    sample.write_text("single only", encoding="utf-8")
    expected_sha = compute_sha256(sample)
    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    result = govern_service.govern_duplicates(sha256=expected_sha)

    assert result.candidates == 0
    assert result.groups_processed == 0
    assert result.groups_upserted == 0

    session_factory = create_session_factory(create_db_engine(govern_service.config))
    with session_factory() as session:
        group_count = session.scalar(
            select(func.count())
            .select_from(KbDuplicateGroup)
            .where(KbDuplicateGroup.sha256 == expected_sha)
        )
        assert group_count == 0

    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)


def test_govern_idempotent(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, govern_service, vault_root, _reports_root = govern_env
    prefix, expected_sha = _setup_duplicate_pair(tmp_path)
    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    first = govern_service.govern_duplicates(sha256=expected_sha)
    second = govern_service.govern_duplicates(sha256=expected_sha)

    assert first.errors == []
    assert second.errors == []
    assert first.groups_upserted == 1
    assert second.groups_upserted == 0
    assert second.skipped >= 1

    session_factory = create_session_factory(create_db_engine(govern_service.config))
    with session_factory() as session:
        group_count = session.scalar(
            select(func.count())
            .select_from(KbDuplicateGroup)
            .where(KbDuplicateGroup.sha256 == expected_sha)
        )
        group = session.scalar(
            select(KbDuplicateGroup).where(KbDuplicateGroup.sha256 == expected_sha)
        )
        assert group_count == 1
        assert group is not None
        first_master = group.master_file_instance_uid

    third = govern_service.govern_duplicates(sha256=expected_sha)
    with session_factory() as session:
        group = session.scalar(
            select(KbDuplicateGroup).where(KbDuplicateGroup.sha256 == expected_sha)
        )
        assert group is not None
        assert group.master_file_instance_uid == first_master
        assert third.skipped >= 1

    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)


def test_govern_single_group_error_continues(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, govern_service, vault_root, _reports_root = govern_env
    prefix = tmp_path.as_posix()

    group_a = tmp_path / "group_a_1.txt"
    group_a_dup = tmp_path / "group_a_2.txt"
    group_b = tmp_path / "group_b_1.txt"
    group_b_dup = tmp_path / "group_b_2.txt"
    group_a.write_text("group a content", encoding="utf-8")
    group_a_dup.write_text("group a content", encoding="utf-8")
    group_b.write_text("group b content", encoding="utf-8")
    group_b_dup.write_text("group b content", encoding="utf-8")
    sha_a = compute_sha256(group_a)
    sha_b = compute_sha256(group_b)
    _cleanup_duplicate_data([prefix], [sha_a, sha_b], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=sha_a)
    vault_service.copy_to_vault(sha256=sha_b)

    session_factory = create_session_factory(create_db_engine(govern_service.config))
    with session_factory() as session:
        test_contents = list(
            session.scalars(
                select(KbFileContent).where(KbFileContent.sha256.in_([sha_a, sha_b]))
            ).all()
        )

    def _load_only_test_candidates(**kwargs: object) -> list[KbFileContent]:
        return test_contents

    govern_service._load_candidates = _load_only_test_candidates  # type: ignore[method-assign]

    original_govern = govern_service._govern_content

    def _govern_with_failure(content: KbFileContent) -> tuple:
        if content.sha256 == sha_a:
            raise RuntimeError("injected group failure")
        return original_govern(content)

    govern_service._govern_content = _govern_with_failure  # type: ignore[method-assign]
    result = govern_service.govern_duplicates()

    assert len(result.errors) == 1
    assert result.errors[0].sha256 == sha_a
    assert result.groups_upserted == 1
    assert result.candidates == 2

    session_factory = create_session_factory(create_db_engine(govern_service.config))
    with session_factory() as session:
        group_b = session.scalar(
            select(KbDuplicateGroup).where(KbDuplicateGroup.sha256 == sha_b)
        )
        assert group_b is not None

    _cleanup_duplicate_data([prefix], [sha_a, sha_b], vault_root)


def test_original_files_unchanged(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, govern_service, vault_root, _reports_root = govern_env
    prefix, expected_sha = _setup_duplicate_pair(tmp_path)
    first = tmp_path / "方案.txt"
    second = tmp_path / "方案副本.txt"
    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)

    before_first_stat = first.stat()
    before_second_stat = second.stat()
    before_first_hash = compute_sha256(first)
    before_second_hash = compute_sha256(second)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    govern_service.govern_duplicates(sha256=expected_sha)

    after_first_stat = first.stat()
    after_second_stat = second.stat()
    assert before_first_stat.st_size == after_first_stat.st_size
    assert before_first_stat.st_mtime == after_first_stat.st_mtime
    assert before_second_stat.st_size == after_second_stat.st_size
    assert before_second_stat.st_mtime == after_second_stat.st_mtime
    assert before_first_hash == compute_sha256(first)
    assert before_second_hash == compute_sha256(second)

    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)


def test_raw_vault_unchanged(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
    tmp_path: Path,
) -> None:
    scanner, vault_service, govern_service, vault_root, _reports_root = govern_env
    prefix, expected_sha = _setup_duplicate_pair(tmp_path)
    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)

    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)

    before_bins = {
        path.as_posix(): compute_sha256(path)
        for path in vault_root.rglob("original.bin")
    }
    before_listing = sorted(path.as_posix() for path in vault_root.rglob("*") if path.is_file())

    govern_service.govern_duplicates(sha256=expected_sha)

    after_bins = {
        path.as_posix(): compute_sha256(path)
        for path in vault_root.rglob("original.bin")
    }
    after_listing = sorted(path.as_posix() for path in vault_root.rglob("*") if path.is_file())
    assert before_bins == after_bins
    assert before_listing == after_listing

    _cleanup_duplicate_data([prefix], [expected_sha], vault_root)


def test_govern_project_fixtures_integration(
    govern_env: tuple[
        InventoryScanner, FileContentVaultService, DuplicateGovernanceService, Path, Path
    ],
) -> None:
    if not FIXTURES_ROOT.is_dir():
        pytest.skip("Project fixtures directory is missing")

    scanner, vault_service, govern_service, vault_root, reports_root = govern_env
    prefix = FIXTURES_ROOT.as_posix()
    fixture_files = list(FIXTURES_ROOT.rglob("*.txt"))
    sha_values = list(dict.fromkeys(compute_sha256(path) for path in fixture_files))
    _cleanup_duplicate_data([prefix], sha_values, vault_root)

    scanner.scan(FIXTURES_ROOT)
    vault_result = vault_service.copy_to_vault(sha256=sha_values[0])
    assert vault_result.errors == []

    result = govern_service.govern_duplicates(sha256=sha_values[0])

    assert result.groups_processed >= 1
    assert result.suggestions_generated >= 1
    assert result.errors == []
    assert result.duplicate_report_path is not None
    assert result.cleanup_suggestion_report_path is not None
    assert result.duplicate_report_path.exists()
    assert result.cleanup_suggestion_report_path.exists()

    duplicate_payload = json.loads(
        result.duplicate_report_path.read_text(encoding="utf-8")
    )
    cleanup_payload = json.loads(
        result.cleanup_suggestion_report_path.read_text(encoding="utf-8")
    )
    assert duplicate_payload["report_type"] == "duplicate_report"
    assert cleanup_payload["report_type"] == "cleanup_suggestion_report"
    assert cleanup_payload["auto_execute"] is False
    assert all(item["auto_execute"] is False for item in cleanup_payload["suggestions"])

    session_factory = create_session_factory(create_db_engine(govern_service.config))
    with session_factory() as session:
        group = session.scalar(
            select(KbDuplicateGroup).where(KbDuplicateGroup.sha256 == sha_values[0])
        )
        instances = list(
            session.scalars(
                select(KbFileInstance).where(KbFileInstance.sha256 == sha_values[0])
            ).all()
        )
        assert group is not None
        assert len(instances) == 2
        assert all(item.duplicate_group_uid == sha_values[0] for item in instances)
        master = next(
            item for item in instances if item.file_instance_uid == group.master_file_instance_uid
        )
        assert master.file_name == "方案.txt"

    assert list(reports_root.glob("duplicate_report_*.json"))
    assert list(reports_root.glob("cleanup_suggestion_report_*.json"))

    _cleanup_duplicate_data([prefix], sha_values, vault_root)
