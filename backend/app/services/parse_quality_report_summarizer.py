from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import AppConfig

logger = logging.getLogger(__name__)

INPUT_REPORT_TYPE = "parse_quality_report"
# 016：语义检查开启的报告为 1.1（增量键），关闭时仍为 1.0；两者都可汇总（Owner 裁定 O-4）。
INPUT_SCHEMA_VERSION = "1.0"
INPUT_SCHEMA_VERSIONS = frozenset({"1.0", "1.1"})
INPUT_MODE = "check"

REPORT_TYPE = "parse_quality_summary"
SCHEMA_VERSION = "1.0"
MODE_SUMMARIZE = "summarize"

# 008 既有 18 项（输入报告必须全含；语义零变更）。
LEGACY_ISSUE_CODES: tuple[str, ...] = (
    "MISSING_RAW_VAULT_OBJECT",
    "STALE_RAW_VAULT_PATH",
    "MISSING_PARSED_DIR",
    "MISSING_PARSED_TEXT",
    "MISSING_PARSED_METADATA",
    "MISSING_PARSE_MANIFEST",
    "INVALID_PARSE_MANIFEST_JSON",
    "MANIFEST_REQUIRED_FIELD_MISSING",
    "MANIFEST_SHA256_MISMATCH",
    "MANIFEST_CONTENT_UID_MISMATCH",
    "MANIFEST_PARSER_NAME_INVALID",
    "MANIFEST_ADAPTER_VERSION_MISSING",
    "REGISTRY_ARTIFACT_PATH_MISSING",
    "REGISTRY_STATUS_FILE_MISMATCH",
    "REGISTRY_MISSING_MANIFEST_RESULT",
    "REGISTRY_FAILED_RESULT",
    "REGISTRY_EMPTY_RESULT",
    "REGISTRY_SKIPPED_RESULT",
)

# 016 第五类语义检查 code（追加在后）。
SEMANTIC_ISSUE_CODES: tuple[str, ...] = (
    "OCR_GARBLED_RATIO",
    "OCR_LOW_CONFIDENCE",
    "OCR_CONFIDENCE_UNAVAILABLE",
    "TABLE_STRUCTURE_INCOMPLETE",
    "EMPTY_BUT_NONZERO_PAGES",
)

ISSUE_CODES: tuple[str, ...] = LEGACY_ISSUE_CODES + SEMANTIC_ISSUE_CODES

SUMMARY_REQUIRED_KEYS = (
    "checked_content_count",
    "checked_raw_vault_count",
    "checked_parse_result_count",
    "checked_artifact_count",
    "issue_count",
    "critical_count",
    "error_count",
    "warning_count",
    "info_count",
)

SCOPE_KEYS = ("sha256", "content_uid", "parser_name", "status", "limit")

NOISE_BUCKETS = ("TEST_STALE_PATH", "STALE_VAULT_PATH", "REAL_DEFECT")

STALE_PATH_MARKERS = ("/tmp/", "/var/tmp/", "/private/tmp/")
PYTEST_PATH_MARKER = "/tmp/pytest-of-"
P5_REQA_MARKER = "/tmp/p5_reqa_"

REPORT_FILENAME_PATTERN = re.compile(
    r"^parse_quality_report_(\d{8}T\d{6}Z)\.json$"
)

ALLOWED_SEVERITIES = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO"})
ALLOWED_PARSER_NAMES = frozenset({"markitdown", "mineru", "image_ocr"})


@dataclass
class ParseQualitySummaryResult:
    summary_path: Path
    input_path: Path
    filtered_issue_count: int
    noise_breakdown: dict[str, int]
    exit_code_hint: int


def _utc_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _summary_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _normalize_path(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("\\", "/")


def _issue_paths(issue: dict[str, Any]) -> tuple[str, str]:
    evidence = issue.get("evidence")
    evidence_path = ""
    if isinstance(evidence, dict):
        raw_evidence_path = evidence.get("path")
        if isinstance(raw_evidence_path, str):
            evidence_path = _normalize_path(raw_evidence_path)
    path = issue.get("path")
    main_path = _normalize_path(path) if isinstance(path, str) else ""
    return main_path, evidence_path


def classify_noise_bucket(issue: dict[str, Any]) -> str:
    main_path, evidence_path = _issue_paths(issue)
    combined = f"{main_path} {evidence_path}"

    evidence = issue.get("evidence")
    if isinstance(evidence, dict):
        error_name = evidence.get("error")
        if error_name == "PermissionError":
            return "TEST_STALE_PATH"

    if PYTEST_PATH_MARKER in main_path or PYTEST_PATH_MARKER in evidence_path:
        return "TEST_STALE_PATH"

    issue_code = issue.get("issue_code")
    if issue_code == "STALE_RAW_VAULT_PATH":
        return "STALE_VAULT_PATH"

    if P5_REQA_MARKER in main_path or P5_REQA_MARKER in evidence_path:
        return "STALE_VAULT_PATH"

    for marker in STALE_PATH_MARKERS:
        if marker in combined:
            return "STALE_VAULT_PATH"

    return "REAL_DEFECT"


def _empty_filtered_issue_counts() -> dict[str, int]:
    return {code: 0 for code in ISSUE_CODES}


def _increment(bucket: dict[str, int], key: str | None) -> None:
    label = key if key else "(null)"
    bucket[label] = bucket.get(label, 0) + 1


def _parse_report_filename_timestamp(path: Path) -> datetime | None:
    match = REPORT_FILENAME_PATTERN.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def discover_latest_input_report(reports_root: Path) -> Path:
    candidates: list[tuple[datetime, Path]] = []
    for path in reports_root.glob("parse_quality_report_*.json"):
        if not path.is_file():
            continue
        timestamp = _parse_report_filename_timestamp(path)
        if timestamp is not None:
            candidates.append((timestamp, path))
    if not candidates:
        raise FileNotFoundError(
            f"No parse_quality_report_*.json found under {reports_root}"
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Invalid input: {label} must be an object")
    return value


def _require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Invalid input: {label} must be an integer")
    return value


def validate_input_report(data: dict[str, Any]) -> None:
    if data.get("report_type") != INPUT_REPORT_TYPE:
        raise ValueError(
            f"Invalid input: report_type must be {INPUT_REPORT_TYPE!r}"
        )
    if data.get("schema_version") not in INPUT_SCHEMA_VERSIONS:
        raise ValueError(
            f"Invalid input: schema_version must be one of "
            f"{sorted(INPUT_SCHEMA_VERSIONS)}"
        )
    if data.get("mode") != INPUT_MODE:
        raise ValueError(f"Invalid input: mode must be {INPUT_MODE!r}")

    generated_at = data.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        raise ValueError("Invalid input: generated_at must be a non-empty string")

    scope = _require_dict(data.get("scope"), "scope")
    for key in SCOPE_KEYS:
        if key not in scope:
            raise ValueError(f"Invalid input: scope missing key {key!r}")

    summary = _require_dict(data.get("summary"), "summary")
    for key in SUMMARY_REQUIRED_KEYS:
        if key not in summary:
            raise ValueError(f"Invalid input: summary missing key {key!r}")
        _require_int(summary[key], f"summary.{key}")

    issue_counts = _require_dict(data.get("issue_counts"), "issue_counts")
    unknown_keys = set(issue_counts) - set(ISSUE_CODES)
    if unknown_keys:
        raise ValueError(
            f"Invalid input: issue_counts contains unknown keys: {sorted(unknown_keys)}"
        )
    if data.get("schema_version") == "1.0":
        # fail-closed：016 语义开启时 checker 产 1.1、关闭时产 1.0，
        # 1.0 里出现语义键只可能是生产者 bug；静默接受会让版本语义失效。
        semantic_keys = [code for code in SEMANTIC_ISSUE_CODES if code in issue_counts]
        if semantic_keys:
            raise ValueError(
                "Invalid input: issue_counts must not contain semantic codes when "
                f"schema_version='1.0': {sorted(semantic_keys)}"
            )
    for code in LEGACY_ISSUE_CODES:
        if code not in issue_counts:
            raise ValueError(f"Invalid input: issue_counts missing key {code!r}")
    # 016 语义 code 属增量键：1.0 报告可以不含（增量键兼容），含则必须合法。
    for code in ISSUE_CODES:
        if code not in issue_counts:
            continue
        value = issue_counts[code]
        count = _require_int(value, f"issue_counts.{code}")
        if count < 0:
            raise ValueError(f"Invalid input: issue_counts.{code} must be >= 0")

    for key in ("by_parser", "by_status", "by_route_type", "by_severity"):
        _require_dict(data.get(key), key)

    issues = data.get("issues")
    if not isinstance(issues, list):
        raise ValueError("Invalid input: issues must be an array")

    recommendations = data.get("recommendations")
    if not isinstance(recommendations, list):
        raise ValueError("Invalid input: recommendations must be an array")

    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise ValueError(f"Invalid input: issues[{index}] must be an object")
        for required in ("issue_code", "severity", "message"):
            value = issue.get(required)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"Invalid input: issues[{index}].{required} must be a non-empty string"
                )


def _copy_issue_counts(data: dict[str, Any]) -> dict[str, int]:
    source = data["issue_counts"]
    # 016 语义 code 在 1.0 输入报告中可缺席 → 补 0（增量键兼容）。
    return {code: int(source.get(code, 0)) for code in ISSUE_CODES}


def _filter_issues(
    issues: list[dict[str, Any]],
    *,
    severity: str | None,
    issue_codes: list[str] | None,
    parser_name: str | None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    allowed_codes = set(issue_codes or [])
    for issue in issues:
        if severity is not None and issue.get("severity") != severity:
            continue
        if allowed_codes and issue.get("issue_code") not in allowed_codes:
            continue
        if parser_name is not None and issue.get("parser_name") != parser_name:
            continue
        filtered.append(issue)
    return filtered


def _compute_filtered_issue_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = _empty_filtered_issue_counts()
    for issue in issues:
        code = issue.get("issue_code")
        if isinstance(code, str) and code in counts:
            counts[code] += 1
    return counts


def _compute_aggregations(
    issues: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    by_parser: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_route_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for issue in issues:
        _increment(by_parser, issue.get("parser_name"))
        _increment(by_severity, issue.get("severity"))
        evidence = issue.get("evidence")
        if isinstance(evidence, dict):
            status = evidence.get("result_status")
            if isinstance(status, str):
                _increment(by_status, status)
            route_type = evidence.get("route_type")
            if isinstance(route_type, str):
                _increment(by_route_type, route_type)
    return by_parser, by_status, by_route_type, by_severity


def _compute_noise_breakdown(issues: list[dict[str, Any]]) -> dict[str, int]:
    breakdown = {bucket: 0 for bucket in NOISE_BUCKETS}
    for issue in issues:
        bucket = classify_noise_bucket(issue)
        breakdown[bucket] += 1
    return breakdown


def _compute_severity_summary(issues: list[dict[str, Any]]) -> dict[str, int]:
    summary = {level: 0 for level in sorted(ALLOWED_SEVERITIES)}
    for issue in issues:
        severity = issue.get("severity")
        if isinstance(severity, str) and severity in summary:
            summary[severity] += 1
    return summary


def _top_issue_codes(
    filtered_issue_counts: dict[str, int], *, limit: int = 5
) -> list[dict[str, Any]]:
    ranked = sorted(
        (
            {"issue_code": code, "count": count}
            for code, count in filtered_issue_counts.items()
            if count > 0
        ),
        key=lambda item: (-item["count"], item["issue_code"]),
    )
    return ranked[:limit]


def _build_recommendations(
    input_recommendations: list[Any],
    filtered_issues: list[dict[str, Any]],
    noise_breakdown: dict[str, int],
) -> list[str]:
    filtered_codes = {issue.get("issue_code") for issue in filtered_issues}
    selected: list[str] = []
    for item in input_recommendations:
        if not isinstance(item, str) or not item:
            continue
        if any(code in item for code in filtered_codes if isinstance(code, str)):
            selected.append(item)
        elif not filtered_issues and item not in selected:
            selected.append(item)
    if not selected:
        selected = [
            str(item)
            for item in input_recommendations
            if isinstance(item, str) and item
        ]

    notes: list[str] = []
    if noise_breakdown.get("TEST_STALE_PATH", 0) > 0:
        notes.append(
            "TEST_STALE_PATH issues are likely pytest temp-path noise; "
            "review before treating as production defects."
        )
    if noise_breakdown.get("STALE_VAULT_PATH", 0) > 0:
        notes.append(
            "STALE_VAULT_PATH issues point to stale temp vault locations; "
            "no automatic repair is performed by summarize-parse-quality."
        )
    if noise_breakdown.get("REAL_DEFECT", 0) > 0:
        notes.append(
            "REAL_DEFECT issues should be triaged against 008 recommendations "
            "before any manual repair or re-parse."
        )

    combined: list[str] = []
    for item in selected + notes:
        if item not in combined:
            combined.append(item)
    return combined


def _default_output_path(
    reports_root: Path, *, output_format: str, explicit_output: Path | None
) -> Path:
    if explicit_output is not None:
        return explicit_output
    suffix = ".json" if output_format == "json" else ".md"
    return reports_root / f"parse_quality_summary_{_summary_timestamp()}{suffix}"


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Parse Quality Summary",
        "",
        "## Source Report",
        "",
        f"- Path: `{payload['source_report_path']}`",
        f"- Generated at: `{payload['source_report_generated_at']}`",
        f"- Scope: `{json.dumps(payload['source_scope'], ensure_ascii=False)}`",
        "",
        "## Executive Summary",
        "",
        f"- Input issue count: {payload['summary']['input_issue_count']}",
        f"- Filtered issue count: {payload['summary']['filtered_issue_count']}",
        f"- Critical: {payload['summary']['critical_count']}",
        f"- Error: {payload['summary']['error_count']}",
        f"- Warning: {payload['summary']['warning_count']}",
        f"- Info: {payload['summary']['info_count']}",
        "",
        "## Issue Code Matrix",
        "",
        "| Issue Code | Input Count | Filtered Count |",
        "|---|---:|---:|",
    ]
    for code in ISSUE_CODES:
        lines.append(
            f"| `{code}` | {payload['issue_counts'][code]} | "
            f"{payload['filtered_issue_counts'][code]} |"
        )

    lines.extend(["", "## Aggregations", ""])
    for label, key in (
        ("Severity", "by_severity"),
        ("Parser", "by_parser"),
        ("Status", "by_status"),
        ("Route Type", "by_route_type"),
    ):
        lines.append(f"### By {label}")
        lines.append("")
        bucket = payload[key]
        if bucket:
            for name, count in sorted(bucket.items(), key=lambda item: (-item[1], item[0])):
                lines.append(f"- {name}: {count}")
        else:
            lines.append("- (none)")
        lines.append("")

    lines.extend(["## Noise Classification", ""])
    for bucket in NOISE_BUCKETS:
        lines.append(f"- {bucket}: {payload['noise_breakdown'][bucket]}")
    lines.extend(["", "## Top Issues", ""])
    for issue in payload["sample_issues"]:
        path = issue.get("path") or "(null)"
        lines.append(
            f"- [{issue.get('severity')}] `{issue.get('issue_code')}` "
            f"{issue.get('message')} (`{path}`)"
        )
    if not payload["sample_issues"]:
        lines.append("- (none)")

    lines.extend(["", "## Recommendations", ""])
    for item in payload["recommendations"]:
        lines.append(f"- {item}")
    if not payload["recommendations"]:
        lines.append("- (none)")

    lines.append("")
    return "\n".join(lines)


def _build_summary_payload(
    *,
    input_path: Path,
    data: dict[str, Any],
    filtered_issues: list[dict[str, Any]],
    filters: dict[str, Any],
    top: int,
) -> dict[str, Any]:
    all_issues = data["issues"]
    filtered_issue_counts = _compute_filtered_issue_counts(filtered_issues)
    by_parser, by_status, by_route_type, by_severity = _compute_aggregations(
        filtered_issues
    )
    noise_breakdown = _compute_noise_breakdown(filtered_issues)
    severity_summary = _compute_severity_summary(filtered_issues)

    return {
        "report_type": REPORT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "mode": MODE_SUMMARIZE,
        "generated_at": _utc_iso(),
        "source_report_path": str(input_path.resolve()),
        "source_report_generated_at": data["generated_at"],
        "source_scope": data["scope"],
        "filters": filters,
        "summary": {
            "input_issue_count": len(all_issues),
            "filtered_issue_count": len(filtered_issues),
            "critical_count": severity_summary["CRITICAL"],
            "error_count": severity_summary["ERROR"],
            "warning_count": severity_summary["WARNING"],
            "info_count": severity_summary["INFO"],
        },
        "issue_counts": _copy_issue_counts(data),
        "filtered_issue_counts": filtered_issue_counts,
        "by_parser": by_parser,
        "by_status": by_status,
        "by_route_type": by_route_type,
        "by_severity": by_severity,
        "noise_breakdown": noise_breakdown,
        "top_issue_codes": _top_issue_codes(filtered_issue_counts),
        "sample_issues": filtered_issues[:top],
        "recommendations": _build_recommendations(
            data["recommendations"], filtered_issues, noise_breakdown
        ),
    }


class ParseQualityReportSummarizerService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def summarize(
        self,
        *,
        input_path: Path | None = None,
        output: Path | None = None,
        output_format: str = "markdown",
        severity: str | None = None,
        issue_codes: list[str] | None = None,
        parser_name: str | None = None,
        top: int = 20,
        fail_on_issue: bool = False,
    ) -> ParseQualitySummaryResult:
        if output_format not in {"markdown", "json"}:
            raise ValueError("output_format must be 'markdown' or 'json'")
        if top < 1:
            raise ValueError("top must be >= 1")
        if severity is not None and severity not in ALLOWED_SEVERITIES:
            raise ValueError(f"severity must be one of {sorted(ALLOWED_SEVERITIES)}")
        if parser_name is not None and parser_name not in ALLOWED_PARSER_NAMES:
            raise ValueError(
                f"parser_name must be one of {sorted(ALLOWED_PARSER_NAMES)}"
            )
        if issue_codes:
            unknown = [code for code in issue_codes if code not in ISSUE_CODES]
            if unknown:
                raise ValueError(f"Unknown issue codes: {unknown}")

        reports_root = self.config.storage.reports_root
        if not reports_root.exists():
            raise FileNotFoundError(f"reports_root does not exist: {reports_root}")

        resolved_input = input_path or discover_latest_input_report(reports_root)
        if not resolved_input.is_file():
            raise FileNotFoundError(f"Input report not found: {resolved_input}")

        with resolved_input.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        validate_input_report(data)

        filters = {
            "severity": severity,
            "issue_codes": list(issue_codes or []),
            "parser_name": parser_name,
        }
        filtered_issues = _filter_issues(
            data["issues"],
            severity=severity,
            issue_codes=issue_codes,
            parser_name=parser_name,
        )
        payload = _build_summary_payload(
            input_path=resolved_input,
            data=data,
            filtered_issues=filtered_issues,
            filters=filters,
            top=top,
        )

        if output is not None and output.suffix.lower() == ".json":
            effective_format = "json"
        elif output is not None and output.suffix.lower() == ".md":
            effective_format = "markdown"
        else:
            effective_format = output_format

        summary_path = _default_output_path(
            reports_root,
            output_format=effective_format,
            explicit_output=output,
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)

        if effective_format == "json":
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        else:
            content = _render_markdown(payload)

        temp_path = summary_path.with_suffix(summary_path.suffix + ".tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(summary_path)

        exit_code_hint = 0
        if fail_on_issue and len(filtered_issues) > 0:
            exit_code_hint = 2

        logger.info("Wrote parse quality summary to %s", summary_path)
        return ParseQualitySummaryResult(
            summary_path=summary_path,
            input_path=resolved_input,
            filtered_issue_count=len(filtered_issues),
            noise_breakdown=payload["noise_breakdown"],
            exit_code_hint=exit_code_hint,
        )
