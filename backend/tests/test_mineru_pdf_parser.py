from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import create_engine, delete, func, select, update
from typer.testing import CliRunner

from app.cli.main import app
from app.core.config import load_config
from app.core.database import create_db_engine, create_session_factory
from app.core.ids import compute_sha256
from app.core.parsed_paths import build_parsed_artifact_paths, build_parsed_content_dir
from app.core.parser_routing import RouteType
from app.core.vault_paths import VAULT_NOT_COPIED, build_vault_artifact_paths, build_vault_dir
from app.models.file import KbFileContent, KbFileInstance
from app.models.parse_registry import KbParsedArtifact, KbParseResult, KbParseRun
from app.models.vault import KbRawVaultObject
from app.services.file_content_vault import FileContentVaultService
from app.services.inventory_scanner import InventoryScanner
from app.services.mineru_pdf_parser import (
    DECISION_ALREADY_SUCCESS,
    DECISION_ASSET_INCOMPLETE,
    DECISION_OUTPUT_CONTRACT_VIOLATION,
    DECISION_PARSED,
    DECISION_ROUTE_MISMATCH,
    DECISION_SUBPROCESS_ERROR,
    DECISION_TIMEOUT,
    PARSER_ADAPTER_VERSION,
    PARSER_NAME,
    PARSER_PROFILE,
    SKIP_REASON_IDEMPOTENT,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    MineruPdfParserService,
)
from app.services.parse_registry import ParseRegistryService
from tests._db_env import (
    MYSQL_PASSWORD as DEFAULT_MYSQL_PASSWORD,
    mysql_config_block,
    mysql_url,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
                    .values(vault_path=None, vault_status=VAULT_NOT_COPIED, parse_status=None)
                )
                session.execute(delete(KbFileContent).where(KbFileContent.sha256 == sha256))
        session.commit()

    if vault_root and vault_root.exists():
        shutil.rmtree(vault_root, ignore_errors=True)
    if parsed_root and parsed_root.exists():
        shutil.rmtree(parsed_root, ignore_errors=True)


def _cleanup_registry_runs(run_uids: list[str]) -> None:
    if not run_uids:
        return
    session_factory = _mysql_session_factory()
    with session_factory() as session:
        for run_uid in run_uids:
            session.execute(delete(KbParsedArtifact).where(KbParsedArtifact.run_uid == run_uid))
            session.execute(delete(KbParseResult).where(KbParseResult.run_uid == run_uid))
            session.execute(delete(KbParseRun).where(KbParseRun.run_uid == run_uid))
        session.commit()


def _count_parse_results() -> int:
    session_factory = _mysql_session_factory()
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(KbParseResult)) or 0


@pytest.fixture
def mineru_env(
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
        pytest.skip(f"MySQL unavailable for mineru parser tests: {exc}")

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


def _mock_subprocess_success(
    md_text: str = "# parsed pdf\n",
    *,
    returncode: int = 0,
    include_image: Path | None = None,
):
    def runner(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        output_dir = Path(cmd[cmd.index("-o") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "output.md").write_text(md_text, encoding="utf-8")
        if include_image is not None:
            shutil.copy2(include_image, output_dir / include_image.name)
        return subprocess.CompletedProcess(cmd, returncode, stdout="ok", stderr="")

    return runner


def _mock_subprocess_timeout():
    def runner(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output=b"", stderr=b"timed out")

    return runner


def _mineru_service(config_path: Path, subprocess_runner=None) -> MineruPdfParserService:
    config = load_config(config_path)
    return MineruPdfParserService(
        config,
        magic_pdf_cmd="magic-pdf-mock",
        subprocess_runner=subprocess_runner,
    )


_FIXTURE_PDF_TEXT = (
    "Digital PDF sample text for the mineru parser adapter tests. "
    "The quick brown fox jumps over the lazy dog. "
    "Pack my box with five dozen liquor jugs."
)


def _write_digital_pdf(path: Path) -> None:
    """生成带真实文本层的数字 PDF 夹具。

    014 内容嗅探要求 ``text_chars >= 100`` 且 ``avg_chars_per_page >= 20``
    才判 ``PDF_DIGITAL``；伪 PDF 字节串（无文本层）会被正确判为
    ``PDF_SCANNED_OR_IMAGE``，因此这里用 pypdf 写出可抽取文本的最小 PDF。
    """
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    content = DecodedStreamObject()
    content.set_data(
        f"BT /F1 12 Tf 72 700 Td ({_FIXTURE_PDF_TEXT}) Tj ET".encode("latin-1")
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as handle:
        writer.write(handle)


def _prepare_copied_pdf(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
    filename: str = "sample.pdf",
    content_text: str | None = None,
) -> str:
    scanner, vault_service, vault_root, _parsed, _reports, _config = mineru_env
    sample = tmp_path / filename
    if content_text is None:
        _write_digital_pdf(sample)
    else:
        sample.write_text(content_text, encoding="utf-8")
    prefix = tmp_path.as_posix()
    expected_sha = compute_sha256(sample)
    _cleanup_parser_data([prefix], [expected_sha], vault_root)
    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)
    return expected_sha


def _content_uid_for_sha(session_factory, sha256: str) -> str:
    with session_factory() as session:
        content = session.scalar(select(KbFileContent).where(KbFileContent.sha256 == sha256))
        assert content is not None
        return content.content_uid


def _write_success_parse_manifest(
    parsed_root: Path,
    sha256: str,
    content_uid: str,
    source_vault_path: str,
) -> dict[str, Path]:
    parsed_dir = build_parsed_content_dir(parsed_root, sha256)
    artifacts = build_parsed_artifact_paths(parsed_dir)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    artifacts["parsed_text"].write_text("# existing\n", encoding="utf-8")
    output_hash = compute_sha256(artifacts["parsed_text"])
    output_size = artifacts["parsed_text"].stat().st_size
    artifacts["parsed_metadata"].write_text(
        json.dumps(
            {
                "parser_name": PARSER_NAME,
                "parser_profile": PARSER_PROFILE,
                "parser_adapter_version": PARSER_ADAPTER_VERSION,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest = {
        "content_uid": content_uid,
        "sha256": sha256,
        "route_type": RouteType.PDF_DIGITAL.value,
        "parser_name": PARSER_NAME,
        "parser_profile": PARSER_PROFILE,
        "parser_adapter_version": PARSER_ADAPTER_VERSION,
        "source_vault_path": source_vault_path,
        "parsed_text_path": artifacts["parsed_text"].as_posix(),
        "parsed_metadata_path": artifacts["parsed_metadata"].as_posix(),
        "generated_at": "2026-01-01T00:00:00Z",
        "status": STATUS_SUCCESS,
        "output_hash": output_hash,
        "output_size_bytes": output_size,
    }
    artifacts["parse_manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return artifacts


def test_manifest_three_file_names_match_005_contract(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    service.parse_many(sha256=expected_sha, limit=1)

    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    assert artifacts["parsed_text"].name == "parsed_text.md"
    assert artifacts["parsed_metadata"].name == "parsed_metadata.json"
    assert artifacts["parse_manifest"].name == "parse_manifest.json"
    assert not (artifacts["parsed_dir"] / "content.md").exists()
    assert not (artifacts["parsed_dir"] / "parse_report.json").exists()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_magic_pdf_receives_pdf_suffixed_input(
    tmp_path: Path,
) -> None:
    """MinerU 按后缀分发解析器，vault 原件 original.bin 必须以 .pdf 后缀传入。

    该用例不依赖 MySQL：只验证调用 magic-pdf 时的入参构造与 staging 副本。
    """
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, tmp_path / "workspace")
    config = load_config(config_path)

    original = tmp_path / "original.bin"
    original.write_bytes(b"%PDF-1.4 staged input test")
    staging = tmp_path / "staging"
    staging.mkdir()

    seen: dict[str, Path] = {}

    def runner(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        seen["input"] = Path(cmd[cmd.index("-p") + 1])
        seen["output"] = Path(cmd[cmd.index("-o") + 1])
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    service = MineruPdfParserService(
        config,
        magic_pdf_cmd="magic-pdf-mock",
        subprocess_runner=runner,
    )
    service._invoke_magic_pdf(input_path=original, output_dir=staging, timeout_seconds=5)

    staged = seen["input"]
    assert staged.suffix == ".pdf", staged
    assert staged == staging / "original.pdf"
    assert staged.read_bytes() == original.read_bytes()
    # 暂存副本只落在 staging 目录，不污染 vault 原件目录
    assert not (tmp_path / "original.pdf").exists()


def test_parse_manifest_field_completeness(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    vault_dir = build_vault_dir(vault_root, expected_sha)
    source_path = build_vault_artifact_paths(vault_dir)["original_bin"].as_posix()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    service.parse_many(sha256=expected_sha, limit=1)

    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    manifest = json.loads(artifacts["parse_manifest"].read_text(encoding="utf-8"))
    assert manifest["parser_name"] == PARSER_NAME
    assert manifest["parser_profile"] == PARSER_PROFILE
    assert manifest["parser_adapter_version"] == PARSER_ADAPTER_VERSION
    assert manifest["route_type"] == RouteType.PDF_DIGITAL.value
    assert manifest["status"] == STATUS_SUCCESS
    assert manifest["parsed_text_path"] == artifacts["parsed_text"].as_posix()
    assert manifest["parsed_metadata_path"] == artifacts["parsed_metadata"].as_posix()
    assert manifest["source_vault_path"] == source_path
    assert manifest["output_hash"]
    assert manifest["generated_at"]

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_parsed_metadata_field_completeness(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    content_uid = _content_uid_for_sha(session_factory, expected_sha)

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    service.parse_many(sha256=expected_sha, limit=1)

    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    metadata = json.loads(artifacts["parsed_metadata"].read_text(encoding="utf-8"))
    assert metadata["parser_name"] == PARSER_NAME
    assert metadata["parser_profile"] == PARSER_PROFILE
    assert metadata["parser_adapter_version"] == PARSER_ADAPTER_VERSION
    assert metadata["route_type"] == RouteType.PDF_DIGITAL.value
    assert metadata["content_uid"] == content_uid
    assert metadata["sha256"] == expected_sha
    assert metadata["source_vault_path"]
    assert metadata["generated_at"]

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_idempotent_reads_parse_manifest_json(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    content_uid = _content_uid_for_sha(session_factory, expected_sha)
    vault_dir = build_vault_dir(vault_root, expected_sha)
    source_path = build_vault_artifact_paths(vault_dir)["original_bin"].as_posix()
    _write_success_parse_manifest(parsed_root, expected_sha, content_uid, source_path)

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    plan = service.plan_one(sha256=expected_sha)
    assert plan.decision == DECISION_ALREADY_SUCCESS
    assert plan.skip_reason == SKIP_REASON_IDEMPOTENT

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_dry_run_zero_side_effects(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, reports_root, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())

    result = service.parse_many(sha256=expected_sha, limit=1, dry_run=True)

    assert result.dry_run is True
    assert result.report_path is None
    assert not parsed_root.exists() or not any(parsed_root.rglob("parsed_text.md"))
    assert not any(reports_root.glob("parse_mineru_pdf_report_*.json"))
    assert result.plans[0].dry_run_action == "would_parse"

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_route_mismatch_skip_docx_no_parsed_dir(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    sample = tmp_path / "doc.docx"
    sample.write_text("docx", encoding="utf-8")
    prefix = tmp_path.as_posix()
    scanner, vault_service, _, _, _, _ = mineru_env
    expected_sha = compute_sha256(sample)
    _cleanup_parser_data([prefix], [expected_sha], vault_root)
    scanner.scan(tmp_path)
    vault_service.copy_to_vault(sha256=expected_sha)

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.parsed_count == 0
    assert result.items[0].status == STATUS_SKIPPED
    assert result.items[0].decision == DECISION_ROUTE_MISMATCH
    parsed_dir = build_parsed_content_dir(parsed_root, expected_sha)
    assert not parsed_dir.exists()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_existing_success_skip_without_force(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    content_uid = _content_uid_for_sha(session_factory, expected_sha)
    vault_dir = build_vault_dir(vault_root, expected_sha)
    source_path = build_vault_artifact_paths(vault_dir)["original_bin"].as_posix()
    _write_success_parse_manifest(parsed_root, expected_sha, content_uid, source_path)

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.parsed_count == 0
    assert result.skipped_count == 1
    assert result.items[0].decision == DECISION_ALREADY_SUCCESS

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_force_overwrites_existing_manifest(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    content_uid = _content_uid_for_sha(session_factory, expected_sha)
    vault_dir = build_vault_dir(vault_root, expected_sha)
    source_path = build_vault_artifact_paths(vault_dir)["original_bin"].as_posix()
    _write_success_parse_manifest(parsed_root, expected_sha, content_uid, source_path)

    service = _mineru_service(
        config_path,
        subprocess_runner=_mock_subprocess_success(md_text="# forced\n"),
    )
    result = service.parse_many(sha256=expected_sha, limit=1, force=True)

    assert result.parsed_count == 1
    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    assert "# forced" in artifacts["parsed_text"].read_text(encoding="utf-8")
    manifest = json.loads(artifacts["parse_manifest"].read_text(encoding="utf-8"))
    assert manifest["status"] == STATUS_SUCCESS

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_force_cleans_assets_directory(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    parsed_dir = build_parsed_content_dir(parsed_root, expected_sha)
    stale_assets = parsed_dir / "assets" / "images" / "stale.png"
    stale_assets.parent.mkdir(parents=True, exist_ok=True)
    stale_assets.write_bytes(b"stale")

    image = tmp_path / "new.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    service = _mineru_service(
        config_path,
        subprocess_runner=_mock_subprocess_success(include_image=image),
    )
    service.parse_many(sha256=expected_sha, limit=1, force=True)

    assert not stale_assets.exists()
    assert (parsed_dir / "assets" / "images" / "new.png").is_file()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_subprocess_timeout_no_manifest(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_timeout())
    result = service.parse_many(sha256=expected_sha, limit=1, timeout_seconds=5)

    assert result.failed_count == 1
    assert result.items[0].decision == DECISION_TIMEOUT
    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    assert not artifacts["parse_manifest"].exists()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_subprocess_non_zero_returncode_no_manifest(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(
        config_path,
        subprocess_runner=_mock_subprocess_success(returncode=1),
    )
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.failed_count == 1
    assert result.items[0].decision == DECISION_SUBPROCESS_ERROR
    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    assert not artifacts["parse_manifest"].exists()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_output_contract_violation_no_manifest(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    def empty_runner(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        output_dir = Path(cmd[cmd.index("-o") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    service = _mineru_service(config_path, subprocess_runner=empty_runner)
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.failed_count == 1
    assert result.items[0].decision == DECISION_OUTPUT_CONTRACT_VIOLATION
    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    assert not artifacts["parse_manifest"].exists()
    assert not artifacts["parsed_text"].exists()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_contract_violation_message_carries_magic_pdf_output(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    """magic-pdf 内部异常仍以 exit 0 退出时，错误信息必须带出输出尾部。"""
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    def failing_runner(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        output_dir = Path(cmd[cmd.index("-o") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="Traceback (most recent call last):\nException: Unknown file suffix: .bin\n",
            stderr="",
        )

    service = _mineru_service(config_path, subprocess_runner=failing_runner)
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.failed_count == 1
    error = result.errors[0]
    assert error.code == DECISION_OUTPUT_CONTRACT_VIOLATION
    # 真实原因必须可见，否则排查只能靠复现
    assert "Unknown file suffix: .bin" in error.message
    assert "returncode=0" in error.message

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_assets_dir_recorded_in_parse_manifest(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    image = tmp_path / "figure.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    service = _mineru_service(
        config_path,
        subprocess_runner=_mock_subprocess_success(include_image=image),
    )
    service.parse_many(sha256=expected_sha, limit=1)

    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    manifest = json.loads(artifacts["parse_manifest"].read_text(encoding="utf-8"))
    assert manifest.get("assets_dir")
    assert manifest.get("asset_files")

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_register_true_real_registry_ingest_success(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    content_uid = _content_uid_for_sha(session_factory, expected_sha)
    config = load_config(config_path)
    registry = ParseRegistryService(config)
    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())

    result = service.parse_many(
        sha256=expected_sha,
        limit=1,
        register=True,
        registry_service=registry,
    )
    assert result.report_path is not None

    with session_factory() as session:
        parse_result = session.scalar(
            select(KbParseResult)
            .where(KbParseResult.content_uid == content_uid)
            .order_by(KbParseResult.id.desc())
        )
        assert parse_result is not None
        assert parse_result.status == STATUS_SUCCESS
        assert parse_result.error_code is None or parse_result.error_code != "MISSING_MANIFEST"
        assert parse_result.text_path
        assert parse_result.metadata_path
        assert parse_result.manifest_path
        artifact_count = session.scalar(
            select(func.count())
            .select_from(KbParsedArtifact)
            .where(KbParsedArtifact.run_uid == parse_result.run_uid)
        )
        assert artifact_count is not None and artifact_count >= 3

    _cleanup_registry_runs([parse_result.run_uid])
    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_register_false_no_db_write(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    before = _count_parse_results()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    service.parse_many(sha256=expected_sha, limit=1, register=False)

    assert _count_parse_results() == before

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_dry_run_register_no_db_write(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    before = _count_parse_results()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    service.parse_many(sha256=expected_sha, limit=1, dry_run=True, register=True)

    assert _count_parse_results() == before

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_batch_limit_in_scope_only(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    scanner, vault_service, vault_root, parsed_root, _reports, config_path = mineru_env
    first = tmp_path / "limit_a.pdf"
    second = tmp_path / "limit_b.pdf"
    third = tmp_path / "skip.docx"
    first.write_text("%PDF a", encoding="utf-8")
    second.write_text("%PDF b", encoding="utf-8")
    third.write_text("docx", encoding="utf-8")
    sha_a = compute_sha256(first)
    sha_b = compute_sha256(second)
    sha_docx = compute_sha256(third)
    prefix = tmp_path.as_posix()
    _cleanup_parser_data([prefix], [sha_a, sha_b, sha_docx], vault_root)

    scanner.scan(tmp_path)
    for sha in (sha_a, sha_b, sha_docx):
        vault_service.copy_to_vault(sha256=sha)

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    result = service.parse_many(limit=1)

    assert result.parsed_count == 1
    assert any(item.skip_reason == "parse_limit_reached" for item in result.items)

    _cleanup_parser_data([prefix], [sha_a, sha_b, sha_docx], vault_root, parsed_root)


def test_batch_sha256_filter(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.total_candidates == 1
    assert result.parsed_count == 1

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_batch_content_uid_filter(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    result = service.parse_many(content_uid=expected_sha, limit=1)

    assert result.total_candidates == 1
    assert result.parsed_count == 1

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_missing_original_bin_handled(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    vault_dir = build_vault_dir(vault_root, expected_sha)
    build_vault_artifact_paths(vault_dir)["original_bin"].unlink()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.failed_count == 1
    assert result.errors[0].code == "MISSING_ORIGINAL_BIN"
    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    assert not artifacts["parse_manifest"].exists()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_empty_mineru_markdown_status_empty(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(
        config_path,
        subprocess_runner=_mock_subprocess_success(md_text="   "),
    )
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.empty_count == 1
    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    manifest = json.loads(artifacts["parse_manifest"].read_text(encoding="utf-8"))
    assert manifest["status"] == STATUS_EMPTY

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_pdf_scanned_or_image_route_in_scope(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())

    with patch(
        "app.services.mineru_pdf_parser.match_route_type",
        return_value=(
            RouteType.PDF_SCANNED_OR_IMAGE,
            "ROUTE",
            "ext_pdf_scanned",
            "MINERU_FAMILY",
            "scanned pdf",
        ),
    ):
        result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.parsed_count == 1
    assert result.items[0].route_type == RouteType.PDF_SCANNED_OR_IMAGE.value

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_cli_argument_parsing(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    with patch("app.cli.main.check_magic_pdf_available"), patch(
        "app.cli.main.MineruPdfParserService.parse_many",
        return_value=MagicMock(
            total_candidates=1,
            in_scope_candidates=1,
            parsed_count=1,
            skipped_count=0,
            failed_count=0,
            empty_count=0,
            timeout_count=0,
            partial_count=0,
            dry_run=False,
            errors=[],
            report_path=None,
            plans=[],
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "parse-mineru-pdf",
                "--config",
                str(config_path),
                "--sha256",
                expected_sha,
                "--timeout",
                "120",
                "--no-register",
            ],
        )

    assert result.exit_code == 0
    assert "Parsed: 1" in result.stdout

    no_filter = CliRunner().invoke(app, ["parse-mineru-pdf", "--config", str(config_path)])
    assert no_filter.exit_code != 0

    over_limit = CliRunner().invoke(
        app,
        ["parse-mineru-pdf", "--config", str(config_path), "--limit", "101"],
    )
    assert over_limit.exit_code != 0

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_parsed_three_files_generated(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(
        config_path,
        subprocess_runner=_mock_subprocess_success(md_text="# three files\n"),
    )
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.parsed_count == 1
    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    assert artifacts["parsed_text"].is_file()
    assert artifacts["parsed_metadata"].is_file()
    assert artifacts["parse_manifest"].is_file()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_plan_one_no_subprocess(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())

    with patch.object(service, "_invoke_magic_pdf") as mock_invoke:
        plan = service.plan_one(sha256=expected_sha)
        assert plan.decision == DECISION_PARSED
        mock_invoke.assert_not_called()

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_cli_dependency_missing_exits(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, _parsed, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    with patch(
        "app.cli.main.check_magic_pdf_available",
        side_effect=RuntimeError("DEPENDENCY_MISSING: magic-pdf not found"),
    ):
        result = CliRunner().invoke(
            app,
            ["parse-mineru-pdf", "--config", str(config_path), "--sha256", expected_sha],
        )

    assert result.exit_code != 0
    assert "DEPENDENCY_MISSING" in result.stdout

    _cleanup_parser_data([prefix], [expected_sha], vault_root)


def test_asset_incomplete_manifest_failed(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())

    with patch.object(service, "_collect_assets", return_value=([], ["copy failed"])):
        result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.partial_count == 1
    assert result.items[0].decision == DECISION_ASSET_INCOMPLETE
    assert result.items[0].status == STATUS_FAILED
    artifacts = build_parsed_artifact_paths(build_parsed_content_dir(parsed_root, expected_sha))
    manifest = json.loads(artifacts["parse_manifest"].read_text(encoding="utf-8"))
    assert manifest["status"] == STATUS_FAILED
    assert manifest["error"]["code"] == DECISION_ASSET_INCOMPLETE

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_vault_original_bin_unchanged(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    vault_dir = build_vault_dir(vault_root, expected_sha)
    original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]
    before_hash = compute_sha256(original_bin)

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    service.parse_many(sha256=expected_sha, limit=1)

    assert compute_sha256(original_bin) == before_hash

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_manual_register_after_parse_success(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    session_factory = _mysql_session_factory()
    content_uid = _content_uid_for_sha(session_factory, expected_sha)
    config = load_config(config_path)
    registry = ParseRegistryService(config)
    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())

    parse_result = service.parse_many(sha256=expected_sha, limit=1, register=False)
    assert parse_result.report_path is not None

    reg_result = registry.register_parse_report(report_path=parse_result.report_path, dry_run=False)
    assert reg_result.artifacts_recorded >= 3

    with session_factory() as session:
        row = session.scalar(
            select(KbParseResult).where(
                KbParseResult.run_uid == reg_result.run_uid,
                KbParseResult.content_uid == content_uid,
            )
        )
        assert row is not None
        assert row.status == STATUS_SUCCESS

    _cleanup_registry_runs([reg_result.run_uid or ""])
    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_batch_report_contains_parser_metadata(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, reports_root, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    result = service.parse_many(sha256=expected_sha, limit=1)

    assert result.report_path is not None
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["parser_name"] == PARSER_NAME
    assert payload["parser_adapter_version"] == PARSER_ADAPTER_VERSION
    assert payload["report_type"] == "parse_mineru_pdf_report"
    assert payload["items"][0]["parsed_dir"]

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)


def test_staging_directory_cleaned_after_parse(
    mineru_env: tuple[InventoryScanner, FileContentVaultService, Path, Path, Path, Path],
    tmp_path: Path,
) -> None:
    _scanner, _vault, vault_root, parsed_root, _reports, config_path = mineru_env
    expected_sha = _prepare_copied_pdf(mineru_env, tmp_path)
    prefix = tmp_path.as_posix()
    staging_root = parsed_root / ".staging"

    service = _mineru_service(config_path, subprocess_runner=_mock_subprocess_success())
    service.parse_many(sha256=expected_sha, limit=1)

    if staging_root.exists():
        assert list(staging_root.glob("mineru_*")) == []

    _cleanup_parser_data([prefix], [expected_sha], vault_root, parsed_root)

