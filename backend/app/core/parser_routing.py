from __future__ import annotations

from enum import Enum
from pathlib import Path

from app.core.file_types import DOCUMENT_EXTENSIONS
from app.core.pdf_sniffing import PdfSniffResult, SniffStatus

# 默认（当前生效）规则版本。014 起默认 v2：PDF 分支叠加内容嗅探。
ROUTING_RULES_VERSION = "004_mvp_v2"
# v1 冻结路径：仅扩展名规则，不做任何嗅探，逐字节复现 004 行为（A014-08）。
ROUTING_RULES_VERSION_V1 = "004_mvp_v1"
ROUTING_RULES_VERSIONS = frozenset({ROUTING_RULES_VERSION_V1, ROUTING_RULES_VERSION})

# 014 新增的 PDF 嗅探 rule_name 闭集（plan §6）。
PDF_SNIFF_RULE_NAMES = frozenset(
    {
        "pdf_sniff_digital",
        "pdf_sniff_scanned",
        "pdf_sniff_encrypted",
        "pdf_sniff_empty",
        "pdf_sniff_error",
        "pdf_sniff_unavailable",
    }
)

DECISION_ROUTE = "ROUTE"
DECISION_UNKNOWN = "UNKNOWN"
DECISION_UNSUPPORTED = "UNSUPPORTED"

IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
)
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".csv"})
MARKUP_EXTENSIONS = frozenset({".html", ".htm", ".xml", ".json"})
LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".ppt", ".xls"})
OTHER_OFFICE_EXTENSIONS = frozenset({".rtf", ".odt", ".ods", ".odp"})


class RouteType(str, Enum):
    DOCX = "DOCX"
    PPTX = "PPTX"
    XLSX = "XLSX"
    PDF_DIGITAL = "PDF_DIGITAL"
    PDF_SCANNED_OR_IMAGE = "PDF_SCANNED_OR_IMAGE"
    IMAGE = "IMAGE"
    TEXT_OR_MARKDOWN = "TEXT_OR_MARKDOWN"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class FutureParserHint(str, Enum):
    MARKITDOWN_FAMILY = "MARKITDOWN_FAMILY"
    MINERU_FAMILY = "MINERU_FAMILY"
    DIRECT_TEXT = "DIRECT_TEXT"
    NONE = "NONE"


def normalize_file_ext(ext: str | None) -> str | None:
    if ext is None:
        return None
    normalized = ext.strip().lower()
    if not normalized:
        return None
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    return normalized


def ext_from_path(path: str) -> str | None:
    suffix = Path(path).suffix
    if not suffix:
        return None
    return normalize_file_ext(suffix)


def match_route_type(
    *,
    file_ext: str | None,
    mime_type: str | None,
    fallback_ext: str | None = None,
) -> tuple[RouteType, str, str, str, str]:
    normalized_ext = normalize_file_ext(file_ext)
    normalized_fallback = normalize_file_ext(fallback_ext)
    effective_ext = normalized_ext or normalized_fallback

    if effective_ext == ".pdf" and mime_type and mime_type.lower().startswith("image/"):
        reason = (
            f"file_ext .pdf conflicts with mime_type {mime_type}; "
            "cannot classify without reading file content"
        )
        return (
            RouteType.UNKNOWN,
            DECISION_UNKNOWN,
            "ext_mime_conflict",
            FutureParserHint.NONE.value,
            reason,
        )

    if effective_ext == ".docx":
        return _route(
            RouteType.DOCX,
            "ext_docx",
            FutureParserHint.MARKITDOWN_FAMILY,
            "file_ext .docx maps to DOCX",
        )
    if effective_ext == ".pptx":
        return _route(
            RouteType.PPTX,
            "ext_pptx",
            FutureParserHint.MARKITDOWN_FAMILY,
            "file_ext .pptx maps to PPTX",
        )
    if effective_ext == ".xlsx":
        return _route(
            RouteType.XLSX,
            "ext_xlsx",
            FutureParserHint.MARKITDOWN_FAMILY,
            "file_ext .xlsx maps to XLSX",
        )
    if effective_ext == ".pdf":
        return _route(
            RouteType.PDF_DIGITAL,
            "ext_pdf_digital",
            FutureParserHint.MINERU_FAMILY,
            "file_ext .pdf maps to PDF_DIGITAL",
        )
    if effective_ext in IMAGE_EXTENSIONS:
        return _route(
            RouteType.IMAGE,
            "ext_image",
            FutureParserHint.MINERU_FAMILY,
            f"file_ext {effective_ext} maps to IMAGE",
        )
    if effective_ext in TEXT_EXTENSIONS:
        return _route(
            RouteType.TEXT_OR_MARKDOWN,
            "ext_text",
            FutureParserHint.DIRECT_TEXT,
            f"file_ext {effective_ext} maps to TEXT_OR_MARKDOWN",
        )
    if effective_ext in MARKUP_EXTENSIONS:
        return _route(
            RouteType.TEXT_OR_MARKDOWN,
            "ext_markup_json",
            FutureParserHint.DIRECT_TEXT,
            f"file_ext {effective_ext} maps to TEXT_OR_MARKDOWN",
        )
    if effective_ext in LEGACY_OFFICE_EXTENSIONS:
        return _unsupported(
            "ext_legacy_office",
            f"file_ext {effective_ext} is legacy Office; not parsed in Phase 1",
        )
    if effective_ext in OTHER_OFFICE_EXTENSIONS:
        return _unsupported(
            "ext_other_office",
            f"file_ext {effective_ext} is not supported in Phase 1",
        )

    if effective_ext is None:
        source = "file_ext and fallback_ext both missing"
        return _unknown("ext_missing", f"{source}; cannot determine route_type")

    if effective_ext in DOCUMENT_EXTENSIONS:
        return _unknown(
            "ext_unknown",
            f"file_ext {effective_ext} is a document extension without a routing rule",
        )

    return _unknown(
        "ext_unrecognized",
        f"file_ext {effective_ext} is not recognized for parser routing",
    )


def _route(
    route_type: RouteType,
    rule_name: str,
    hint: FutureParserHint,
    reason: str,
) -> tuple[RouteType, str, str, str, str]:
    return route_type, DECISION_ROUTE, rule_name, hint.value, reason


def _unknown(rule_name: str, reason: str) -> tuple[RouteType, str, str, str, str]:
    return RouteType.UNKNOWN, DECISION_UNKNOWN, rule_name, FutureParserHint.NONE.value, reason


def _unsupported(rule_name: str, reason: str) -> tuple[RouteType, str, str, str, str]:
    return (
        RouteType.UNSUPPORTED,
        DECISION_UNSUPPORTED,
        rule_name,
        FutureParserHint.NONE.value,
        reason,
    )


# 014 §6：嗅探状态 → 路由判定闭集映射。任何未列状态由调用方 fail-closed 处理。
_PDF_SNIFF_ROUTE_MAP: dict[SniffStatus, tuple[RouteType, str]] = {
    SniffStatus.TEXT_LAYER_PRESENT: (RouteType.PDF_DIGITAL, "pdf_sniff_digital"),
    SniffStatus.TEXT_LAYER_ABSENT: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_scanned"),
    SniffStatus.ENCRYPTED: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_encrypted"),
    SniffStatus.EMPTY_DOCUMENT: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_empty"),
    SniffStatus.SNIFF_ERROR: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_error"),
    SniffStatus.DEPENDENCY_MISSING: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_unavailable"),
}

# 运行时护栏（复核 [4']）：下述三条此前只被测试消费，import 期即校验，
# 避免新增状态/改错映射时静默 fail-open（把「未嗅探成功」当成数字版）。
assert set(_PDF_SNIFF_ROUTE_MAP) == set(SniffStatus), (
    "嗅探状态映射表必须覆盖全部 SniffStatus 成员"
)
assert {rule for _route_type, rule in _PDF_SNIFF_ROUTE_MAP.values()} <= PDF_SNIFF_RULE_NAMES, (
    "映射产出的 rule_name 必须落在 PDF_SNIFF_RULE_NAMES 闭集内"
)
assert {
    status
    for status, (route_type, _rule) in _PDF_SNIFF_ROUTE_MAP.items()
    if route_type is RouteType.PDF_DIGITAL
} == {SniffStatus.TEXT_LAYER_PRESENT}, (
    "只有 TEXT_LAYER_PRESENT 允许判 PDF_DIGITAL（fail-closed 不变式）"
)


def pdf_sniff_reason(sniff: PdfSniffResult) -> str:
    """由嗅探结果生成 reason。只依赖缓存字段，故缓存命中/未命中逐字段一致。"""

    status = sniff.status
    if status is SniffStatus.TEXT_LAYER_PRESENT:
        return (
            f"pdf text layer detected: {sniff.text_chars} chars over "
            f"{sniff.pages_sampled}/{sniff.pages_total} pages"
        )
    if status is SniffStatus.TEXT_LAYER_ABSENT:
        return (
            f"pdf text layer absent: {sniff.text_chars} chars over "
            f"{sniff.pages_sampled}/{sniff.pages_total} pages"
        )
    if status is SniffStatus.ENCRYPTED:
        return "pdf is encrypted; text layer not readable (no password attempt)"
    if status is SniffStatus.EMPTY_DOCUMENT:
        return "pdf has zero pages; cannot confirm a text layer"
    if status is SniffStatus.DEPENDENCY_MISSING:
        return "pypdf is not installed; cannot verify pdf text layer"
    return f"pdf sniff failed: {sniff.detail or 'unknown error'}"


def route_pdf_with_sniff(sniff: PdfSniffResult) -> tuple[RouteType, str, str, str, str]:
    """014 §6：嗅探 → (route_type, decision, rule_name, hint, reason)。

    fail-closed：映射表未覆盖的状态一律落到 `PDF_SCANNED_OR_IMAGE` / `pdf_sniff_error`。
    """

    mapped = _PDF_SNIFF_ROUTE_MAP.get(sniff.status)
    if mapped is None:
        mapped = (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_error")
    route_type, rule_name = mapped
    return (
        route_type,
        DECISION_ROUTE,
        rule_name,
        FutureParserHint.MINERU_FAMILY.value,
        pdf_sniff_reason(sniff),
    )
