from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import PROJECT_ROOT, AppConfig, ensure_readonly
from app.core.database import create_db_engine, create_session_factory
from app.core.parsed_paths import build_parsed_artifact_paths, build_parsed_content_dir
from app.core.vault_paths import build_vault_artifact_paths
from app.models.file import KbFileContent
from app.models.parse_registry import KbParseResult, KbParsedArtifact
from app.models.vault import KbRawVaultObject

logger = logging.getLogger(__name__)

REPORT_TYPE = "parse_quality_report"
# 语义检查开启时的报告版本（016）；关闭时回落 SCHEMA_VERSION_SEMANTIC_DISABLED。
SCHEMA_VERSION = "1.1"
SCHEMA_VERSION_SEMANTIC_DISABLED = "1.0"
MODE_CHECK = "check"

# 015 新增 parser_name="image_ocr"；016 扩展白名单使其 manifest 不再被判非法。
ALLOWED_PARSER_NAMES = frozenset({"markitdown", "mineru", "image_ocr"})
ALLOWED_RESULT_STATUSES = frozenset({"SUCCESS", "EMPTY", "SKIPPED", "FAILED"})
STALE_PATH_MARKERS = ("/tmp/", "/var/tmp/", "/private/tmp/")

# 008 既有 18 项结构检查 code（语义零变更）。
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

# 016 第五类语义检查 code（既有在前、追加在后）。
SEMANTIC_ISSUE_CODES: tuple[str, ...] = (
    "OCR_GARBLED_RATIO",
    "OCR_LOW_CONFIDENCE",
    "OCR_CONFIDENCE_UNAVAILABLE",
    "TABLE_STRUCTURE_INCOMPLETE",
    "EMPTY_BUT_NONZERO_PAGES",
)

ISSUE_CODES: tuple[str, ...] = LEGACY_ISSUE_CODES + SEMANTIC_ISSUE_CODES

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

ERROR_MISSING_MANIFEST = "MISSING_MANIFEST"

COMMON_MANIFEST_REQUIRED_FIELDS = (
    "content_uid",
    "sha256",
    "parser_name",
    "parser_adapter_version",
    "status",
    "generated_at",
)
MINERU_MANIFEST_REQUIRED_FIELDS = ("parser_profile",)
SUCCESS_MANIFEST_ARTIFACT_FIELDS = ("parsed_text_path", "parsed_metadata_path")

RECOMMENDATION_BY_CODE: dict[str, str] = {
    "MISSING_RAW_VAULT_OBJECT": (
        "Re-run copy-to-vault for missing raw vault object after manual review."
    ),
    "STALE_RAW_VAULT_PATH": (
        "Review stale /tmp vault path and rebuild vault object if needed."
    ),
    "MISSING_PARSED_DIR": (
        "Re-run parser only after raw vault sample is restored."
    ),
    "MISSING_PARSED_TEXT": (
        "Re-run parser only after raw vault sample is restored."
    ),
    "MISSING_PARSED_METADATA": (
        "Re-run parser only after raw vault sample is restored."
    ),
    "MISSING_PARSE_MANIFEST": (
        "Re-run parser or reconcile-parsed-artifacts after manual review."
    ),
    "INVALID_PARSE_MANIFEST_JSON": (
        "Inspect parse_manifest.json manually; re-run parser if repair is approved."
    ),
    "MANIFEST_REQUIRED_FIELD_MISSING": (
        "Inspect parse_manifest.json against the 005/007 contract."
    ),
    "MANIFEST_SHA256_MISMATCH": (
        "Verify content identity and re-parse only after manual approval."
    ),
    "MANIFEST_CONTENT_UID_MISMATCH": (
        "Verify content identity and re-parse only after manual approval."
    ),
    "MANIFEST_PARSER_NAME_INVALID": (
        "Review parser routing and registry records for this content."
    ),
    "MANIFEST_ADAPTER_VERSION_MISSING": (
        "Inspect parse_manifest.json for missing parser_adapter_version."
    ),
    "REGISTRY_ARTIFACT_PATH_MISSING": (
        "Reconcile registry artifact paths or re-run parser after manual review."
    ),
    "REGISTRY_STATUS_FILE_MISMATCH": (
        "Registry SUCCESS contradicts filesystem; review before re-parsing."
    ),
    "REGISTRY_MISSING_MANIFEST_RESULT": (
        "Review registry result with missing manifest before re-parsing."
    ),
    "REGISTRY_FAILED_RESULT": (
        "Review failed parse result; re-run parser only after root cause is fixed."
    ),
    "REGISTRY_EMPTY_RESULT": (
        "Review empty parse result to confirm source content is expected to be empty."
    ),
    "REGISTRY_SKIPPED_RESULT": (
        "Skipped parse result recorded for review; no automatic action required."
    ),
    "OCR_GARBLED_RATIO": (
        "Inspect parsed_text.md for OCR garbage; fix the OCR profile and re-parse "
        "only after manual approval."
    ),
    "OCR_LOW_CONFIDENCE": (
        "Review low OCR confidence sample; consider a higher OCR profile or manual "
        "transcription before indexing."
    ),
    "OCR_CONFIDENCE_UNAVAILABLE": (
        "OCR confidence is not reported by this parser run; do not treat the text as "
        "verified. No automatic action required."
    ),
    "TABLE_STRUCTURE_INCOMPLETE": (
        "Inspect the reported Markdown table rows; repair the table manually or "
        "re-parse with a table-aware profile."
    ),
    "EMPTY_BUT_NONZERO_PAGES": (
        "Registry reports SUCCESS for an empty body while the source is non-empty; "
        "review the parser output and re-parse only after manual approval."
    ),
}


# --- 016 语义检查配置与判定表（config/quality_semantics.yaml） ------------------------

SEMANTIC_CONFIG_FILENAME = "quality_semantics.yaml"
DEFAULT_MIN_SAMPLE_CHARS = 200
DEFAULT_GARBLED_WARN_RATIO = 0.02
DEFAULT_GARBLED_ERROR_RATIO = 0.10
DEFAULT_CONFIDENCE_MIN_MEAN = 0.70
DEFAULT_OCR_PARSER_NAMES: tuple[str, ...] = ("image_ocr", "mineru")

# 乱码判定表冻结在代码里（yaml 只调阈值）：U+FFFD、C0/C1 控制符（除 \n \t \r）、
# 代理区、私有区、Unicode 非字符，以及非常用符号区块的连续 run。
GARBLED_SYMBOL_RUN_MIN = 3
SUSPICIOUS_SYMBOL_RANGES: tuple[tuple[int, int], ...] = (
    (0x2500, 0x257F),  # Box Drawing
    (0x2580, 0x259F),  # Block Elements
    (0x25A0, 0x25FF),  # Geometric Shapes
    (0x2B00, 0x2BFF),  # Miscellaneous Symbols and Arrows
)
ALLOWED_TEXT_CONTROL_CHARS = frozenset({"\n", "\t", "\r"})
MARKDOWN_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
TABLE_LINE_NUMBERS_LIMIT = 20


@dataclass(frozen=True)
class SemanticCheckConfig:
    """016 语义检查阈值载体。默认值 = Owner 裁定 O-3（FROZEN）。"""

    enabled: bool = True
    min_sample_chars: int = DEFAULT_MIN_SAMPLE_CHARS
    garbled_warn_ratio: float = DEFAULT_GARBLED_WARN_RATIO
    garbled_error_ratio: float = DEFAULT_GARBLED_ERROR_RATIO
    confidence_min_mean: float = DEFAULT_CONFIDENCE_MIN_MEAN
    ocr_parser_names: tuple[str, ...] = DEFAULT_OCR_PARSER_NAMES
    tables_enabled: bool = True
    empty_text_enabled: bool = True

    def __post_init__(self) -> None:
        if self.min_sample_chars < 0:
            raise ValueError(
                f"garbled.min_sample_chars must be >= 0: {self.min_sample_chars!r}"
            )
        for label, value in (
            ("garbled.warn_ratio", self.garbled_warn_ratio),
            ("garbled.error_ratio", self.garbled_error_ratio),
            ("confidence.min_mean", self.confidence_min_mean),
        ):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{label} must be within [0.0, 1.0]: {value!r}")
        if not self.ocr_parser_names:
            raise ValueError("confidence.ocr_parser_names must not be empty")


def _config_mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"quality_semantics.yaml: {label} must be a mapping")
    return value


def _config_float(value: Any, label: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"quality_semantics.yaml: {label} must be a number")
    return float(value)


def _config_int(value: Any, label: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"quality_semantics.yaml: {label} must be an integer")
    return value


def _config_bool(value: Any, label: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"quality_semantics.yaml: {label} must be a boolean")
    return value


def _config_parser_names(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_OCR_PARSER_NAMES
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(
            "quality_semantics.yaml: confidence.ocr_parser_names must be a list of "
            "non-empty strings"
        )
    return tuple(value)


def load_semantic_check_config(path: Path | None = None) -> SemanticCheckConfig:
    """读取 ``config/quality_semantics.yaml``；文件/字段缺失回落代码默认值（WARNING 一次）。

    阈值非法（负数 / >1）→ ``ValueError``，不静默降级（plan §8）。
    """

    config_path = path or (PROJECT_ROOT / "config" / SEMANTIC_CONFIG_FILENAME)
    if not config_path.is_file():
        logger.warning(
            "quality_semantics.yaml not found at %s; using 016 defaults", config_path
        )
        return SemanticCheckConfig()

    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read quality_semantics.yaml: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"quality_semantics.yaml is not valid YAML: {exc}") from exc

    if payload is None:
        logger.warning("quality_semantics.yaml is empty; using 016 defaults")
        return SemanticCheckConfig()
    if not isinstance(payload, dict):
        raise ValueError("quality_semantics.yaml root must be a mapping")

    section = _config_mapping(payload.get("semantic_checks"), "semantic_checks")
    garbled = _config_mapping(section.get("garbled"), "semantic_checks.garbled")
    confidence = _config_mapping(
        section.get("confidence"), "semantic_checks.confidence"
    )
    tables = _config_mapping(section.get("tables"), "semantic_checks.tables")
    empty_text = _config_mapping(
        section.get("empty_text"), "semantic_checks.empty_text"
    )

    return SemanticCheckConfig(
        enabled=_config_bool(section.get("enabled"), "semantic_checks.enabled", True),
        min_sample_chars=_config_int(
            garbled.get("min_sample_chars"),
            "semantic_checks.garbled.min_sample_chars",
            DEFAULT_MIN_SAMPLE_CHARS,
        ),
        garbled_warn_ratio=_config_float(
            garbled.get("warn_ratio"),
            "semantic_checks.garbled.warn_ratio",
            DEFAULT_GARBLED_WARN_RATIO,
        ),
        garbled_error_ratio=_config_float(
            garbled.get("error_ratio"),
            "semantic_checks.garbled.error_ratio",
            DEFAULT_GARBLED_ERROR_RATIO,
        ),
        confidence_min_mean=_config_float(
            confidence.get("min_mean"),
            "semantic_checks.confidence.min_mean",
            DEFAULT_CONFIDENCE_MIN_MEAN,
        ),
        ocr_parser_names=_config_parser_names(confidence.get("ocr_parser_names")),
        tables_enabled=_config_bool(
            tables.get("enabled"), "semantic_checks.tables.enabled", True
        ),
        empty_text_enabled=_config_bool(
            empty_text.get("enabled"), "semantic_checks.empty_text.enabled", True
        ),
    )


def _is_garbled_char(ch: str) -> bool:
    """单个字符是否属于乱码类别（纯函数，判定表冻结）。"""

    code = ord(ch)
    if code == 0xFFFD:
        return True
    if code < 0x20 and ch not in ALLOWED_TEXT_CONTROL_CHARS:
        return True
    if 0x7F <= code <= 0x9F:
        return True
    if 0xD800 <= code <= 0xDFFF:
        return True
    if 0xE000 <= code <= 0xF8FF:
        return True
    if 0xF0000 <= code <= 0xFFFFD or 0x100000 <= code <= 0x10FFFD:
        return True
    if 0xFDD0 <= code <= 0xFDEF:
        return True
    if (code & 0xFFFE) == 0xFFFE:
        return True
    return False


def _is_suspicious_symbol(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in SUSPICIOUS_SYMBOL_RANGES)


def count_garbled_chars(text: str) -> int:
    """统计乱码字符数：单个乱码类别 + 非常用符号连续 run（≥GARBLED_SYMBOL_RUN_MIN）。"""

    count = 0
    run = 0
    for ch in text:
        if _is_garbled_char(ch):
            count += 1
            run = 0
            continue
        if _is_suspicious_symbol(ch):
            run += 1
            if run == GARBLED_SYMBOL_RUN_MIN:
                count += run
            elif run > GARBLED_SYMBOL_RUN_MIN:
                count += 1
            continue
        run = 0
    return count


def _is_pipe_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def split_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return stripped.split("|")


def _is_separator_row(line: str) -> bool:
    cells = [cell.strip() for cell in split_table_cells(line)]
    if not cells:
        return False
    return all(MARKDOWN_SEPARATOR_CELL.match(cell) for cell in cells)


def analyze_markdown_tables(text: str) -> tuple[int, tuple[int, ...]]:
    """返回 ``(表格总数, 残缺表格的表头行号元组)``。

    残缺判定（容差 0）：分隔行缺失 / 分隔行列数 ≠ 表头列数 / 任一数据行列数 ≠ 表头列数。
    """

    lines = text.splitlines()
    total_tables = 0
    incomplete_lines: list[int] = []
    index = 0
    total_lines = len(lines)
    while index < total_lines:
        if not _is_pipe_table_line(lines[index]):
            index += 1
            continue
        start = index
        block: list[tuple[int, str]] = []
        while index < total_lines and _is_pipe_table_line(lines[index]):
            block.append((index + 1, lines[index]))
            index += 1
        total_tables += 1
        if _table_block_is_incomplete(block):
            incomplete_lines.append(start + 1)
    return total_tables, tuple(incomplete_lines)


def _table_block_is_incomplete(block: list[tuple[int, str]]) -> bool:
    if len(block) < 2:
        return True
    header_cells = len(split_table_cells(block[0][1]))
    if not _is_separator_row(block[1][1]):
        return True
    if len(split_table_cells(block[1][1])) != header_cells:
        return True
    for _line_number, line in block[2:]:
        if len(split_table_cells(line)) != header_cells:
            return True
    return False


def extract_confidence_mean(metadata: dict[str, Any]) -> float | None:
    """从 ``parsed_metadata.json`` 取 ``ocr_confidence.mean``；缺失/非数值 → None。"""

    confidence = metadata.get("ocr_confidence")
    if isinstance(confidence, dict):
        confidence = confidence.get("mean")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    return float(confidence)


def metadata_page_count(metadata: dict[str, Any]) -> int:
    for key in ("page_count", "pages_count"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    pages = metadata.get("pages")
    if isinstance(pages, bool):
        return 0
    if isinstance(pages, int):
        return max(pages, 0)
    if isinstance(pages, list):
        return len(pages)
    return 0


@dataclass
class ParseQualityCandidate:
    content_uid: str
    sha256: str
    parser_name: str
    parser_adapter_version: str
    result_status: str
    route_type: str | None
    run_uid: str
    result_uid: str
    parsed_dir: str | None
    manifest_path: str | None
    source_vault_path: str | None
    error_code: str | None


@dataclass
class ParseQualityIssue:
    issue_code: str
    severity: str
    content_uid: str | None
    sha256: str | None
    parser_name: str | None
    parser_adapter_version: str | None
    artifact_type: str | None
    path: str | None
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_code": self.issue_code,
            "severity": self.severity,
            "content_uid": self.content_uid,
            "sha256": self.sha256,
            "parser_name": self.parser_name,
            "parser_adapter_version": self.parser_adapter_version,
            "artifact_type": self.artifact_type,
            "path": self.path,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass
class ParseQualityReport:
    scope: dict[str, Any]
    summary: dict[str, int]
    issue_counts: dict[str, int]
    by_parser: dict[str, int]
    by_status: dict[str, int]
    by_route_type: dict[str, int]
    by_severity: dict[str, int]
    issues: list[ParseQualityIssue]
    recommendations: list[str]
    generated_at: str
    report_path: Path | None = None
    schema_version: str = SCHEMA_VERSION
    # None = 语义检查关闭 → 报告中不出现该键（逐字段复现 008）。
    semantic_checks_enabled: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "report_type": REPORT_TYPE,
            "schema_version": self.schema_version,
        }
        if self.semantic_checks_enabled is not None:
            payload["semantic_checks_enabled"] = self.semantic_checks_enabled
        payload.update(
            {
                "generated_at": self.generated_at,
                "mode": MODE_CHECK,
                "scope": self.scope,
                "summary": self.summary,
                "issue_counts": self.issue_counts,
                "by_parser": self.by_parser,
                "by_status": self.by_status,
                "by_route_type": self.by_route_type,
                "by_severity": self.by_severity,
                "issues": [issue.to_dict() for issue in self.issues],
                "recommendations": self.recommendations,
            }
        )
        return payload


def _utc_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _report_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _empty_issue_counts(*, include_semantic: bool = True) -> dict[str, int]:
    codes = ISSUE_CODES if include_semantic else LEGACY_ISSUE_CODES
    return {code: 0 for code in codes}


def _is_stale_path(path_value: str | None) -> bool:
    if not path_value:
        return False
    normalized = path_value.replace("\\", "/")
    return any(marker in normalized for marker in STALE_PATH_MARKERS)


def _increment(bucket: dict[str, int], key: str | None) -> None:
    label = key if key else "(null)"
    bucket[label] = bucket.get(label, 0) + 1


def _os_error_evidence(exc: OSError, path: Path) -> dict[str, Any]:
    return {
        "error": type(exc).__name__,
        "errno": exc.errno,
        "path": path.as_posix(),
    }


def _safe_is_dir(path: Path) -> tuple[bool, OSError | None]:
    try:
        return path.is_dir(), None
    except OSError as exc:
        return False, exc


def _safe_is_file(path: Path) -> tuple[bool, OSError | None]:
    try:
        return path.is_file(), None
    except OSError as exc:
        return False, exc


class ParseQualityCheckerService:
    def __init__(
        self,
        config: AppConfig,
        session_factory: sessionmaker[Session] | None = None,
        semantic_config: SemanticCheckConfig | None = None,
        semantic_config_path: Path | None = None,
    ) -> None:
        ensure_readonly(config)
        self.config = config
        if session_factory is None:
            engine = create_db_engine(config)
            self.session_factory = create_session_factory(engine)
        else:
            self.session_factory = session_factory
        # 016 第五类语义检查阈值；测试可注入内存配置（阈值变异复验）。
        self.semantic_config = (
            semantic_config
            if semantic_config is not None
            else load_semantic_check_config(semantic_config_path)
        )

    def check(
        self,
        *,
        sha256: str | None = None,
        content_uid: str | None = None,
        parser_name: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        output: Path | None = None,
        semantic_enabled: bool | None = None,
    ) -> ParseQualityReport:
        if parser_name is not None and parser_name not in ALLOWED_PARSER_NAMES:
            raise ValueError(
                f"parser_name must be one of {sorted(ALLOWED_PARSER_NAMES)}: {parser_name!r}"
            )
        if status is not None and status not in ALLOWED_RESULT_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(ALLOWED_RESULT_STATUSES)}: {status!r}"
            )
        if limit is not None and limit < 1:
            raise ValueError("--limit must be >= 1")

        semantic_config = self._effective_semantic_config(semantic_enabled)
        scope: dict[str, Any] = {
            "sha256": sha256,
            "content_uid": content_uid,
            "parser_name": parser_name,
            "status": status,
            "limit": limit,
        }
        if semantic_config.enabled:
            # TL 裁定 (c)：语义关闭时省略该键，使 --no-semantic 输出与 008 逐字段一致。
            scope["semantic"] = True
        generated_at = _utc_iso()

        with self.session_factory() as session:
            candidates = self._load_candidates(
                session=session,
                sha256=sha256,
                content_uid=content_uid,
                parser_name=parser_name,
                status=status,
                limit=limit,
            )
            issues: list[ParseQualityIssue] = []
            checked_raw_vault: set[str] = set()
            checked_artifact_ids: set[int] = set()
            checked_semantic_count = 0

            for candidate in candidates:
                vault_object = session.scalar(
                    select(KbRawVaultObject).where(KbRawVaultObject.sha256 == candidate.sha256)
                )
                if vault_object is not None:
                    checked_raw_vault.add(vault_object.sha256)
                    issues.extend(self._check_raw_vault(vault_object))

                issues.extend(self._check_stale_source_path(candidate))

                parsed_dir = self._resolve_parsed_dir(candidate)
                artifact_paths = build_parsed_artifact_paths(parsed_dir)
                issues.extend(self._check_parsed_files(candidate, parsed_dir, artifact_paths))

                manifest_path = self._resolve_manifest_path(candidate, artifact_paths)
                manifest, manifest_issues = self._load_and_validate_manifest(
                    session=session,
                    candidate=candidate,
                    manifest_path=manifest_path,
                )
                issues.extend(manifest_issues)

                if manifest is not None:
                    route_type = candidate.route_type or manifest.get("route_type")
                    if route_type is None and isinstance(manifest.get("route_type"), str):
                        route_type = manifest["route_type"]
                    candidate.route_type = str(route_type) if route_type else None
                    issues.extend(
                        self._check_stale_manifest_source_path(candidate, manifest)
                    )

                issues.extend(
                    self._check_registry_status_files(
                        candidate=candidate,
                        parsed_dir=parsed_dir,
                        artifact_paths=artifact_paths,
                    )
                )
                issues.extend(self._check_registry_status_aggregation(candidate))

                # --- 016 第五类：语义检查（仅 status=SUCCESS 且语义开关开启）---
                if semantic_config.enabled and candidate.result_status == "SUCCESS":
                    try:
                        semantic_issues, evaluated = self._check_semantic_quality(
                            candidate=candidate,
                            manifest=manifest,
                            artifact_paths=artifact_paths,
                            semantic_config=semantic_config,
                        )
                    except (
                        OSError,
                        ValueError,
                        KeyError,
                        TypeError,
                        UnicodeError,
                    ) as exc:  # 单候选容错，不中断批处理（plan §8）
                        logger.warning(
                            "Semantic check failed for %s: %s", candidate.content_uid, exc
                        )
                        semantic_issues, evaluated = [], False
                    if evaluated:
                        checked_semantic_count += 1
                    issues.extend(semantic_issues)

                registry_artifacts = session.scalars(
                    select(KbParsedArtifact).where(
                        KbParsedArtifact.run_uid == candidate.run_uid,
                        KbParsedArtifact.content_uid == candidate.content_uid,
                    )
                ).all()
                for registry_artifact in registry_artifacts:
                    checked_artifact_ids.add(registry_artifact.id)
                    issues.extend(self._check_registry_artifact(registry_artifact))

        issue_counts = _empty_issue_counts(include_semantic=semantic_config.enabled)
        by_parser: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_route_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        severity_summary = {
            SEVERITY_CRITICAL: 0,
            SEVERITY_ERROR: 0,
            SEVERITY_WARNING: 0,
            SEVERITY_INFO: 0,
        }

        candidate_by_uid = {candidate.content_uid: candidate for candidate in candidates}

        for issue in issues:
            issue_counts[issue.issue_code] = issue_counts.get(issue.issue_code, 0) + 1
            _increment(by_parser, issue.parser_name)
            candidate = (
                candidate_by_uid.get(issue.content_uid)
                if issue.content_uid is not None
                else None
            )
            if candidate is not None:
                _increment(by_status, candidate.result_status)
                _increment(by_route_type, candidate.route_type)
            _increment(by_severity, issue.severity)
            if issue.severity in severity_summary:
                severity_summary[issue.severity] += 1

        unique_content_uids = {candidate.content_uid for candidate in candidates}
        summary: dict[str, int] = {
            "checked_content_count": len(unique_content_uids),
            "checked_raw_vault_count": len(checked_raw_vault),
            "checked_parse_result_count": len(candidates),
            "checked_artifact_count": len(checked_artifact_ids),
            "issue_count": len(issues),
            "critical_count": severity_summary[SEVERITY_CRITICAL],
            "error_count": severity_summary[SEVERITY_ERROR],
            "warning_count": severity_summary[SEVERITY_WARNING],
            "info_count": severity_summary[SEVERITY_INFO],
        }
        if semantic_config.enabled:
            summary["checked_semantic_count"] = checked_semantic_count

        recommendations = self._build_recommendations(issues)
        report = ParseQualityReport(
            scope=scope,
            summary=summary,
            issue_counts=issue_counts,
            by_parser=by_parser,
            by_status=by_status,
            by_route_type=by_route_type,
            by_severity=by_severity,
            issues=issues,
            recommendations=recommendations,
            generated_at=generated_at,
            schema_version=(
                SCHEMA_VERSION
                if semantic_config.enabled
                else SCHEMA_VERSION_SEMANTIC_DISABLED
            ),
            semantic_checks_enabled=True if semantic_config.enabled else None,
        )
        report.report_path = self._write_report(report, output)
        return report

    def _load_candidates(
        self,
        *,
        session: Session,
        sha256: str | None,
        content_uid: str | None,
        parser_name: str | None,
        status: str | None,
        limit: int | None,
    ) -> list[ParseQualityCandidate]:
        query = select(KbParseResult).order_by(KbParseResult.id.desc())
        if sha256:
            query = query.where(KbParseResult.sha256 == sha256)
        if content_uid:
            query = query.where(KbParseResult.content_uid == content_uid)
        if parser_name:
            query = query.where(KbParseResult.parser_name == parser_name)
        if status:
            query = query.where(KbParseResult.status == status)
        if limit is not None:
            query = query.limit(limit)
        results = session.scalars(query).all()
        return [
            ParseQualityCandidate(
                content_uid=result.content_uid,
                sha256=result.sha256,
                parser_name=result.parser_name,
                parser_adapter_version=result.parser_adapter_version,
                result_status=result.status,
                route_type=result.route_type,
                run_uid=result.run_uid,
                result_uid=result.result_uid,
                parsed_dir=result.parsed_dir,
                manifest_path=result.manifest_path,
                source_vault_path=result.source_vault_path,
                error_code=result.error_code,
            )
            for result in results
        ]

    def _resolve_parsed_dir(self, candidate: ParseQualityCandidate) -> Path:
        if candidate.parsed_dir:
            return Path(candidate.parsed_dir)
        return build_parsed_content_dir(self.config.storage.parsed_root, candidate.sha256)

    def _resolve_manifest_path(
        self,
        candidate: ParseQualityCandidate,
        artifact_paths: dict[str, Path],
    ) -> Path:
        if candidate.manifest_path:
            return Path(candidate.manifest_path)
        return artifact_paths["parse_manifest"]

    # --- 016 第五类语义检查 -----------------------------------------------------------

    def _effective_semantic_config(
        self, semantic_enabled: bool | None
    ) -> SemanticCheckConfig:
        if semantic_enabled is None or semantic_enabled == self.semantic_config.enabled:
            return self.semantic_config
        return replace(self.semantic_config, enabled=semantic_enabled)

    @staticmethod
    def _semantic_issue(
        *,
        code: str,
        severity: str,
        base_kwargs: dict[str, Any],
        artifact_type: str | None,
        path: str | None,
        message: str,
        evidence: dict[str, Any],
    ) -> ParseQualityIssue:
        return ParseQualityIssue(
            issue_code=code,
            severity=severity,
            artifact_type=artifact_type,
            path=path,
            message=message,
            evidence=evidence,
            **base_kwargs,
        )

    @staticmethod
    def _read_parsed_metadata(metadata_path: Path) -> dict[str, Any] | None:
        """只读解析 ``parsed_metadata.json``；读取失败 / JSON 非法 / 非对象 → None（跳过）。"""

        try:
            raw = metadata_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _check_semantic_quality(
        self,
        *,
        candidate: ParseQualityCandidate,
        manifest: dict[str, Any] | None,
        artifact_paths: dict[str, Path],
        semantic_config: SemanticCheckConfig,
    ) -> tuple[list[ParseQualityIssue], bool]:
        """返回 ``(issues, evaluated)``；``evaluated=False`` 表示该候选未能完成语义评估。"""

        text_path = artifact_paths["parsed_text"]
        try:
            text = text_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError) as exc:
            # 读失败复用既有 MISSING_PARSED_TEXT / 权限码体系，不新增错误类 code。
            logger.warning(
                "Semantic check skipped for %s: cannot read %s (%s)",
                candidate.content_uid,
                text_path,
                exc,
            )
            return [], False

        metadata = self._read_parsed_metadata(artifact_paths["parsed_metadata"])
        text_path_value = text_path.as_posix()
        base_kwargs: dict[str, Any] = {
            "content_uid": candidate.content_uid,
            "sha256": candidate.sha256,
            "parser_name": candidate.parser_name,
            "parser_adapter_version": candidate.parser_adapter_version,
        }

        issues: list[ParseQualityIssue] = []
        empty_issue = self._detect_empty_but_nonzero_pages(
            candidate=candidate,
            manifest=manifest,
            metadata=metadata,
            text=text,
            text_path=text_path_value,
            semantic_config=semantic_config,
            base_kwargs=base_kwargs,
        )
        if empty_issue is not None:
            issues.append(empty_issue)

        garbled_issue = self._detect_garbled_ratio(
            text=text,
            text_path=text_path_value,
            semantic_config=semantic_config,
            base_kwargs=base_kwargs,
        )
        if garbled_issue is not None:
            issues.append(garbled_issue)

        table_issue = self._detect_incomplete_tables(
            text=text,
            text_path=text_path_value,
            semantic_config=semantic_config,
            base_kwargs=base_kwargs,
        )
        if table_issue is not None:
            issues.append(table_issue)

        issues.extend(
            self._detect_ocr_confidence(
                candidate=candidate,
                metadata=metadata,
                metadata_path=artifact_paths["parsed_metadata"].as_posix(),
                semantic_config=semantic_config,
                base_kwargs=base_kwargs,
            )
        )
        return issues, True

    def _detect_empty_but_nonzero_pages(
        self,
        *,
        candidate: ParseQualityCandidate,
        manifest: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
        text: str,
        text_path: str,
        semantic_config: SemanticCheckConfig,
        base_kwargs: dict[str, Any],
    ) -> ParseQualityIssue | None:
        if not semantic_config.empty_text_enabled:
            return None
        if text.strip() != "":
            return None

        raw_size = manifest.get("content_size_bytes") if manifest else None
        content_size_bytes = (
            raw_size
            if isinstance(raw_size, int) and not isinstance(raw_size, bool)
            else 0
        )
        page_count = metadata_page_count(metadata or {})
        if content_size_bytes <= 0 and page_count <= 0:
            # 源本身为空 → 不评估（008 EMPTY 语义已由 REGISTRY_EMPTY_RESULT 覆盖）。
            return None

        return self._semantic_issue(
            code="EMPTY_BUT_NONZERO_PAGES",
            severity=SEVERITY_ERROR,
            base_kwargs=base_kwargs,
            artifact_type="PARSED_TEXT",
            path=text_path,
            message=(
                "Registry status is SUCCESS but parsed_text.md is empty while the "
                "source content is non-empty"
            ),
            evidence={
                "content_size_bytes": content_size_bytes,
                "page_count": page_count,
                "text_length": len(text),
                "result_status": candidate.result_status,
            },
        )

    def _detect_garbled_ratio(
        self,
        *,
        text: str,
        text_path: str,
        semantic_config: SemanticCheckConfig,
        base_kwargs: dict[str, Any],
    ) -> ParseQualityIssue | None:
        total_chars = len(text)
        if total_chars < semantic_config.min_sample_chars:
            # 样本不足 → 整体跳过（不是「ratio=0 通过」）。
            return None

        garbled_chars = count_garbled_chars(text)
        garbled_ratio = garbled_chars / total_chars
        if garbled_ratio >= semantic_config.garbled_error_ratio:
            severity = SEVERITY_ERROR
        elif garbled_ratio >= semantic_config.garbled_warn_ratio:
            severity = SEVERITY_WARNING
        else:
            return None

        return self._semantic_issue(
            code="OCR_GARBLED_RATIO",
            severity=severity,
            base_kwargs=base_kwargs,
            artifact_type="PARSED_TEXT",
            path=text_path,
            message=(
                f"parsed_text.md garbled ratio {garbled_ratio:.4f} exceeds threshold "
                f"({semantic_config.garbled_warn_ratio} warn / "
                f"{semantic_config.garbled_error_ratio} error)"
            ),
            evidence={
                "garbled_ratio": round(garbled_ratio, 4),
                "garbled_chars": garbled_chars,
                "total_chars": total_chars,
                "threshold_warn": semantic_config.garbled_warn_ratio,
                "threshold_error": semantic_config.garbled_error_ratio,
                "min_sample_chars": semantic_config.min_sample_chars,
                "sampled": True,
            },
        )

    def _detect_incomplete_tables(
        self,
        *,
        text: str,
        text_path: str,
        semantic_config: SemanticCheckConfig,
        base_kwargs: dict[str, Any],
    ) -> ParseQualityIssue | None:
        if not semantic_config.tables_enabled:
            return None

        total_tables, incomplete_lines = analyze_markdown_tables(text)
        if total_tables <= 0 or not incomplete_lines:
            return None

        incomplete_tables = len(incomplete_lines)
        return self._semantic_issue(
            code="TABLE_STRUCTURE_INCOMPLETE",
            severity=SEVERITY_WARNING,
            base_kwargs=base_kwargs,
            artifact_type="PARSED_TEXT",
            path=text_path,
            message=(
                f"{incomplete_tables} of {total_tables} Markdown table(s) have "
                "inconsistent column counts or a missing separator row"
            ),
            evidence={
                "total_tables": total_tables,
                "incomplete_tables": incomplete_tables,
                "incomplete_ratio": round(incomplete_tables / total_tables, 4),
                "incomplete_table_lines": list(
                    incomplete_lines[:TABLE_LINE_NUMBERS_LIMIT]
                ),
            },
        )

    def _detect_ocr_confidence(
        self,
        *,
        candidate: ParseQualityCandidate,
        metadata: dict[str, Any] | None,
        metadata_path: str,
        semantic_config: SemanticCheckConfig,
        base_kwargs: dict[str, Any],
    ) -> list[ParseQualityIssue]:
        if candidate.parser_name not in semantic_config.ocr_parser_names:
            # 非 OCR 族（如 markitdown）不评估，两个 code 都不产。
            return []
        if metadata is None:
            # metadata 缺失/非法：既有 MISSING_PARSED_METADATA 已覆盖，不新增 code。
            return []

        confidence_mean = extract_confidence_mean(metadata)
        if confidence_mean is None:
            # 反编造红线：无置信度只产 INFO，绝不产 OCR_LOW_CONFIDENCE。
            return [
                self._semantic_issue(
                    code="OCR_CONFIDENCE_UNAVAILABLE",
                    severity=SEVERITY_INFO,
                    base_kwargs=base_kwargs,
                    artifact_type="PARSED_METADATA",
                    path=metadata_path,
                    message=(
                        "OCR parser did not report ocr_confidence.mean; text quality "
                        "cannot be verified from confidence data"
                    ),
                    evidence={
                        "parser_name": candidate.parser_name,
                        "metadata_path": metadata_path,
                        "ocr_confidence_present": False,
                        "ocr_parser_names": list(semantic_config.ocr_parser_names),
                    },
                )
            ]

        if confidence_mean >= semantic_config.confidence_min_mean:
            return []
        return [
            self._semantic_issue(
                code="OCR_LOW_CONFIDENCE",
                severity=SEVERITY_WARNING,
                base_kwargs=base_kwargs,
                artifact_type="PARSED_METADATA",
                path=metadata_path,
                message=(
                    f"ocr_confidence.mean {confidence_mean:.4f} is below threshold "
                    f"{semantic_config.confidence_min_mean}"
                ),
                evidence={
                    "confidence_mean": round(confidence_mean, 4),
                    "threshold_min_mean": semantic_config.confidence_min_mean,
                    "metadata_path": metadata_path,
                },
            )
        ]

    def _check_raw_vault(self, vault_object: KbRawVaultObject) -> list[ParseQualityIssue]:
        issues: list[ParseQualityIssue] = []
        vault_dir = Path(vault_object.vault_path)
        original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]

        if _is_stale_path(vault_object.vault_path):
            issues.append(
                ParseQualityIssue(
                    issue_code="STALE_RAW_VAULT_PATH",
                    severity=SEVERITY_WARNING,
                    content_uid=vault_object.content_uid,
                    sha256=vault_object.sha256,
                    parser_name=None,
                    parser_adapter_version=None,
                    artifact_type="RAW_VAULT",
                    path=vault_object.vault_path,
                    message="Raw vault path points to a suspicious temporary location",
                    evidence={"vault_path": vault_object.vault_path},
                )
            )

        is_file, file_err = _safe_is_file(original_bin)
        if file_err is not None:
            issues.append(
                ParseQualityIssue(
                    issue_code="MISSING_RAW_VAULT_OBJECT",
                    severity=SEVERITY_ERROR,
                    content_uid=vault_object.content_uid,
                    sha256=vault_object.sha256,
                    parser_name=None,
                    parser_adapter_version=None,
                    artifact_type="RAW_VAULT",
                    path=original_bin.as_posix(),
                    message=f"Cannot access raw_vault original.bin: {file_err}",
                    evidence={
                        "vault_path": vault_object.vault_path,
                        "original_bin": original_bin.as_posix(),
                        **_os_error_evidence(file_err, original_bin),
                    },
                )
            )
        elif not is_file:
            issues.append(
                ParseQualityIssue(
                    issue_code="MISSING_RAW_VAULT_OBJECT",
                    severity=SEVERITY_ERROR,
                    content_uid=vault_object.content_uid,
                    sha256=vault_object.sha256,
                    parser_name=None,
                    parser_adapter_version=None,
                    artifact_type="RAW_VAULT",
                    path=original_bin.as_posix(),
                    message="raw_vault original.bin is missing",
                    evidence={
                        "vault_path": vault_object.vault_path,
                        "original_bin": original_bin.as_posix(),
                    },
                )
            )
        return issues

    def _check_stale_source_path(
        self,
        candidate: ParseQualityCandidate,
    ) -> list[ParseQualityIssue]:
        if not _is_stale_path(candidate.source_vault_path):
            return []
        return [
            ParseQualityIssue(
                issue_code="STALE_RAW_VAULT_PATH",
                severity=SEVERITY_WARNING,
                content_uid=candidate.content_uid,
                sha256=candidate.sha256,
                parser_name=candidate.parser_name,
                parser_adapter_version=candidate.parser_adapter_version,
                artifact_type="RAW_VAULT",
                path=candidate.source_vault_path,
                message="Registry source_vault_path points to a suspicious temporary location",
                evidence={"source_vault_path": candidate.source_vault_path},
            )
        ]

    def _check_stale_manifest_source_path(
        self,
        candidate: ParseQualityCandidate,
        manifest: dict[str, Any],
    ) -> list[ParseQualityIssue]:
        source_vault_path = manifest.get("source_vault_path")
        if not isinstance(source_vault_path, str) or not _is_stale_path(source_vault_path):
            return []
        if candidate.source_vault_path == source_vault_path:
            return []
        return [
            ParseQualityIssue(
                issue_code="STALE_RAW_VAULT_PATH",
                severity=SEVERITY_WARNING,
                content_uid=candidate.content_uid,
                sha256=candidate.sha256,
                parser_name=candidate.parser_name,
                parser_adapter_version=candidate.parser_adapter_version,
                artifact_type="RAW_VAULT",
                path=source_vault_path,
                message="Manifest source_vault_path points to a suspicious temporary location",
                evidence={"source_vault_path": source_vault_path},
            )
        ]

    def _check_parsed_files(
        self,
        candidate: ParseQualityCandidate,
        parsed_dir: Path,
        artifact_paths: dict[str, Path],
    ) -> list[ParseQualityIssue]:
        issues: list[ParseQualityIssue] = []
        base_kwargs = {
            "content_uid": candidate.content_uid,
            "sha256": candidate.sha256,
            "parser_name": candidate.parser_name,
            "parser_adapter_version": candidate.parser_adapter_version,
        }

        is_dir, dir_err = _safe_is_dir(parsed_dir)
        if dir_err is not None:
            issues.append(
                ParseQualityIssue(
                    issue_code="MISSING_PARSED_DIR",
                    severity=SEVERITY_ERROR,
                    artifact_type="PARSED_DIR",
                    path=parsed_dir.as_posix(),
                    message=f"Cannot access parsed directory: {dir_err}",
                    evidence={
                        "parsed_dir": parsed_dir.as_posix(),
                        **_os_error_evidence(dir_err, parsed_dir),
                    },
                    **base_kwargs,
                )
            )
            return issues

        if not is_dir:
            issues.append(
                ParseQualityIssue(
                    issue_code="MISSING_PARSED_DIR",
                    severity=SEVERITY_ERROR,
                    artifact_type="PARSED_DIR",
                    path=parsed_dir.as_posix(),
                    message="Expected parsed directory is missing",
                    evidence={"parsed_dir": parsed_dir.as_posix()},
                    **base_kwargs,
                )
            )
            return issues

        for code, key, artifact_type in (
            ("MISSING_PARSED_TEXT", "parsed_text", "PARSED_TEXT"),
            ("MISSING_PARSED_METADATA", "parsed_metadata", "PARSED_METADATA"),
            ("MISSING_PARSE_MANIFEST", "parse_manifest", "PARSE_MANIFEST"),
        ):
            file_path = artifact_paths[key]
            is_file, file_err = _safe_is_file(file_path)
            if file_err is not None:
                issues.append(
                    ParseQualityIssue(
                        issue_code=code,
                        severity=SEVERITY_ERROR,
                        artifact_type=artifact_type,
                        path=file_path.as_posix(),
                        message=f"Cannot access parsed artifact: {file_err}",
                        evidence={
                            "parsed_dir": parsed_dir.as_posix(),
                            **_os_error_evidence(file_err, file_path),
                        },
                        **base_kwargs,
                    )
                )
                continue
            if not is_file:
                issues.append(
                    ParseQualityIssue(
                        issue_code=code,
                        severity=SEVERITY_ERROR,
                        artifact_type=artifact_type,
                        path=file_path.as_posix(),
                        message=f"Required parsed artifact is missing: {file_path.name}",
                        evidence={"parsed_dir": parsed_dir.as_posix()},
                        **base_kwargs,
                    )
                )
        return issues

    def _load_and_validate_manifest(
        self,
        *,
        session: Session,
        candidate: ParseQualityCandidate,
        manifest_path: Path,
    ) -> tuple[dict[str, Any] | None, list[ParseQualityIssue]]:
        issues: list[ParseQualityIssue] = []
        base_kwargs = {
            "content_uid": candidate.content_uid,
            "sha256": candidate.sha256,
            "parser_name": candidate.parser_name,
            "parser_adapter_version": candidate.parser_adapter_version,
            "artifact_type": "PARSE_MANIFEST",
            "path": manifest_path.as_posix(),
        }

        is_file, file_err = _safe_is_file(manifest_path)
        if file_err is not None:
            issues.append(
                ParseQualityIssue(
                    issue_code="MISSING_PARSE_MANIFEST",
                    severity=SEVERITY_ERROR,
                    message=f"Cannot access parse_manifest.json: {file_err}",
                    evidence={
                        "manifest_path": manifest_path.as_posix(),
                        **_os_error_evidence(file_err, manifest_path),
                    },
                    **base_kwargs,
                )
            )
            return None, issues

        if not is_file:
            return None, issues

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except OSError as exc:
            issues.append(
                ParseQualityIssue(
                    issue_code="INVALID_PARSE_MANIFEST_JSON",
                    severity=SEVERITY_ERROR,
                    message=f"Cannot read parse_manifest.json: {exc}",
                    evidence={
                        "manifest_path": manifest_path.as_posix(),
                        **_os_error_evidence(exc, manifest_path),
                    },
                    **base_kwargs,
                )
            )
            return None, issues
        except json.JSONDecodeError as exc:
            issues.append(
                ParseQualityIssue(
                    issue_code="INVALID_PARSE_MANIFEST_JSON",
                    severity=SEVERITY_ERROR,
                    message=f"parse_manifest.json is not valid JSON: {exc}",
                    evidence={"manifest_path": manifest_path.as_posix()},
                    **base_kwargs,
                )
            )
            return None, issues

        if not isinstance(manifest, dict):
            issues.append(
                ParseQualityIssue(
                    issue_code="INVALID_PARSE_MANIFEST_JSON",
                    severity=SEVERITY_ERROR,
                    message="parse_manifest.json root must be a JSON object",
                    evidence={"manifest_path": manifest_path.as_posix()},
                    **base_kwargs,
                )
            )
            return None, issues

        required_fields = list(COMMON_MANIFEST_REQUIRED_FIELDS)
        if manifest.get("parser_name") == "mineru":
            required_fields.extend(MINERU_MANIFEST_REQUIRED_FIELDS)
        if manifest.get("status") == "SUCCESS":
            required_fields.extend(SUCCESS_MANIFEST_ARTIFACT_FIELDS)

        missing_fields = [
            field_name
            for field_name in required_fields
            if field_name not in manifest or manifest.get(field_name) in (None, "")
        ]
        if missing_fields:
            issues.append(
                ParseQualityIssue(
                    issue_code="MANIFEST_REQUIRED_FIELD_MISSING",
                    severity=SEVERITY_ERROR,
                    message=f"Manifest missing required fields: {', '.join(missing_fields)}",
                    evidence={"missing_fields": missing_fields},
                    **base_kwargs,
                )
            )

        manifest_sha256 = manifest.get("sha256")
        if isinstance(manifest_sha256, str) and manifest_sha256 != candidate.sha256:
            issues.append(
                ParseQualityIssue(
                    issue_code="MANIFEST_SHA256_MISMATCH",
                    severity=SEVERITY_CRITICAL,
                    message="Manifest sha256 does not match registry/content sha256",
                    evidence={
                        "manifest_sha256": manifest_sha256,
                        "expected_sha256": candidate.sha256,
                    },
                    **base_kwargs,
                )
            )

        manifest_content_uid = manifest.get("content_uid")
        if (
            isinstance(manifest_content_uid, str)
            and manifest_content_uid != candidate.content_uid
        ):
            issues.append(
                ParseQualityIssue(
                    issue_code="MANIFEST_CONTENT_UID_MISMATCH",
                    severity=SEVERITY_CRITICAL,
                    message="Manifest content_uid does not match registry content_uid",
                    evidence={
                        "manifest_content_uid": manifest_content_uid,
                        "expected_content_uid": candidate.content_uid,
                    },
                    **base_kwargs,
                )
            )

        manifest_parser_name = manifest.get("parser_name")
        if manifest_parser_name is not None and manifest_parser_name not in ALLOWED_PARSER_NAMES:
            issues.append(
                ParseQualityIssue(
                    issue_code="MANIFEST_PARSER_NAME_INVALID",
                    severity=SEVERITY_ERROR,
                    message=f"Manifest parser_name is not allowed: {manifest_parser_name!r}",
                    evidence={"parser_name": manifest_parser_name},
                    **base_kwargs,
                )
            )
        elif (
            isinstance(manifest_parser_name, str)
            and manifest_parser_name != candidate.parser_name
        ):
            issues.append(
                ParseQualityIssue(
                    issue_code="MANIFEST_PARSER_NAME_INVALID",
                    severity=SEVERITY_ERROR,
                    message="Manifest parser_name does not match registry parser_name",
                    evidence={
                        "manifest_parser_name": manifest_parser_name,
                        "registry_parser_name": candidate.parser_name,
                    },
                    **base_kwargs,
                )
            )

        if not manifest.get("parser_adapter_version"):
            issues.append(
                ParseQualityIssue(
                    issue_code="MANIFEST_ADAPTER_VERSION_MISSING",
                    severity=SEVERITY_ERROR,
                    message="Manifest parser_adapter_version is missing",
                    evidence={},
                    **base_kwargs,
                )
            )

        db_content = session.scalar(
            select(KbFileContent).where(KbFileContent.sha256 == candidate.sha256)
        )
        if db_content is not None and isinstance(manifest_sha256, str):
            if manifest_sha256 != db_content.sha256:
                if not any(
                    issue.issue_code == "MANIFEST_SHA256_MISMATCH" for issue in issues
                ):
                    issues.append(
                        ParseQualityIssue(
                            issue_code="MANIFEST_SHA256_MISMATCH",
                            severity=SEVERITY_CRITICAL,
                            message="Manifest sha256 does not match kb_file_content.sha256",
                            evidence={
                                "manifest_sha256": manifest_sha256,
                                "db_sha256": db_content.sha256,
                            },
                            **base_kwargs,
                        )
                    )

        if candidate.route_type is None:
            route_type = manifest.get("route_type")
            if isinstance(route_type, str):
                candidate.route_type = route_type

        return manifest, issues

    def _check_registry_status_files(
        self,
        *,
        candidate: ParseQualityCandidate,
        parsed_dir: Path,
        artifact_paths: dict[str, Path],
    ) -> list[ParseQualityIssue]:
        if candidate.result_status != "SUCCESS":
            return []
        missing_paths: list[str] = []
        for key in ("parsed_text", "parsed_metadata", "parse_manifest"):
            file_path = artifact_paths[key]
            is_file, _file_err = _safe_is_file(file_path)
            if _file_err is not None or not is_file:
                missing_paths.append(file_path.as_posix())
        is_dir, _dir_err = _safe_is_dir(parsed_dir)
        if not missing_paths and is_dir and _dir_err is None:
            return []
        return [
            ParseQualityIssue(
                issue_code="REGISTRY_STATUS_FILE_MISMATCH",
                severity=SEVERITY_CRITICAL,
                content_uid=candidate.content_uid,
                sha256=candidate.sha256,
                parser_name=candidate.parser_name,
                parser_adapter_version=candidate.parser_adapter_version,
                artifact_type=None,
                path=parsed_dir.as_posix(),
                message="Registry status is SUCCESS but required parsed artifacts are missing",
                evidence={
                    "result_status": candidate.result_status,
                    "missing_paths": missing_paths,
                },
            )
        ]

    def _check_registry_status_aggregation(
        self,
        candidate: ParseQualityCandidate,
    ) -> list[ParseQualityIssue]:
        if candidate.error_code == ERROR_MISSING_MANIFEST:
            return [
                ParseQualityIssue(
                    issue_code="REGISTRY_MISSING_MANIFEST_RESULT",
                    severity=SEVERITY_WARNING,
                    content_uid=candidate.content_uid,
                    sha256=candidate.sha256,
                    parser_name=candidate.parser_name,
                    parser_adapter_version=candidate.parser_adapter_version,
                    artifact_type="PARSE_MANIFEST",
                    path=candidate.manifest_path,
                    message="Registry result indicates missing manifest",
                    evidence={"error_code": candidate.error_code},
                )
            ]
        if candidate.result_status == "FAILED":
            return [
                ParseQualityIssue(
                    issue_code="REGISTRY_FAILED_RESULT",
                    severity=SEVERITY_WARNING,
                    content_uid=candidate.content_uid,
                    sha256=candidate.sha256,
                    parser_name=candidate.parser_name,
                    parser_adapter_version=candidate.parser_adapter_version,
                    artifact_type=None,
                    path=None,
                    message="Registry parse result status is FAILED",
                    evidence={
                        "result_status": candidate.result_status,
                        "error_code": candidate.error_code,
                    },
                )
            ]
        if candidate.result_status == "EMPTY":
            return [
                ParseQualityIssue(
                    issue_code="REGISTRY_EMPTY_RESULT",
                    severity=SEVERITY_WARNING,
                    content_uid=candidate.content_uid,
                    sha256=candidate.sha256,
                    parser_name=candidate.parser_name,
                    parser_adapter_version=candidate.parser_adapter_version,
                    artifact_type=None,
                    path=None,
                    message="Registry parse result status is EMPTY",
                    evidence={"result_status": candidate.result_status},
                )
            ]
        if candidate.result_status == "SKIPPED":
            return [
                ParseQualityIssue(
                    issue_code="REGISTRY_SKIPPED_RESULT",
                    severity=SEVERITY_INFO,
                    content_uid=candidate.content_uid,
                    sha256=candidate.sha256,
                    parser_name=candidate.parser_name,
                    parser_adapter_version=candidate.parser_adapter_version,
                    artifact_type=None,
                    path=None,
                    message="Registry parse result status is SKIPPED",
                    evidence={"result_status": candidate.result_status},
                )
            ]
        return []

    def _check_registry_artifact(
        self,
        artifact: KbParsedArtifact,
    ) -> list[ParseQualityIssue]:
        artifact_path = Path(artifact.artifact_path)
        is_file, file_err = _safe_is_file(artifact_path)
        if file_err is not None:
            return [
                ParseQualityIssue(
                    issue_code="REGISTRY_ARTIFACT_PATH_MISSING",
                    severity=SEVERITY_ERROR,
                    content_uid=artifact.content_uid or None,
                    sha256=artifact.sha256,
                    parser_name=artifact.parser_name,
                    parser_adapter_version=artifact.parser_adapter_version,
                    artifact_type=artifact.artifact_type,
                    path=artifact.artifact_path,
                    message=f"Cannot access registry artifact path: {file_err}",
                    evidence={
                        "artifact_uid": artifact.artifact_uid,
                        "artifact_type": artifact.artifact_type,
                        **_os_error_evidence(file_err, artifact_path),
                    },
                )
            ]
        if is_file:
            return []
        return [
            ParseQualityIssue(
                issue_code="REGISTRY_ARTIFACT_PATH_MISSING",
                severity=SEVERITY_ERROR,
                content_uid=artifact.content_uid or None,
                sha256=artifact.sha256,
                parser_name=artifact.parser_name,
                parser_adapter_version=artifact.parser_adapter_version,
                artifact_type=artifact.artifact_type,
                path=artifact.artifact_path,
                message="Registry artifact path points to a missing file",
                evidence={
                    "artifact_uid": artifact.artifact_uid,
                    "artifact_type": artifact.artifact_type,
                },
            )
        ]

    def _build_recommendations(self, issues: list[ParseQualityIssue]) -> list[str]:
        seen: set[str] = set()
        recommendations: list[str] = []
        for issue in issues:
            recommendation = RECOMMENDATION_BY_CODE.get(issue.issue_code)
            if recommendation and recommendation not in seen:
                seen.add(recommendation)
                recommendations.append(recommendation)
        return recommendations

    def _write_report(
        self,
        report: ParseQualityReport,
        output: Path | None,
    ) -> Path:
        reports_root = self.config.storage.reports_root
        reports_root.mkdir(parents=True, exist_ok=True)
        report_path = output or (
            reports_root / f"parse_quality_report_{_report_timestamp()}.json"
        )
        try:
            report_path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(f"Report directory not writable: {exc}") from exc
        logger.info("Wrote parse quality report: %s", report_path)
        return report_path
