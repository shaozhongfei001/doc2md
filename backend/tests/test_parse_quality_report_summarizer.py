from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from app.cli.main import app
from app.core.config import AppConfig, MysqlConfig, RawConfig, StorageConfig
from app.services.parse_quality_checker import ISSUE_CODES as CHECKER_ISSUE_CODES
from app.services.parse_quality_report_summarizer import (
    ISSUE_CODES,
    ParseQualityReportSummarizerService,
    classify_noise_bucket,
    discover_latest_input_report,
    validate_input_report,
)

cli_runner = CliRunner()
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _test_config(workspace_root: Path) -> AppConfig:
    return AppConfig(
        pipeline_version="v1.1",
        storage=StorageConfig(
            source_registry_root=workspace_root / "source_registry",
            raw_vault_root=workspace_root / "raw_vault",
            parsed_root=workspace_root / "parsed",
            curated_root=workspace_root / "curated",
            quarantine_root=workspace_root / "quarantine",
            reports_root=workspace_root / "reports",
        ),
        mysql=MysqlConfig(
            host="127.0.0.1",
            port=3306,
            database="personal_kb",
            username="personal_kb",
            password="test",
            charset="utf8mb4",
        ),
        raw=RawConfig(
            original_files_readonly=True,
            copy_unique_content_to_vault=True,
        ),
    )


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_input_report(reports_root: Path, name: str, payload: dict) -> Path:
    reports_root.mkdir(parents=True, exist_ok=True)
    path = reports_root / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def summarizer_env(tmp_path: Path):
    reports_root = tmp_path / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    config = _test_config(tmp_path)
    return config, reports_root


def test_issue_codes_match_008_checker():
    assert ISSUE_CODES == CHECKER_ISSUE_CODES


def test_valid_report_markdown_summary(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(input_path=input_path, output_format="markdown")

    assert result.summary_path.is_file()
    assert result.filtered_issue_count == 2
    content = result.summary_path.read_text(encoding="utf-8")
    assert "# Parse Quality Summary" in content
    assert "Issue Code Matrix" in content
    for code in ISSUE_CODES:
        assert code in content


def test_valid_report_json_summary(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    output_path = reports_root / "summary.json"
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(
        input_path=input_path,
        output=output_path,
        output_format="json",
    )

    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert data["report_type"] == "parse_quality_summary"
    assert data["schema_version"] == "1.0"
    assert data["mode"] == "summarize"
    assert set(data["issue_counts"]) == set(ISSUE_CODES)
    assert "noise_breakdown" in data


def test_invalid_report_type_rejected(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_invalid_schema.json")
    input_path = _write_input_report(reports_root, "invalid.json", payload)
    service = ParseQualityReportSummarizerService(config)
    with pytest.raises(ValueError, match="report_type"):
        service.summarize(input_path=input_path)


def test_missing_issue_code_rejected(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_missing_issue_code.json")
    input_path = _write_input_report(reports_root, "missing_code.json", payload)
    service = ParseQualityReportSummarizerService(config)
    with pytest.raises(ValueError, match="issue_counts missing key"):
        service.summarize(input_path=input_path)


def test_empty_issues_summary(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_empty_issues.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T140000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(input_path=input_path)
    assert result.filtered_issue_count == 0
    assert result.exit_code_hint == 0


def test_noise_classification_buckets():
    permission_issue = {
        "issue_code": "STALE_RAW_VAULT_PATH",
        "path": "/tmp/pytest-of-root/test0/file",
        "evidence": {"error": "PermissionError", "path": "/tmp/pytest-of-root/test0/file"},
    }
    assert classify_noise_bucket(permission_issue) == "TEST_STALE_PATH"

    stale_issue = {
        "issue_code": "STALE_RAW_VAULT_PATH",
        "path": "/tmp/p5_reqa_vault/original.bin",
        "evidence": {"path": "/tmp/p5_reqa_vault/original.bin"},
    }
    assert classify_noise_bucket(stale_issue) == "STALE_VAULT_PATH"

    real_issue = {
        "issue_code": "MISSING_PARSED_TEXT",
        "path": "/workspace/parsed/parsed_text.md",
        "evidence": {},
    }
    assert classify_noise_bucket(real_issue) == "REAL_DEFECT"


def test_noise_summary_counts(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_with_noise.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T130000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(input_path=input_path, output_format="json")
    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert data["noise_breakdown"]["TEST_STALE_PATH"] == 1
    assert data["noise_breakdown"]["STALE_VAULT_PATH"] == 1
    assert data["noise_breakdown"]["REAL_DEFECT"] == 1


def test_severity_filter(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(input_path=input_path, severity="ERROR", output_format="json")
    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert result.filtered_issue_count == 1
    assert data["summary"]["filtered_issue_count"] == 1


def test_issue_code_filter(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(
        input_path=input_path,
        issue_codes=["REGISTRY_SKIPPED_RESULT"],
        output_format="json",
    )
    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert result.filtered_issue_count == 1
    assert data["filtered_issue_counts"]["REGISTRY_SKIPPED_RESULT"] == 1


def test_fail_on_issue_exit_hint(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(input_path=input_path, fail_on_issue=True)
    assert result.exit_code_hint == 2
    assert result.summary_path.is_file()


def test_chinese_path_in_markdown(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_with_noise.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T130000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(input_path=input_path)
    content = result.summary_path.read_text(encoding="utf-8")
    assert "中文目录" in content


def test_input_report_not_modified(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    before = input_path.read_bytes()
    service = ParseQualityReportSummarizerService(config)
    service.summarize(input_path=input_path)
    assert input_path.read_bytes() == before


def test_discover_latest_input_report(summarizer_env):
    _, reports_root = summarizer_env
    older = reports_root / "parse_quality_report_20260615T120000Z.json"
    newer = reports_root / "parse_quality_report_20260616T130000Z.json"
    older.write_text("{}", encoding="utf-8")
    newer.write_text("{}", encoding="utf-8")
    assert discover_latest_input_report(reports_root) == newer


def test_no_mysql_connection(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    with patch("app.core.database.create_db_engine") as create_engine:
        with patch("app.core.database.create_session_factory") as create_factory:
            service.summarize(input_path=input_path)
            create_engine.assert_not_called()
            create_factory.assert_not_called()


def test_no_checker_or_parser_invocation(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    with patch("app.services.parse_quality_checker.ParseQualityCheckerService") as checker:
        with patch("app.services.markitdown_parser.MarkItDownParserService") as markitdown:
            with patch("app.services.mineru_pdf_parser.MineruPdfParserService") as mineru:
                service.summarize(input_path=input_path)
                checker.assert_not_called()
                markitdown.assert_not_called()
                mineru.assert_not_called()


def test_no_raw_vault_or_parsed_reads(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    original_is_file = Path.is_file
    touched: list[str] = []

    def tracking_is_file(self: Path) -> bool:
        normalized = str(self).replace("\\", "/")
        if "/raw_vault/" in normalized or "/parsed/" in normalized:
            touched.append(normalized)
        return original_is_file(self)

    with patch.object(Path, "is_file", tracking_is_file):
        service.summarize(input_path=input_path)

    assert touched == []


def test_cli_smoke_markdown(summarizer_env, tmp_path: Path):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        f"""
app:
  pipeline_version: v1.1
storage:
  source_registry_root: {tmp_path / "source_registry"}
  raw_vault_root: {tmp_path / "raw_vault"}
  parsed_root: {tmp_path / "parsed"}
  curated_root: {tmp_path / "curated"}
  quarantine_root: {tmp_path / "quarantine"}
  reports_root: {reports_root}
mysql:
  host: 127.0.0.1
  port: 3306
  database: personal_kb
  username: personal_kb
  password: test
raw:
  original_files_readonly: true
  copy_unique_content_to_vault: true
""".strip(),
        encoding="utf-8",
    )
    result = cli_runner.invoke(
        app,
        [
            "summarize-parse-quality",
            "--config",
            str(config_path),
            "--input",
            str(input_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Parse quality summary:" in result.output


def test_cli_fail_on_issue_exit_code(summarizer_env, tmp_path: Path):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        f"""
app:
  pipeline_version: v1.1
storage:
  source_registry_root: {tmp_path / "source_registry"}
  raw_vault_root: {tmp_path / "raw_vault"}
  parsed_root: {tmp_path / "parsed"}
  curated_root: {tmp_path / "curated"}
  quarantine_root: {tmp_path / "quarantine"}
  reports_root: {reports_root}
mysql:
  host: 127.0.0.1
  port: 3306
  database: personal_kb
  username: personal_kb
  password: test
raw:
  original_files_readonly: true
  copy_unique_content_to_vault: true
""".strip(),
        encoding="utf-8",
    )
    result = cli_runner.invoke(
        app,
        [
            "summarize-parse-quality",
            "--config",
            str(config_path),
            "--input",
            str(input_path),
            "--fail-on-issue",
        ],
    )
    assert result.exit_code == 2, result.output


def test_validate_input_report_requires_scope_keys():
    payload = _load_fixture("parse_quality_report_valid.json")
    payload["scope"] = {"sha256": None}
    with pytest.raises(ValueError, match="scope missing key"):
        validate_input_report(payload)


# --- P5 QA supplemental negative / contract tests ---


def test_invalid_schema_version_rejected(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    payload["schema_version"] = "2.0"
    input_path = _write_input_report(reports_root, "bad_schema.json", payload)
    service = ParseQualityReportSummarizerService(config)
    with pytest.raises(ValueError, match="schema_version"):
        service.summarize(input_path=input_path)


def test_unknown_issue_counts_key_rejected(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    payload["issue_counts"]["UNKNOWN_CODE"] = 1
    input_path = _write_input_report(reports_root, "unknown_key.json", payload)
    service = ParseQualityReportSummarizerService(config)
    with pytest.raises(ValueError, match="unknown keys"):
        service.summarize(input_path=input_path)


def test_malformed_json_rejected(summarizer_env):
    config, reports_root = summarizer_env
    input_path = reports_root / "broken.json"
    input_path.write_text("{not-json", encoding="utf-8")
    service = ParseQualityReportSummarizerService(config)
    with pytest.raises(json.JSONDecodeError):
        service.summarize(input_path=input_path)


def test_missing_input_file_rejected(summarizer_env):
    config, reports_root = summarizer_env
    service = ParseQualityReportSummarizerService(config)
    with pytest.raises(FileNotFoundError, match="Input report not found"):
        service.summarize(input_path=reports_root / "missing.json")


def test_parser_name_filter(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(
        input_path=input_path,
        parser_name="markitdown",
        output_format="json",
    )
    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert result.filtered_issue_count == 1
    assert all(
        issue.get("parser_name") == "markitdown" for issue in data["sample_issues"]
    )


def test_top_truncation(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    payload["issues"] = [
        {
            "issue_code": "MISSING_PARSED_TEXT",
            "severity": "ERROR",
            "content_uid": f"content-{index}",
            "sha256": f"sha{index}",
            "parser_name": "markitdown",
            "parser_adapter_version": "1.0.0",
            "artifact_type": "parsed_text",
            "path": f"/workspace/parsed/{index}/parsed_text.md",
            "message": "missing",
            "evidence": {},
        }
        for index in range(10)
    ]
    payload["summary"]["issue_count"] = 10
    payload["issue_counts"]["MISSING_PARSED_TEXT"] = 10
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T150000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(input_path=input_path, top=5, output_format="json")
    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert len(data["sample_issues"]) == 5


def test_classification_priority_permission_over_stale():
    issue = {
        "issue_code": "STALE_RAW_VAULT_PATH",
        "path": "/tmp/p5_reqa_vault/original.bin",
        "evidence": {
            "error": "PermissionError",
            "path": "/tmp/pytest-of-root/test0/file",
        },
    }
    assert classify_noise_bucket(issue) == "TEST_STALE_PATH"


def test_issue_counts_unchanged_when_filtered(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(
        input_path=input_path,
        severity="ERROR",
        output_format="json",
    )
    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    expected = {code: payload["issue_counts"].get(code, 0) for code in ISSUE_CODES}
    assert data["issue_counts"] == expected
    assert result.filtered_issue_count == 1


def test_only_summary_output_written(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    before = {path.name for path in reports_root.iterdir()}
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(
        input_path=input_path,
        output=reports_root / "explicit_summary.md",
    )
    after = {path.name for path in reports_root.iterdir()}
    assert after - before == {"explicit_summary.md"}
    assert input_path.name in after
    assert result.summary_path.name == "explicit_summary.md"


def test_cli_invalid_issue_code_exit_1(summarizer_env, tmp_path: Path):
    _, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_valid.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T120000Z.json", payload
    )
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        f"""
app:
  pipeline_version: v1.1
storage:
  source_registry_root: {tmp_path / "source_registry"}
  raw_vault_root: {tmp_path / "raw_vault"}
  parsed_root: {tmp_path / "parsed"}
  curated_root: {tmp_path / "curated"}
  quarantine_root: {tmp_path / "quarantine"}
  reports_root: {reports_root}
mysql:
  host: 127.0.0.1
  port: 3306
  database: personal_kb
  username: personal_kb
  password: test
raw:
  original_files_readonly: true
  copy_unique_content_to_vault: true
""".strip(),
        encoding="utf-8",
    )
    result = cli_runner.invoke(
        app,
        [
            "summarize-parse-quality",
            "--config",
            str(config_path),
            "--input",
            str(input_path),
            "--issue-code",
            "NOT_A_REAL_CODE",
        ],
    )
    assert result.exit_code == 1, result.output


def test_fail_on_issue_zero_after_filter_exit_0(summarizer_env):
    config, reports_root = summarizer_env
    payload = _load_fixture("parse_quality_report_empty_issues.json")
    input_path = _write_input_report(
        reports_root, "parse_quality_report_20260616T140000Z.json", payload
    )
    service = ParseQualityReportSummarizerService(config)
    result = service.summarize(input_path=input_path, fail_on_issue=True)
    assert result.exit_code_hint == 0
