"""016 语义质检指标（第五类语义检查）验收测试。

反假绿原则（acceptance.md 头部）：
- 每个 issue code 都必须同时证明「该报时报」与「**不该报时不报**」，否定断言显式写出；
- 样本不足 / 缺置信度 / 无表格 是**显式跳过**，用对照用例证明跳过不是检测器失效；
- 阈值用**变异**证明真实接线（改阈值 → issue 消失 → 恢复 → 复现）。

fixture 全部现场构造（tmp_path 写产物 + 只读 FakeSession），不落任何二进制。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.config import AppConfig, MysqlConfig, RawConfig, StorageConfig
from app.core.parsed_paths import build_parsed_artifact_paths, build_parsed_content_dir
from app.services.parse_quality_checker import (
    ISSUE_CODES,
    LEGACY_ISSUE_CODES,
    RECOMMENDATION_BY_CODE,
    SCHEMA_VERSION,
    SCHEMA_VERSION_SEMANTIC_DISABLED,
    SEMANTIC_ISSUE_CODES,
    ParseQualityCandidate,
    ParseQualityCheckerService,
    ParseQualityReport,
    SemanticCheckConfig,
    analyze_markdown_tables,
    count_garbled_chars,
    extract_confidence_mean,
    load_semantic_check_config,
)
from app.services.parse_quality_checker import _is_garbled_char
from app.services.parse_quality_report_summarizer import (
    ISSUE_CODES as SUMMARIZER_ISSUE_CODES,
)
from app.services.parse_quality_report_summarizer import (
    ParseQualityReportSummarizerService,
    validate_input_report,
)

SUMMARY_KEYS_008 = {
    "checked_content_count",
    "checked_raw_vault_count",
    "checked_parse_result_count",
    "checked_artifact_count",
    "issue_count",
    "critical_count",
    "error_count",
    "warning_count",
    "info_count",
}
SCOPE_KEYS_008 = {"sha256", "content_uid", "parser_name", "status", "limit"}
# 008 报告的顶层键集（--no-semantic 必须逐字段复现，不得多键）。
TOP_LEVEL_KEYS_008 = {
    "report_type",
    "schema_version",
    "generated_at",
    "mode",
    "scope",
    "summary",
    "issue_counts",
    "by_parser",
    "by_status",
    "by_route_type",
    "by_severity",
    "issues",
    "recommendations",
}


# --------------------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------------------


def _app_config(root: Path) -> AppConfig:
    return AppConfig(
        pipeline_version="v1.1",
        storage=StorageConfig(
            source_registry_root=root / "source_registry",
            raw_vault_root=root / "raw_vault",
            parsed_root=root / "parsed",
            curated_root=root / "curated",
            quarantine_root=root / "quarantine",
            reports_root=root / "reports",
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


class _EmptyScalarResult:
    def all(self) -> list[Any]:
        return []


class _ReadOnlySession:
    """只读会话：任何写操作立即 AssertionError（A016-08 的 DB 零写证据）。"""

    def scalar(self, stmt: Any) -> None:
        return None

    def scalars(self, stmt: Any) -> _EmptyScalarResult:
        return _EmptyScalarResult()

    def __enter__(self) -> _ReadOnlySession:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def add(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("016 must not write to the database (add)")

    def add_all(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("016 must not write to the database (add_all)")

    def flush(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("016 must not write to the database (flush)")

    def commit(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("016 must not write to the database (commit)")

    def delete(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("016 must not write to the database (delete)")

    def execute(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("016 must not write to the database (execute)")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    for name in ("raw_vault", "parsed", "reports"):
        (tmp_path / name).mkdir()
    return tmp_path


def _create_candidate(
    workspace: Path,
    *,
    sha256: str = "a" * 64,
    text: str,
    metadata: dict[str, Any] | str | None = None,
    parser_name: str = "markitdown",
    status: str = "SUCCESS",
    manifest_status: str | None = None,
    content_size_bytes: int | None = None,
    parsed_dir: Path | None = None,
    route_type: str = "OFFICE_DOCX",
    run_uid: str = "parse_run_test",
    result_uid: str = "parse_result_test",
) -> tuple[ParseQualityCandidate, dict[str, Path]]:
    """现场落盘 parsed_text.md / parsed_metadata.json / parse_manifest.json。"""

    content_uid = sha256
    target_dir = parsed_dir or build_parsed_content_dir(workspace / "parsed", sha256)
    target_dir.mkdir(parents=True, exist_ok=True)
    artifacts = build_parsed_artifact_paths(target_dir)
    # 合成一个非临时目录的 vault 路径：避免 008 的 STALE_RAW_VAULT_PATH 噪音
    # （tmp_path 位于 /tmp/ 下，会让每个候选都多一条与语义检查无关的 WARNING）。
    source_vault_path = f"/srv/kb/raw_vault/{sha256}"

    artifacts["parsed_text"].write_text(text, encoding="utf-8")
    if isinstance(metadata, str):
        metadata_text = metadata
    else:
        metadata_text = json.dumps(metadata or {}, ensure_ascii=False, indent=2)
    artifacts["parsed_metadata"].write_text(metadata_text, encoding="utf-8")

    manifest: dict[str, Any] = {
        "content_uid": content_uid,
        "sha256": sha256,
        "route_type": route_type,
        "parser_name": parser_name,
        "parser_adapter_version": "test-adapter-v1",
        "source_vault_path": source_vault_path,
        "parsed_text_path": artifacts["parsed_text"].as_posix(),
        "parsed_metadata_path": artifacts["parsed_metadata"].as_posix(),
        "generated_at": "2026-01-01T00:00:00Z",
        "status": manifest_status or status,
    }
    if parser_name == "mineru":
        manifest["parser_profile"] = "mineru_default_v1"
    if content_size_bytes is not None:
        manifest["content_size_bytes"] = content_size_bytes
    artifacts["parse_manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    candidate = ParseQualityCandidate(
        content_uid=content_uid,
        sha256=sha256,
        parser_name=parser_name,
        parser_adapter_version="test-adapter-v1",
        result_status=status,
        route_type=route_type,
        run_uid=run_uid,
        result_uid=result_uid,
        parsed_dir=target_dir.as_posix(),
        manifest_path=artifacts["parse_manifest"].as_posix(),
        source_vault_path=source_vault_path,
        error_code=None,
    )
    return candidate, dict(artifacts)


def _run(
    workspace: Path,
    candidates: list[ParseQualityCandidate],
    *,
    semantic_config: SemanticCheckConfig | None = None,
    semantic_enabled: bool | None = None,
    **check_kwargs: Any,
) -> ParseQualityReport:
    service = ParseQualityCheckerService(
        _app_config(workspace),
        session_factory=_ReadOnlySession,
        semantic_config=semantic_config or SemanticCheckConfig(),
    )
    service._load_candidates = lambda **kwargs: candidates  # type: ignore[method-assign]
    return service.check(semantic_enabled=semantic_enabled, **check_kwargs)


def _codes(report: ParseQualityReport) -> set[str]:
    return {issue.issue_code for issue in report.issues}


def _issue(report: ParseQualityReport, code: str):
    for issue in report.issues:
        if issue.issue_code == code:
            return issue
    raise AssertionError(f"issue {code} not found; got {sorted(_codes(report))}")


# --- 正文构造 -------------------------------------------------------------------------

CLEAN_SENTENCE = "本段是正常的中文正文内容，用于对照乱码检测是否误报。"
GARBLED_CHAR = "\ufffd"


def _clean_text(total_chars: int = 2000) -> str:
    repeats = total_chars // len(CLEAN_SENTENCE) + 1
    return (CLEAN_SENTENCE * repeats)[:total_chars]


def _garbled_text(*, total_chars: int = 2000, garbled_chars: int) -> str:
    """在干净正文中均匀混入 ``garbled_chars`` 个 U+FFFD，总长保持 total_chars。"""

    clean = _clean_text(total_chars - garbled_chars)
    step = max(1, len(clean) // max(garbled_chars, 1))
    chunks: list[str] = []
    inserted = 0
    for index, char in enumerate(clean):
        if inserted < garbled_chars and index % step == 0:
            chunks.append(GARBLED_CHAR)
            inserted += 1
        chunks.append(char)
    while inserted < garbled_chars:
        chunks.append(GARBLED_CHAR)
        inserted += 1
    return "".join(chunks)


GOOD_TABLE = (
    "| 列一 | 列二 | 列三 |\n"
    "| --- | --- | --- |\n"
    "| a | b | c |\n"
    "| d | e | f |\n"
)
RAGGED_TABLE = (
    "| 列一 | 列二 | 列三 |\n"
    "| --- | --- | --- |\n"
    "| a | b |\n"
    "| d | e | f |\n"
)
MISSING_SEPARATOR_TABLE = "| 列一 | 列二 |\n| a | b |\n"


# --------------------------------------------------------------------------------------
# 纯函数（判定表冻结）
# --------------------------------------------------------------------------------------


def test_is_garbled_char_categories() -> None:
    assert _is_garbled_char(GARBLED_CHAR) is True
    assert _is_garbled_char("\x00") is True
    assert _is_garbled_char("\x1b") is True
    assert _is_garbled_char("\x7f") is True
    assert _is_garbled_char("\x9f") is True
    assert _is_garbled_char("\ue000") is True  # 私有区
    assert _is_garbled_char("\ud800") is True  # 孤立代理
    assert _is_garbled_char("\ufffe") is True  # 非字符
    assert _is_garbled_char("\ufdd0") is True  # 非字符

    # 否定：正常文本与控制白名单不得误判
    assert _is_garbled_char("中") is False
    assert _is_garbled_char("a") is False
    assert _is_garbled_char("\n") is False
    assert _is_garbled_char("\t") is False
    assert _is_garbled_char("\r") is False
    assert _is_garbled_char("，") is False


def test_count_garbled_chars_symbol_run() -> None:
    assert count_garbled_chars("中中中") == 0
    # run 阈值 3：低于阈值不计数，达到阈值整段计数
    assert count_garbled_chars("┌┐") == 0
    assert count_garbled_chars("┌┐└") == 3
    assert count_garbled_chars("┌┐└┘│") == 5
    assert count_garbled_chars("中\x00中") == 1


def test_analyze_markdown_tables() -> None:
    assert analyze_markdown_tables(GOOD_TABLE) == (1, ())
    assert analyze_markdown_tables("纯文本没有表格") == (0, ())
    total, incomplete = analyze_markdown_tables(RAGGED_TABLE)
    assert (total, incomplete) == (1, (1,))
    total, incomplete = analyze_markdown_tables(MISSING_SEPARATOR_TABLE)
    assert (total, incomplete) == (1, (1,))
    mixed = "正文\n" + GOOD_TABLE + "\n" + RAGGED_TABLE
    total, incomplete = analyze_markdown_tables(mixed)
    assert total == 2
    assert incomplete == (7,)


def test_extract_confidence_mean() -> None:
    assert extract_confidence_mean({"ocr_confidence": {"mean": 0.55}}) == 0.55
    assert extract_confidence_mean({"ocr_confidence": {"mean": None}}) is None
    assert extract_confidence_mean({"ocr_confidence": {}}) is None
    assert extract_confidence_mean({}) is None
    assert extract_confidence_mean({"ocr_confidence": "high"}) is None


# --------------------------------------------------------------------------------------
# A016-01 OCR_GARBLED_RATIO 双向断言
# --------------------------------------------------------------------------------------


def test_garbled_warning(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_garbled_text(total_chars=2000, garbled_chars=100)
    )
    report = _run(workspace, [candidate])

    issue = _issue(report, "OCR_GARBLED_RATIO")
    assert issue.severity == "WARNING"
    assert 0.04 <= issue.evidence["garbled_ratio"] <= 0.06
    assert issue.evidence["total_chars"] == 2000
    assert issue.evidence["sampled"] is True
    assert issue.evidence["threshold_warn"] == 0.02
    assert issue.evidence["threshold_error"] == 0.10
    assert report.issue_counts["OCR_GARBLED_RATIO"] == 1


def test_garbled_error(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_garbled_text(total_chars=2000, garbled_chars=300)
    )
    report = _run(workspace, [candidate])

    issue = _issue(report, "OCR_GARBLED_RATIO")
    assert issue.severity == "ERROR"
    assert issue.evidence["garbled_ratio"] >= 0.10


def test_clean_text_no_garbled_issue(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text=_clean_text(2000))
    report = _run(workspace, [candidate])

    # 否定断言：干净正文不得报乱码
    assert "OCR_GARBLED_RATIO" not in _codes(report)
    assert report.issue_counts["OCR_GARBLED_RATIO"] == 0


def test_control_chars_counted_as_garbled(workspace: Path) -> None:
    text = "".join(
        ["\x00\x01\x02\x03" if index % 10 == 0 else "中" for index in range(400)]
    )
    candidate, _ = _create_candidate(workspace, text=text)
    report = _run(workspace, [candidate])

    assert "OCR_GARBLED_RATIO" in _codes(report)


# --------------------------------------------------------------------------------------
# A016-02 样本不足整体跳过（反假绿）+ 对照
# --------------------------------------------------------------------------------------


def test_garbled_skipped_when_sample_too_small(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text=GARBLED_CHAR * 50)
    report = _run(workspace, [candidate])

    # 否定断言：样本不足 → 零 issue（不是「ratio=0 通过」，也不是 INFO 噪音）
    assert "OCR_GARBLED_RATIO" not in _codes(report)
    assert report.issue_counts["OCR_GARBLED_RATIO"] == 0
    assert report.issues == []


def test_garbled_detected_after_padding(workspace: Path) -> None:
    # 同一批乱码内容填到 >= min_sample_chars → 必须报（证明「跳过」是显式逻辑）
    candidate, _ = _create_candidate(
        workspace, text=GARBLED_CHAR * 50 + _clean_text(200)
    )
    report = _run(workspace, [candidate])

    issue = _issue(report, "OCR_GARBLED_RATIO")
    assert issue.evidence["total_chars"] >= 200
    assert issue.severity == "ERROR"


def test_min_sample_chars_threshold_is_wired(workspace: Path) -> None:
    # 同一份 150 字符正文：默认阈值(200) 不评估；min_sample_chars=100 时必须报
    text = GARBLED_CHAR * 60 + _clean_text(90)
    candidate, _ = _create_candidate(workspace, text=text)

    default_report = _run(workspace, [candidate])
    assert "OCR_GARBLED_RATIO" not in _codes(default_report)

    sensitive_report = _run(
        workspace, [candidate], semantic_config=SemanticCheckConfig(min_sample_chars=100)
    )
    assert "OCR_GARBLED_RATIO" in _codes(sensitive_report)


# --------------------------------------------------------------------------------------
# A016-03 OCR_LOW_CONFIDENCE 双向断言 + 反编造
# --------------------------------------------------------------------------------------


def test_low_confidence(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace,
        text=_clean_text(400),
        parser_name="image_ocr",
        metadata={"ocr_confidence": {"mean": 0.55, "min": 0.3, "sample_count": 10}},
    )
    report = _run(workspace, [candidate])

    issue = _issue(report, "OCR_LOW_CONFIDENCE")
    assert issue.severity == "WARNING"
    assert issue.evidence["confidence_mean"] == 0.55
    assert issue.evidence["threshold_min_mean"] == 0.70
    assert "OCR_CONFIDENCE_UNAVAILABLE" not in _codes(report)


def test_high_confidence_no_issue(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace,
        text=_clean_text(400),
        parser_name="image_ocr",
        metadata={"ocr_confidence": {"mean": 0.95, "min": 0.9, "sample_count": 10}},
    )
    report = _run(workspace, [candidate])

    assert "OCR_LOW_CONFIDENCE" not in _codes(report)
    assert "OCR_CONFIDENCE_UNAVAILABLE" not in _codes(report)


@pytest.mark.parametrize("parser_name", ["mineru", "image_ocr"])
def test_missing_confidence_info_only(workspace: Path, parser_name: str) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_clean_text(400), parser_name=parser_name, metadata={}
    )
    report = _run(workspace, [candidate])

    issue = _issue(report, "OCR_CONFIDENCE_UNAVAILABLE")
    assert issue.severity == "INFO"
    # 反编造核心否定断言：缺数据绝不产 LOW_CONFIDENCE
    assert "OCR_LOW_CONFIDENCE" not in _codes(report)
    assert report.issue_counts["OCR_LOW_CONFIDENCE"] == 0


def test_null_confidence_info_only(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace,
        text=_clean_text(400),
        parser_name="image_ocr",
        metadata={"ocr_confidence": {"mean": None, "min": None, "sample_count": 0}},
    )
    report = _run(workspace, [candidate])

    assert _issue(report, "OCR_CONFIDENCE_UNAVAILABLE").severity == "INFO"
    assert "OCR_LOW_CONFIDENCE" not in _codes(report)


def test_markitdown_not_evaluated(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_clean_text(400), parser_name="markitdown", metadata={}
    )
    report = _run(workspace, [candidate])

    # 非 OCR 族：两个 confidence code 都不产
    assert "OCR_LOW_CONFIDENCE" not in _codes(report)
    assert "OCR_CONFIDENCE_UNAVAILABLE" not in _codes(report)


def test_markitdown_low_confidence_not_evaluated(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace,
        text=_clean_text(400),
        parser_name="markitdown",
        metadata={"ocr_confidence": {"mean": 0.10}},
    )
    report = _run(workspace, [candidate])

    assert "OCR_LOW_CONFIDENCE" not in _codes(report)


def test_invalid_metadata_json_skips_confidence(workspace: Path) -> None:
    candidate, artifacts = _create_candidate(
        workspace,
        text=_clean_text(400),
        parser_name="image_ocr",
        metadata={},
    )
    artifacts["parsed_metadata"].write_text("{not-json", encoding="utf-8")

    report = _run(workspace, [candidate])
    assert "OCR_LOW_CONFIDENCE" not in _codes(report)
    assert "OCR_CONFIDENCE_UNAVAILABLE" not in _codes(report)
    # 批处理不中断：乱码检查仍然执行
    assert report.report_path is not None and report.report_path.is_file()


# --------------------------------------------------------------------------------------
# A016-04 TABLE_STRUCTURE_INCOMPLETE 双向断言
# --------------------------------------------------------------------------------------


def test_ragged_pipe_table(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text=RAGGED_TABLE)
    report = _run(workspace, [candidate])

    issue = _issue(report, "TABLE_STRUCTURE_INCOMPLETE")
    assert issue.severity == "WARNING"
    assert issue.evidence["total_tables"] == 1
    assert issue.evidence["incomplete_tables"] == 1
    assert issue.evidence["incomplete_table_lines"] == [1]


def test_table_missing_separator_row(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text=MISSING_SEPARATOR_TABLE)
    report = _run(workspace, [candidate])

    assert "TABLE_STRUCTURE_INCOMPLETE" in _codes(report)


def test_valid_table_no_issue(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text=GOOD_TABLE)
    report = _run(workspace, [candidate])

    assert "TABLE_STRUCTURE_INCOMPLETE" not in _codes(report)


def test_plain_text_no_table_no_issue(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text=_clean_text(600))
    report = _run(workspace, [candidate])

    assert "TABLE_STRUCTURE_INCOMPLETE" not in _codes(report)


def test_tables_disabled_skips(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text=RAGGED_TABLE)
    report = _run(
        workspace, [candidate], semantic_config=SemanticCheckConfig(tables_enabled=False)
    )

    assert "TABLE_STRUCTURE_INCOMPLETE" not in _codes(report)


# --------------------------------------------------------------------------------------
# A016-05 EMPTY_BUT_NONZERO_PAGES
# --------------------------------------------------------------------------------------


def test_empty_but_nonzero_pages(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text="   \n\n  \t", content_size_bytes=54321
    )
    report = _run(workspace, [candidate])

    issue = _issue(report, "EMPTY_BUT_NONZERO_PAGES")
    assert issue.severity == "ERROR"
    assert issue.evidence["content_size_bytes"] == 54321
    assert issue.evidence["result_status"] == "SUCCESS"


def test_empty_text_with_pages_metadata(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text="", metadata={"pages": ["p1", "p2", "p3"]}
    )
    report = _run(workspace, [candidate])

    issue = _issue(report, "EMPTY_BUT_NONZERO_PAGES")
    assert issue.evidence["page_count"] == 3
    assert issue.severity == "ERROR"


def test_empty_status_no_issue(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text="", status="EMPTY", content_size_bytes=54321
    )
    report = _run(workspace, [candidate])

    # 对照 A：既有 REGISTRY_EMPTY_RESULT 覆盖，不产语义 code
    assert "EMPTY_BUT_NONZERO_PAGES" not in _codes(report)
    assert "REGISTRY_EMPTY_RESULT" in _codes(report)


def test_nonempty_text_no_issue(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_clean_text(400), content_size_bytes=54321
    )
    report = _run(workspace, [candidate])

    # 对照 B
    assert "EMPTY_BUT_NONZERO_PAGES" not in _codes(report)


def test_empty_text_without_source_evidence_no_issue(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text="")
    report = _run(workspace, [candidate])

    # 源本身无内容证据 → 不评估（避免对真空文件误报）
    assert "EMPTY_BUT_NONZERO_PAGES" not in _codes(report)


def test_empty_text_disabled_skips(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text="", content_size_bytes=100)
    report = _run(
        workspace,
        [candidate],
        semantic_config=SemanticCheckConfig(empty_text_enabled=False),
    )

    assert "EMPTY_BUT_NONZERO_PAGES" not in _codes(report)


# --------------------------------------------------------------------------------------
# A016-06 阈值接线变异复验（反假绿核心）
# --------------------------------------------------------------------------------------


def test_garbled_threshold_mutation(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_garbled_text(total_chars=2000, garbled_chars=100)
    )

    baseline = _run(workspace, [candidate])
    assert "OCR_GARBLED_RATIO" in _codes(baseline)

    # 变异 1：warn/error 都调高到 0.50 → issue 必须消失（阈值真接线）
    mutated = _run(
        workspace,
        [candidate],
        semantic_config=SemanticCheckConfig(
            garbled_warn_ratio=0.50, garbled_error_ratio=0.50
        ),
    )
    assert "OCR_GARBLED_RATIO" not in _codes(mutated)

    # 恢复默认阈值 → 必须复现
    restored = _run(workspace, [candidate])
    assert "OCR_GARBLED_RATIO" in _codes(restored)


def test_confidence_threshold_mutation(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace,
        text=_clean_text(400),
        parser_name="image_ocr",
        metadata={"ocr_confidence": {"mean": 0.55}},
    )

    baseline = _run(workspace, [candidate])
    assert "OCR_LOW_CONFIDENCE" in _codes(baseline)

    mutated = _run(
        workspace,
        [candidate],
        semantic_config=SemanticCheckConfig(confidence_min_mean=0.10),
    )
    assert "OCR_LOW_CONFIDENCE" not in _codes(mutated)

    restored = _run(workspace, [candidate])
    assert "OCR_LOW_CONFIDENCE" in _codes(restored)


def test_invalid_threshold_fail_fast() -> None:
    with pytest.raises(ValueError):
        SemanticCheckConfig(garbled_warn_ratio=-1.0)
    with pytest.raises(ValueError):
        SemanticCheckConfig(garbled_error_ratio=1.5)
    with pytest.raises(ValueError):
        SemanticCheckConfig(confidence_min_mean=-0.1)
    with pytest.raises(ValueError):
        SemanticCheckConfig(min_sample_chars=-5)


def test_yaml_invalid_threshold_fail_fast(tmp_path: Path) -> None:
    yaml_path = tmp_path / "quality_semantics.yaml"
    yaml_path.write_text(
        "semantic_checks:\n  garbled:\n    warn_ratio: -1\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_semantic_check_config(yaml_path)


def test_yaml_missing_uses_defaults(tmp_path: Path) -> None:
    loaded = load_semantic_check_config(tmp_path / "does-not-exist.yaml")
    assert loaded == SemanticCheckConfig()


def test_project_yaml_matches_frozen_defaults() -> None:
    # config/quality_semantics.yaml 必须与 Owner 裁定 O-3 冻结值一致
    loaded = load_semantic_check_config()
    assert loaded.enabled is True
    assert loaded.min_sample_chars == 200
    assert loaded.garbled_warn_ratio == 0.02
    assert loaded.garbled_error_ratio == 0.10
    assert loaded.confidence_min_mean == 0.70
    assert loaded.tables_enabled is True
    assert loaded.empty_text_enabled is True


def test_yaml_overrides_thresholds(tmp_path: Path) -> None:
    yaml_path = tmp_path / "quality_semantics.yaml"
    yaml_path.write_text(
        "semantic_checks:\n"
        "  enabled: false\n"
        "  garbled:\n"
        "    min_sample_chars: 50\n"
        "    warn_ratio: 0.5\n"
        "    error_ratio: 0.6\n"
        "  confidence:\n"
        "    min_mean: 0.2\n"
        "    ocr_parser_names: [mineru]\n",
        encoding="utf-8",
    )
    loaded = load_semantic_check_config(yaml_path)
    assert loaded.enabled is False
    assert loaded.min_sample_chars == 50
    assert loaded.garbled_warn_ratio == 0.5
    assert loaded.garbled_error_ratio == 0.6
    assert loaded.confidence_min_mean == 0.2
    assert loaded.ocr_parser_names == ("mineru",)


# --------------------------------------------------------------------------------------
# A016-07 --no-semantic 冻结 008 行为
# --------------------------------------------------------------------------------------


def test_no_semantic_freezes_008_report(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_garbled_text(total_chars=2000, garbled_chars=100)
    )
    enabled_report = _run(workspace, [candidate])
    frozen_payload = _run(workspace, [candidate], semantic_enabled=False).to_dict()

    assert frozen_payload["schema_version"] == SCHEMA_VERSION_SEMANTIC_DISABLED == "1.0"
    assert "semantic_checks_enabled" not in frozen_payload
    assert set(frozen_payload["issue_counts"]) == set(LEGACY_ISSUE_CODES)
    assert len(frozen_payload["issue_counts"]) == 18
    assert not (set(frozen_payload["issue_counts"]) & set(SEMANTIC_ISSUE_CODES))
    assert all(
        issue["issue_code"] not in SEMANTIC_ISSUE_CODES
        for issue in frozen_payload["issues"]
    )
    # TL 裁定 (c)：--no-semantic 与 008 **完全一致**，scope 也只有 008 的 5 个键
    assert set(frozen_payload) == TOP_LEVEL_KEYS_008
    assert set(frozen_payload["summary"]) == SUMMARY_KEYS_008
    assert set(frozen_payload["scope"]) == SCOPE_KEYS_008
    assert "semantic" not in frozen_payload["scope"]

    # 对照：语义开启时同一批候选确实产出了语义 issue（证明是开关生效，不是检测器失效）
    assert "OCR_GARBLED_RATIO" in _codes(enabled_report)
    assert enabled_report.scope["semantic"] is True


def test_no_semantic_payload_equals_008_reference(workspace: Path) -> None:
    """全字段相等（TL 裁定 (c)）：与手写 008 参考 payload 逐字段比对，不只比键集。"""

    candidate, _ = _create_candidate(
        workspace,
        text=_garbled_text(total_chars=2000, garbled_chars=100) + RAGGED_TABLE,
        parser_name="image_ocr",
        metadata={"ocr_confidence": {"mean": 0.5}},
    )
    frozen = _run(workspace, [candidate], semantic_enabled=False).to_dict()
    assert "generated_at" in frozen
    frozen.pop("generated_at")

    reference_008 = {
        "report_type": "parse_quality_report",
        "schema_version": "1.0",
        "mode": "check",
        "scope": {
            "sha256": None,
            "content_uid": None,
            "parser_name": None,
            "status": None,
            "limit": None,
        },
        "summary": {
            "checked_content_count": 1,
            "checked_raw_vault_count": 0,
            "checked_parse_result_count": 1,
            "checked_artifact_count": 0,
            "issue_count": 0,
            "critical_count": 0,
            "error_count": 0,
            "warning_count": 0,
            "info_count": 0,
        },
        "issue_counts": {code: 0 for code in LEGACY_ISSUE_CODES},
        "by_parser": {},
        "by_status": {},
        "by_route_type": {},
        "by_severity": {},
        "issues": [],
        "recommendations": [],
    }
    # 同一候选若开启语义检查会产 3 类语义 issue → 关闭后必须一个都不剩且其它字段完全一致
    assert json.dumps(frozen, sort_keys=True, ensure_ascii=False) == json.dumps(
        reference_008, sort_keys=True, ensure_ascii=False
    )


def test_no_semantic_does_not_read_text(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate, _ = _create_candidate(workspace, text=_clean_text(400))

    read_calls: list[Path] = []
    original_read_text = Path.read_text

    def counting_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "parsed_text.md":
            read_calls.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    _run(workspace, [candidate], semantic_enabled=False)
    # 否定断言：--no-semantic 路径不得读取 parsed_text.md
    assert read_calls == []

    # 正向对照：开启语义检查时确实读取（证明计数器有效，不是空转）
    _run(workspace, [candidate], semantic_enabled=True)
    assert len(read_calls) == 1


# --------------------------------------------------------------------------------------
# A016-08 只读红线与幂等 / 单候选容错
# --------------------------------------------------------------------------------------


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_readonly_redline(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace,
        text=_garbled_text(total_chars=600, garbled_chars=60) + RAGGED_TABLE,
        content_size_bytes=1024,
    )
    before = _tree_hashes(workspace / "parsed")
    raw_before = _tree_hashes(workspace / "raw_vault")

    report = _run(workspace, [candidate])

    # _ReadOnlySession 会在任何 DB 写操作时 AssertionError；此处再断言产物未被改写
    assert _tree_hashes(workspace / "parsed") == before
    assert _tree_hashes(workspace / "raw_vault") == raw_before
    assert report.report_path is not None
    assert report.report_path.parent == workspace / "reports"


def test_idempotent_issue_set(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace,
        text=_garbled_text(total_chars=2000, garbled_chars=100) + RAGGED_TABLE,
        metadata={"ocr_confidence": {"mean": 0.5}},
        parser_name="image_ocr",
    )
    first = _run(workspace, [candidate]).to_dict()
    second = _run(workspace, [candidate]).to_dict()

    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_read_failure_isolated_to_candidate(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken, broken_artifacts = _create_candidate(
        workspace,
        sha256="b" * 64,
        text=_garbled_text(total_chars=2000, garbled_chars=100),
        run_uid="run_broken",
        result_uid="res_broken",
    )
    healthy, _ = _create_candidate(
        workspace,
        sha256="c" * 64,
        text=_garbled_text(total_chars=2000, garbled_chars=100),
        run_uid="run_healthy",
        result_uid="res_healthy",
    )

    blocked = broken_artifacts["parsed_text"]
    original_read_text = Path.read_text

    def flaky_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == blocked:
            raise OSError(5, "Input/output error", str(self))
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    report = _run(workspace, [broken, healthy])

    garbled_issues = [
        issue for issue in report.issues if issue.issue_code == "OCR_GARBLED_RATIO"
    ]
    # 只对健康候选产 issue；坏候选被隔离，批处理不中断
    assert len(garbled_issues) == 1
    assert garbled_issues[0].content_uid == healthy.content_uid
    assert report.summary["checked_semantic_count"] == 1
    assert report.report_path is not None


# --------------------------------------------------------------------------------------
# 报告 schema 1.1 / 常量顺序
# --------------------------------------------------------------------------------------


def test_semantic_codes_appended_after_legacy() -> None:
    assert ISSUE_CODES[: len(LEGACY_ISSUE_CODES)] == LEGACY_ISSUE_CODES
    assert ISSUE_CODES[len(LEGACY_ISSUE_CODES) :] == SEMANTIC_ISSUE_CODES
    assert len(LEGACY_ISSUE_CODES) == 18
    assert len(SEMANTIC_ISSUE_CODES) == 5
    for code in SEMANTIC_ISSUE_CODES:
        assert code in RECOMMENDATION_BY_CODE


def test_report_schema_1_1(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_garbled_text(total_chars=2000, garbled_chars=100)
    )
    failed, _ = _create_candidate(
        workspace,
        sha256="d" * 64,
        text="",
        status="FAILED",
        run_uid="run_failed",
        result_uid="res_failed",
    )
    report = _run(workspace, [candidate, failed])
    payload = report.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION == "1.1"
    assert payload["semantic_checks_enabled"] is True
    assert set(payload["issue_counts"]) == set(ISSUE_CODES)
    assert len(payload["issue_counts"]) == 23
    assert "checked_semantic_count" in payload["summary"]
    # 仅 SUCCESS 候选计入语义检查
    assert payload["summary"]["checked_semantic_count"] == 1
    assert payload["scope"]["semantic"] is True
    assert "OCR_GARBLED_RATIO" in payload["recommendations"][0] or any(
        "OCR garbage" in item for item in payload["recommendations"]
    )


def test_semantic_checks_disabled_by_yaml(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace, text=_garbled_text(total_chars=2000, garbled_chars=100)
    )
    report = _run(
        workspace, [candidate], semantic_config=SemanticCheckConfig(enabled=False)
    )
    payload = report.to_dict()

    assert payload["schema_version"] == "1.0"
    assert "semantic_checks_enabled" not in payload
    assert set(payload["issue_counts"]) == set(LEGACY_ISSUE_CODES)
    assert "OCR_GARBLED_RATIO" not in _codes(report)
    # yaml enabled=false 与 --no-semantic 等价：同样省略 scope.semantic
    assert set(payload) == TOP_LEVEL_KEYS_008
    assert "semantic" not in payload["scope"]


# --------------------------------------------------------------------------------------
# A016-09 009 summarizer 联动 + image_ocr 白名单
# --------------------------------------------------------------------------------------


def _report_payload(*, schema_version: str = "1.1") -> dict[str, Any]:
    issue_counts = {code: 0 for code in LEGACY_ISSUE_CODES}
    if schema_version == "1.1":
        issue_counts.update({code: 0 for code in SEMANTIC_ISSUE_CODES})
        issue_counts["OCR_GARBLED_RATIO"] = 1
        issues = [
            {
                "issue_code": "OCR_GARBLED_RATIO",
                "severity": "ERROR",
                "content_uid": "c1",
                "sha256": "a" * 64,
                "parser_name": "image_ocr",
                "parser_adapter_version": "015_mvp_v1",
                "artifact_type": "PARSED_TEXT",
                "path": "/data/parsed/parsed_text.md",
                "message": "garbled ratio too high",
                "evidence": {"garbled_ratio": 0.13},
            }
        ]
    else:
        issues = [
            {
                "issue_code": "MISSING_PARSED_TEXT",
                "severity": "ERROR",
                "content_uid": "c2",
                "sha256": "b" * 64,
                "parser_name": "markitdown",
                "parser_adapter_version": "1.0.0",
                "artifact_type": "PARSED_TEXT",
                "path": "/data/parsed/missing.md",
                "message": "missing",
                "evidence": {},
            }
        ]
        issue_counts["MISSING_PARSED_TEXT"] = 1

    payload: dict[str, Any] = {
        "report_type": "parse_quality_report",
        "schema_version": schema_version,
        "generated_at": "2026-10-17T00:00:00Z",
        "mode": "check",
        "scope": {
            "sha256": None,
            "content_uid": None,
            "parser_name": None,
            "status": None,
            "limit": None,
        },
        "summary": {
            "checked_content_count": 1,
            "checked_raw_vault_count": 0,
            "checked_parse_result_count": 1,
            "checked_artifact_count": 0,
            "issue_count": 1,
            "critical_count": 0,
            "error_count": 1,
            "warning_count": 0,
            "info_count": 0,
        },
        "issue_counts": issue_counts,
        "by_parser": {"image_ocr": 1},
        "by_status": {"SUCCESS": 1},
        "by_route_type": {"IMAGE": 1},
        "by_severity": {"ERROR": 1},
        "issues": issues,
        "recommendations": ["Inspect parsed_text.md for OCR garbage."],
    }
    if schema_version == "1.1":
        payload["semantic_checks_enabled"] = True
        payload["summary"]["checked_semantic_count"] = 1
    return payload


def _summarizer_env(workspace: Path):
    config = _app_config(workspace)
    reports_root = workspace / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    return config, reports_root


def test_summarizer_accepts_semantic_report(workspace: Path) -> None:
    config, reports_root = _summarizer_env(workspace)
    payload = _report_payload(schema_version="1.1")
    input_path = reports_root / "parse_quality_report_20261017T000000Z.json"
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    result = ParseQualityReportSummarizerService(config).summarize(
        input_path=input_path, output_format="json"
    )
    data = json.loads(result.summary_path.read_text(encoding="utf-8"))

    # 1.1 报告不被拒；新 code 进入汇总计数
    assert set(data["issue_counts"]) == set(SUMMARIZER_ISSUE_CODES)
    assert data["issue_counts"]["OCR_GARBLED_RATIO"] == 1
    assert data["filtered_issue_counts"]["OCR_GARBLED_RATIO"] == 1
    assert data["top_issue_codes"][0]["issue_code"] == "OCR_GARBLED_RATIO"


def test_summarizer_issue_code_filter_accepts_new_code(workspace: Path) -> None:
    config, reports_root = _summarizer_env(workspace)
    payload = _report_payload(schema_version="1.1")
    input_path = reports_root / "parse_quality_report_20261017T000001Z.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = ParseQualityReportSummarizerService(config).summarize(
        input_path=input_path,
        output_format="json",
        issue_codes=["OCR_GARBLED_RATIO"],
    )
    assert result.filtered_issue_count == 1

    # 其它语义 code 过滤为空（过滤真生效，非只做合法性校验）
    empty_result = ParseQualityReportSummarizerService(config).summarize(
        input_path=input_path,
        output_format="json",
        issue_codes=["TABLE_STRUCTURE_INCOMPLETE"],
    )
    assert empty_result.filtered_issue_count == 0


def test_summarizer_accepts_legacy_1_0_report() -> None:
    # 增量键兼容：1.0 报告（仅 18 键）仍可汇总，不因新 code 缺失被拒
    validate_input_report(_report_payload(schema_version="1.0"))


def test_summarizer_rejects_unknown_schema_version() -> None:
    # TL 约束 1：未知版本必须 raise，不得变成白名单放行
    payload = _report_payload(schema_version="1.1")
    payload["schema_version"] = "2.0"
    with pytest.raises(ValueError, match="schema_version"):
        validate_input_report(payload)


def test_summarizer_still_requires_legacy_issue_codes() -> None:
    # TL 约束 2：18 个 legacy 键仍然必填（校验强度不得降低），1.1 下同样如此
    payload = _report_payload(schema_version="1.1")
    del payload["issue_counts"]["MISSING_PARSED_TEXT"]
    with pytest.raises(ValueError, match="issue_counts missing key"):
        validate_input_report(payload)


def test_summarizer_semantic_keys_optional_and_zero_filled(workspace: Path) -> None:
    # TL 约束 2：5 个语义键可缺席（不因增量键缺失被拒），输出按 0 归一化
    config, reports_root = _summarizer_env(workspace)
    payload = _report_payload(schema_version="1.1")
    for code in SEMANTIC_ISSUE_CODES:
        payload["issue_counts"].pop(code)
    validate_input_report(payload)

    input_path = reports_root / "parse_quality_report_20261017T000002Z.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = ParseQualityReportSummarizerService(config).summarize(
        input_path=input_path, output_format="json"
    )
    data = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert data["issue_counts"]["OCR_GARBLED_RATIO"] == 0
    assert set(data["issue_counts"]) == set(SUMMARIZER_ISSUE_CODES)


def test_summarizer_rejects_semantic_codes_in_1_0_report() -> None:
    # TL 授权 fail-closed：1.0 报告出现语义键 = 生产者 bug，必须 raise
    payload = _report_payload(schema_version="1.0")
    payload["issue_counts"]["OCR_GARBLED_RATIO"] = 0
    with pytest.raises(ValueError, match="must not contain semantic codes"):
        validate_input_report(payload)


def test_summarizer_rejects_invalid_semantic_count() -> None:
    # 语义键缺席可容忍，但present 时必须合法（不得静默接受坏值）
    payload = _report_payload(schema_version="1.1")
    payload["issue_counts"]["OCR_GARBLED_RATIO"] = -1
    with pytest.raises(ValueError, match="must be >= 0"):
        validate_input_report(payload)


def test_summarizer_schema_version_constant_unchanged() -> None:
    # TL 约束 3：009 输出 schema_version 仍为 "1.0"，不得漂移
    import app.services.parse_quality_report_summarizer as summarizer_module

    assert summarizer_module.SCHEMA_VERSION == "1.0"
    assert summarizer_module.INPUT_SCHEMA_VERSION == "1.0"
    assert summarizer_module.INPUT_SCHEMA_VERSIONS == frozenset({"1.0", "1.1"})


def test_summarizer_codes_match_checker() -> None:
    assert SUMMARIZER_ISSUE_CODES == ISSUE_CODES


def test_image_ocr_parser_name_allowed(workspace: Path) -> None:
    candidate, _ = _create_candidate(
        workspace,
        text=_clean_text(400),
        parser_name="image_ocr",
        metadata={"ocr_confidence": {"mean": 0.9}},
        route_type="IMAGE",
    )
    report = _run(workspace, [candidate])
    assert "MANIFEST_PARSER_NAME_INVALID" not in _codes(report)
    assert report.issues == []

    # CLI 过滤器同样接受 image_ocr（不再被 ValueError 拒）
    filtered = _run(workspace, [candidate], parser_name="image_ocr")
    assert filtered.summary["checked_parse_result_count"] == 1


# --------------------------------------------------------------------------------------
# CLI --no-semantic 接线（T010）
# --------------------------------------------------------------------------------------


def _cli_config(workspace: Path, tmp_path: Path) -> Path:
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        "app:\n"
        "  pipeline_version: v1.1\n"
        "storage:\n"
        f"  source_registry_root: {workspace / 'source_registry'}\n"
        f"  raw_vault_root: {workspace / 'raw_vault'}\n"
        f"  parsed_root: {workspace / 'parsed'}\n"
        f"  curated_root: {workspace / 'curated'}\n"
        f"  quarantine_root: {workspace / 'quarantine'}\n"
        f"  reports_root: {workspace / 'reports'}\n"
        "mysql:\n"
        "  host: 127.0.0.1\n"
        "  port: 3306\n"
        "  database: personal_kb\n"
        "  username: personal_kb\n"
        "  password: test\n"
        "  charset: utf8mb4\n"
        "raw:\n"
        "  original_files_readonly: true\n"
        "  copy_unique_content_to_vault: true\n",
        encoding="utf-8",
    )
    return config_path


def test_cli_no_semantic_flag_plumbing(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from app.cli.main import app
    from app.services.parse_quality_checker import ParseQualityCheckerService

    config_path = _cli_config(workspace, tmp_path)
    captured: dict[str, Any] = {}

    def fake_check(self: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return type(
            "Report",
            (),
            {
                "summary": {
                    "issue_count": 0,
                    "critical_count": 0,
                    "error_count": 0,
                    "warning_count": 0,
                    "checked_parse_result_count": 0,
                },
                "report_path": workspace / "reports" / "parse_quality_report_test.json",
            },
        )()

    monkeypatch.setattr(ParseQualityCheckerService, "check", fake_check)
    runner = CliRunner()

    default_run = runner.invoke(app, ["check-parse-quality", "--config", str(config_path)])
    assert default_run.exit_code == 0
    # 未给 flag → None（沿用 yaml 配置），不得强制 True 覆盖 yaml enabled=false
    assert captured["semantic_enabled"] is None

    captured.clear()
    frozen_run = runner.invoke(
        app, ["check-parse-quality", "--config", str(config_path), "--no-semantic"]
    )
    assert frozen_run.exit_code == 0
    assert captured["semantic_enabled"] is False


# --------------------------------------------------------------------------------------
# A016-10 中文路径与序列化
# --------------------------------------------------------------------------------------


def test_chinese_path_semantic(workspace: Path) -> None:
    chinese_dir = workspace / "parsed" / "中文路径" / "样例目录"
    candidate, _ = _create_candidate(
        workspace,
        sha256="e" * 64,
        text=_garbled_text(total_chars=2000, garbled_chars=100),
        parsed_dir=chinese_dir,
    )
    report = _run(workspace, [candidate])

    issue = _issue(report, "OCR_GARBLED_RATIO")
    assert "中文路径" in issue.path
    raw_report = report.report_path.read_text(encoding="utf-8")
    assert "中文路径" in raw_report
    # ensure_ascii=False：报告里不得出现中文路径的 \u 转义
    assert "\\u4e2d\\u6587" not in raw_report
    persisted = [
        item
        for item in json.loads(raw_report)["issues"]
        if item["issue_code"] == "OCR_GARBLED_RATIO"
    ]
    assert len(persisted) == 1
    assert persisted[0]["path"] == issue.path


def test_report_generated_at_is_iso(workspace: Path) -> None:
    candidate, _ = _create_candidate(workspace, text=_clean_text(400))
    payload = _run(workspace, [candidate]).to_dict()
    generated_at = payload["generated_at"]
    assert generated_at.endswith("Z")
    datetime.fromisoformat(generated_at.replace("Z", "+00:00"))


# --------------------------------------------------------------------------------------
# 五类检查共存（同一候选同时命中多类）
# --------------------------------------------------------------------------------------


def test_multiple_semantic_codes_on_same_candidate(workspace: Path) -> None:
    text = _garbled_text(total_chars=2000, garbled_chars=100) + RAGGED_TABLE
    candidate, _ = _create_candidate(
        workspace,
        text=text,
        parser_name="image_ocr",
        metadata={"ocr_confidence": {"mean": 0.4}},
    )
    report = _run(workspace, [candidate])
    codes = _codes(report)

    for code in (
        "OCR_GARBLED_RATIO",
        "OCR_LOW_CONFIDENCE",
        "TABLE_STRUCTURE_INCOMPLETE",
    ):
        assert code in codes
    # 有真实置信度时不产 UNAVAILABLE（避免「既有数据又说不存在」的矛盾）
    assert "OCR_CONFIDENCE_UNAVAILABLE" not in codes
    # 非空正文不产 EMPTY_BUT_NONZERO_PAGES
    assert "EMPTY_BUT_NONZERO_PAGES" not in codes
    assert report.summary["checked_semantic_count"] == 1
