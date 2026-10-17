"""014 路由服务层 v2 单测（**不依赖 MySQL**）。

覆盖 TC014-05 / 08 / 09 / 10 / 11 / 12 / 13 / 15 / 16，
以及 A014-02 / 03 / 04 / 08 的服务层与报告层断言。

为什么无需 MySQL：`ParserRouterService` 的 DB 访问只发生在 `_load_candidates()`
与 `_route_content()` 内部的 `session.scalar(...)`。本文件用确定性假 session 替换
`session_factory`，从而能真实跑通 `route_parsers()`（含报告写入 / dry-run / 缓存 /
计数），而不是把服务层证据全部让给被 skip 的 MySQL 用例。

反假绿：每条「必须为 Y / 不得为 X」的断言都配对照调用（换 fixture、换 rules_version
或换 dry_run 后必须翻转），避免出现恒真断言。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import AppConfig, load_config
from app.core.parser_routing import (
    ROUTING_RULES_VERSION,
    RouteType,
    match_route_type,
)
from app.core.pdf_sniffing import (
    REASON_SNIFF_SKIPPED,
    PdfSniffResult,
    SniffStatus,
    cache_path_for,
)
from app.core.vault_paths import build_vault_artifact_paths, build_vault_dir
from app.services import parser_router as parser_router_module
from app.services.parser_router import ParserRouterService
from tests.test_pdf_sniffing import (
    build_pdf_bytes,
    write_corrupted_pdf,
    write_digital_pdf,
    write_scanned_pdf,
)

V1_RULES_VERSION = "004_mvp_v1"

assert V1_RULES_VERSION != ROUTING_RULES_VERSION, "v1 与 v2 版本号必须不同（否则用例无意义）"


# --------------------------------------------------------------------------- #
# 测试环境：无 MySQL 的 AppConfig + 确定性假 session
# --------------------------------------------------------------------------- #


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
  sniff_cache_root: {workspace_root / "sniff_cache"}

mysql:
  host: 127.0.0.1
  port: 3306
  database: personal_kb
  username: personal_kb
  password: unused-in-this-test
  charset: utf8mb4

raw:
  original_files_readonly: true
  copy_unique_content_to_vault: true
""",
        encoding="utf-8",
    )


@dataclass
class _Harness:
    service: ParserRouterService
    config: AppConfig
    workspace_root: Path

    @property
    def raw_vault_root(self) -> Path:
        return self.config.storage.raw_vault_root

    @property
    def reports_root(self) -> Path:
        return self.config.storage.reports_root

    @property
    def sniff_cache_root(self) -> Path:
        return self.config.storage.sniff_cache_root

    def snapshot(self) -> dict[str, object]:
        """记录 raw_vault / reports / sniff_cache / 源码目录 的全部文件与哈希。"""

        roots = {
            "raw_vault": self.raw_vault_root,
            "reports": self.reports_root,
            "sniff_cache": self.sniff_cache_root,
        }
        snapshot: dict[str, object] = {}
        for name, root in roots.items():
            if not root.exists():
                snapshot[name] = {}
                continue
            snapshot[name] = {
                path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(root.rglob("*"))
                if path.is_file()
            }
        return snapshot


def _new_service(config_path: Path, workspace_root: Path) -> _Harness:
    _write_test_config(config_path, workspace_root)
    config = load_config(config_path)
    return _Harness(service=ParserRouterService(config), config=config, workspace_root=workspace_root)


@pytest.fixture
def harness(tmp_path: Path) -> _Harness:
    return _new_service(tmp_path / "app.yaml", tmp_path / "workspace")


class _FakeSession:
    """只实现 `scalar()` 的最小 session；额外调用即失败（暴露实现漂移）。"""

    def __init__(self, results: list[object]) -> None:
        self._results = results

    def scalar(self, _statement: object) -> object:
        if not self._results:
            raise AssertionError("unexpected extra session.scalar() call")
        return self._results.pop(0)

    def scalars(self, _statement: object) -> object:
        raise AssertionError("session.scalars() 不应在归一化扩展名下被调用")

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _FakeSessionFactory:
    """按调用顺序把 (content, vault_object) 成对喂给每个新 session。"""

    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.sessions = 0

    def __call__(self) -> _FakeSession:
        self.sessions += 1
        return _FakeSession(self._results)


def _fake_content(
    *,
    sha256: str,
    vault_path: Path | None,
    file_ext: str | None = ".pdf",
    mime_type: str | None = "application/pdf",
    content_uid: str = "uid-014",
) -> SimpleNamespace:
    return SimpleNamespace(
        sha256=sha256,
        file_ext=file_ext,
        mime_type=mime_type,
        vault_path=str(vault_path) if vault_path is not None else None,
        content_uid=content_uid,
        parse_status=None,
        master_file_instance_uid=None,
    )


def _fake_vault_object(*, sha256: str, vault_path: Path) -> SimpleNamespace:
    return SimpleNamespace(sha256=sha256, vault_path=str(vault_path), vault_uid=sha256)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _place_vault_pdf(vault_dir: Path, payload: bytes) -> Path:
    original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]
    original_bin.parent.mkdir(parents=True, exist_ok=True)
    original_bin.write_bytes(payload)
    return original_bin


def _digital_pdf_bytes(*, chars: int = 240) -> bytes:
    return build_pdf_bytes(page_texts=["A" * chars])


def _scanned_pdf_bytes(*, pages: int = 1) -> bytes:
    return build_pdf_bytes(page_texts=["" for _ in range(pages)])


def _route_content(
    harness: _Harness,
    *,
    content: SimpleNamespace,
    vault_object: SimpleNamespace | None = None,
    rules_version: str = ROUTING_RULES_VERSION,
    use_sniff_cache: bool = True,
    dry_run: bool = False,
):
    factory = _FakeSessionFactory([content, vault_object])
    harness.service.session_factory = factory  # type: ignore[assignment]
    return harness.service._route_content(
        content,
        rules_version=rules_version,
        use_sniff_cache=use_sniff_cache,
        dry_run=dry_run,
    )


def _route_all(
    harness: _Harness,
    *,
    contents: list[SimpleNamespace],
    vault_objects: list[SimpleNamespace] | None = None,
    **kwargs: object,
):
    results: list[object] = []
    for index, content in enumerate(contents):
        vault_object = None if vault_objects is None else vault_objects[index]
        results.append(_route_content(harness, content=content, vault_object=vault_object, **kwargs))
    return results


def _pdf_in_vault(harness: _Harness, payload: bytes, *, vault_dir: Path | None = None):
    """把 payload 落到 vault 的 original.bin，返回 (sha256, vault_dir)。"""

    sha256 = _sha256_bytes(payload)
    directory = (
        vault_dir if vault_dir is not None else build_vault_dir(harness.raw_vault_root, sha256)
    )
    _place_vault_pdf(directory, payload)
    return sha256, directory


def _empty_vault_dir(harness: _Harness, sha256: str) -> Path:
    directory = build_vault_dir(harness.raw_vault_root, sha256)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# 004 冻结路径对 .pdf / application/pdf 的基准结果，用作"逐字节未变"的参照。
_V1_PDF_BASE = match_route_type(
    file_ext=".pdf", mime_type="application/pdf", fallback_ext=None
)
_EXPECTED_PDF_DECISION = _V1_PDF_BASE[1]

_NON_PRESENT_RULE = {
    SniffStatus.TEXT_LAYER_ABSENT: "pdf_sniff_scanned",
    SniffStatus.ENCRYPTED: "pdf_sniff_encrypted",
    SniffStatus.EMPTY_DOCUMENT: "pdf_sniff_empty",
    SniffStatus.SNIFF_ERROR: "pdf_sniff_error",
    SniffStatus.DEPENDENCY_MISSING: "pdf_sniff_unavailable",
}


# --------------------------------------------------------------------------- #
# A. v2 PDF 判定 + pdf_sniff 证据块（A014-01 / 02 / 08）
# --------------------------------------------------------------------------- #


def test_v2_digital_pdf_uses_sniff_rule_and_emits_evidence(harness: _Harness) -> None:
    payload = _digital_pdf_bytes(chars=240)
    sha256, vault_dir = _pdf_in_vault(harness, payload)
    content = _fake_content(sha256=sha256, vault_path=vault_dir)

    decision = _route_content(
        harness,
        content=content,
        vault_object=_fake_vault_object(sha256=sha256, vault_path=vault_dir),
    )

    assert decision.route_type is RouteType.PDF_DIGITAL
    assert decision.decision == _EXPECTED_PDF_DECISION
    assert decision.rule_name == "pdf_sniff_digital"
    assert decision.future_parser_hint == _V1_PDF_BASE[3]

    evidence = decision.to_dict()["pdf_sniff"]
    assert evidence == decision.pdf_sniff.evidence()
    assert evidence["status"] == "TEXT_LAYER_PRESENT"
    assert evidence["from_cache"] is False
    assert evidence["sniffer_version"] == "pdf_sniff_v1"
    assert evidence["text_chars"] >= 100

    # 嗅探落缓存（非 dry-run）
    assert cache_path_for(harness.sniff_cache_root, sha256).is_file()
    # A014-10：original.bin 逐字节未变
    original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]
    assert original_bin.read_bytes() == payload


def test_v2_scanned_pdf_routes_scanned_with_control(harness: _Harness) -> None:
    payload = _scanned_pdf_bytes()
    sha256, vault_dir = _pdf_in_vault(harness, payload)
    content = _fake_content(sha256=sha256, vault_path=vault_dir)

    decision = _route_content(
        harness,
        content=content,
        vault_object=_fake_vault_object(sha256=sha256, vault_path=vault_dir),
    )

    assert decision.route_type is RouteType.PDF_SCANNED_OR_IMAGE
    assert decision.route_type is not RouteType.PDF_DIGITAL
    assert decision.rule_name == "pdf_sniff_scanned"
    assert decision.decision == _EXPECTED_PDF_DECISION
    assert decision.to_dict()["pdf_sniff"]["status"] == "TEXT_LAYER_ABSENT"

    # 反假绿对照：同一条判定路径换成 digital fixture 必须变成 PDF_DIGITAL
    other_payload = _digital_pdf_bytes(chars=240)
    other_sha, other_dir = _pdf_in_vault(harness, other_payload)
    control = _route_content(
        harness,
        content=_fake_content(sha256=other_sha, vault_path=other_dir),
        vault_object=_fake_vault_object(sha256=other_sha, vault_path=other_dir),
    )
    assert control.route_type is RouteType.PDF_DIGITAL
    assert control.rule_name == "pdf_sniff_digital"


def test_pdf_sniff_key_only_present_on_sniffed_pdf(harness: _Harness) -> None:
    """A014-08：非 PDF decision 不得携带 pdf_sniff 键。"""

    text_sha = "b" * 64
    text_dir = _empty_vault_dir(harness, text_sha)
    text_decision = _route_content(
        harness,
        content=_fake_content(
            sha256=text_sha, vault_path=text_dir, file_ext=".md", mime_type="text/markdown"
        ),
        vault_object=_fake_vault_object(sha256=text_sha, vault_path=text_dir),
    )

    expected_text_base = match_route_type(
        file_ext=".md", mime_type="text/markdown", fallback_ext=None
    )
    assert text_decision.route_type is expected_text_base[0]
    assert text_decision.rule_name == expected_text_base[2]
    assert "pdf_sniff" not in text_decision.to_dict()
    assert text_decision.pdf_sniff is None

    pdf_payload = _digital_pdf_bytes(chars=240)
    pdf_sha, pdf_dir = _pdf_in_vault(harness, pdf_payload)
    pdf_decision = _route_content(
        harness,
        content=_fake_content(sha256=pdf_sha, vault_path=pdf_dir),
        vault_object=_fake_vault_object(sha256=pdf_sha, vault_path=pdf_dir),
    )
    assert "pdf_sniff" in pdf_decision.to_dict()


# --------------------------------------------------------------------------- #
# B. v1 冻结路径：完全不嗅探、不读 vault、零磁盘写入（TC014-11 / A014-05）
# --------------------------------------------------------------------------- #


def test_v1_route_is_byte_frozen_and_never_touches_vault(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _scanned_pdf_bytes()
    sha256, vault_dir = _pdf_in_vault(harness, payload)
    content = _fake_content(sha256=sha256, vault_path=vault_dir)

    def _boom_sniff(*args: object, **kwargs: object) -> object:
        raise AssertionError("v1 冻结路径不得执行任何嗅探")

    def _boom_paths(*args: object, **kwargs: object) -> object:
        raise AssertionError("v1 冻结路径不得访问 vault 制品路径")

    monkeypatch.setattr(parser_router_module, "resolve_pdf_sniff", _boom_sniff)
    monkeypatch.setattr(parser_router_module, "build_vault_artifact_paths", _boom_paths)

    before = harness.snapshot()
    decision = _route_content(
        harness,
        content=content,
        vault_object=_fake_vault_object(sha256=sha256, vault_path=vault_dir),
        rules_version=V1_RULES_VERSION,
    )

    # 与 004 冻结结果逐字段一致（含 reason 文案）
    assert decision.route_type is RouteType.PDF_DIGITAL
    assert decision.decision == _V1_PDF_BASE[1]
    assert decision.rule_name == "ext_pdf_digital"
    assert decision.reason == _V1_PDF_BASE[4]
    assert decision.future_parser_hint == _V1_PDF_BASE[3]
    assert decision.pdf_sniff is None
    assert "pdf_sniff" not in decision.to_dict()
    assert harness.snapshot() == before, "v1 必须零磁盘写入（连缓存目录都不建）"


def test_v1_scanned_pdf_still_routes_digital(harness: _Harness) -> None:
    """v1 对照：同一张扫描件在 v1 下仍judged PDF_DIGITAL（证明上述断言非恒真）。"""

    payload = _scanned_pdf_bytes()
    sha256, vault_dir = _pdf_in_vault(harness, payload)

    v1_decision = _route_content(
        harness,
        content=_fake_content(sha256=sha256, vault_path=vault_dir),
        vault_object=_fake_vault_object(sha256=sha256, vault_path=vault_dir),
        rules_version=V1_RULES_VERSION,
    )
    v2_decision = _route_content(
        harness,
        content=_fake_content(sha256=sha256, vault_path=vault_dir),
        vault_object=_fake_vault_object(sha256=sha256, vault_path=vault_dir),
    )

    assert v1_decision.route_type is RouteType.PDF_DIGITAL
    assert v1_decision.pdf_sniff is None
    assert v2_decision.route_type is RouteType.PDF_SCANNED_OR_IMAGE
    assert v2_decision.pdf_sniff is not None


# --------------------------------------------------------------------------- #
# C. vault 内容缺失回退（TC014-13 / D-3）
# --------------------------------------------------------------------------- #


def test_missing_original_bin_falls_back_to_v1_rule(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    sha256 = "c" * 64
    vault_dir = _empty_vault_dir(harness, sha256)  # 目录存在但无 original.bin
    content = _fake_content(sha256=sha256, vault_path=vault_dir)

    before = harness.snapshot()
    with caplog.at_level(logging.WARNING, logger="app.services.parser_router"):
        decision = _route_content(
            harness,
            content=content,
            vault_object=_fake_vault_object(sha256=sha256, vault_path=vault_dir),
        )

    assert decision.route_type is RouteType.PDF_DIGITAL
    assert decision.rule_name == "ext_pdf_digital"
    assert decision.decision == _EXPECTED_PDF_DECISION
    assert REASON_SNIFF_SKIPPED in decision.reason
    assert decision.reason.startswith(_V1_PDF_BASE[4])
    assert decision.pdf_sniff is None
    assert "pdf_sniff" not in decision.to_dict()
    assert any(
        "PDF sniff skipped" in record.getMessage() for record in caplog.records
    ), "回退必须留 WARNING"
    assert harness.snapshot() == before, "回退路径不得写缓存"


def test_missing_vault_path_falls_back_to_v1_rule(harness: _Harness) -> None:
    """vault_path 完全缺失（DB 元数据为空）同样回退，且不抛异常。"""

    sha256 = "d" * 64

    decision = _route_content(
        harness,
        content=_fake_content(sha256=sha256, vault_path=None),
        vault_object=None,
    )

    assert decision.route_type is RouteType.PDF_DIGITAL
    assert decision.rule_name == "ext_pdf_digital"
    assert REASON_SNIFF_SKIPPED in decision.reason
    assert "vault_path is null in database metadata" in decision.reason
    assert decision.pdf_sniff is None
    assert not harness.sniff_cache_root.exists()


def test_skip_reason_is_visible_in_route_report(harness: _Harness) -> None:
    """[2'] 义务(b)：bin 不可达时 skip 理由必须落到报告里，消费者可读。

    否则「未嗅探」会被只读 route_type 的消费者误读成「已确认数字版」。
    """

    sha256 = "f" * 64
    vault_dir = _empty_vault_dir(harness, sha256)
    content = _fake_content(sha256=sha256, vault_path=vault_dir, content_uid="c1")
    _prepare_route_parsers(
        harness, [(content, _fake_vault_object(sha256=sha256, vault_path=vault_dir))]
    )

    result = harness.service.route_parsers()

    assert result.errors == []
    assert result.report_path is not None
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    entry = report["decisions"][0]

    assert entry["route_type"] == RouteType.PDF_DIGITAL.value
    assert REASON_SNIFF_SKIPPED in entry["reason"], "报告必须显式暴露『本次未嗅探』"
    assert "pdf_sniff" not in entry, "未嗅探时不得出现 pdf_sniff 证据块"
    assert report["summary"]["pdf_sniffed"] == 0


# --------------------------------------------------------------------------- #
# D. fail-closed：六状态里除 PRESENT 外一律不得判 digital（A014-02/03/04）
# --------------------------------------------------------------------------- #


def test_non_present_rule_table_is_closed_set() -> None:
    assert set(_NON_PRESENT_RULE) == set(SniffStatus) - {SniffStatus.TEXT_LAYER_PRESENT}


@pytest.mark.parametrize("status", sorted(_NON_PRESENT_RULE, key=lambda item: item.value))
def test_router_fail_closed_for_every_non_present_status(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, status: SniffStatus
) -> None:
    """底子是「有文本层的 PDF」，只要嗅探状态非 PRESENT 就必须落 scanned。"""

    payload = _digital_pdf_bytes(chars=240)
    sha256, vault_dir = _pdf_in_vault(harness, payload)
    content = _fake_content(sha256=sha256, vault_path=vault_dir)

    stub = PdfSniffResult(
        status=status, sniffer_version="pdf_sniff_v1", detail="stubbed-status"
    )

    def _stub_resolve(*args: object, **kwargs: object) -> PdfSniffResult:
        return stub

    monkeypatch.setattr(parser_router_module, "resolve_pdf_sniff", _stub_resolve)

    decision = _route_content(
        harness,
        content=content,
        vault_object=_fake_vault_object(sha256=sha256, vault_path=vault_dir),
    )

    assert decision.route_type is RouteType.PDF_SCANNED_OR_IMAGE
    assert decision.route_type is not RouteType.PDF_DIGITAL
    assert decision.decision == _EXPECTED_PDF_DECISION
    assert decision.rule_name == _NON_PRESENT_RULE[status]
    assert decision.to_dict()["pdf_sniff"]["status"] == status.value


# --------------------------------------------------------------------------- #
# E. route_parsers() 端到端（假 session + 假候选集），含报告 / dry-run / 缓存
# --------------------------------------------------------------------------- #


def _prepare_route_parsers(harness: _Harness, entries: list[tuple]) -> list[object]:
    """安装假 candidates 与假 session；返回按顺序消费的 scalar 结果队列。"""

    flat: list[object] = []
    for content, vault_object in entries:
        flat.extend([content, vault_object])
    harness.service.session_factory = _FakeSessionFactory(flat)  # type: ignore[assignment]
    harness.service._load_candidates = lambda **kwargs: [  # type: ignore[method-assign]
        content for content, _ in entries
    ]
    return flat


def _pdf_entry(harness: _Harness, payload: bytes, *, content_uid: str, vault_dir: Path | None = None):
    sha256, directory = _pdf_in_vault(harness, payload, vault_dir=vault_dir)
    return (
        _fake_content(sha256=sha256, vault_path=directory, content_uid=content_uid),
        _fake_vault_object(sha256=sha256, vault_path=directory),
        sha256,
    )


def test_route_parsers_batch_continues_past_broken_pdf(
    harness: _Harness, tmp_path: Path
) -> None:
    """TC014-05：一张完全损坏的 PDF 不得中断整批路由。"""

    digital_entry = _pdf_entry(harness, _digital_pdf_bytes(chars=240), content_uid="c1")
    broken_entry = _pdf_entry(
        harness, write_corrupted_pdf(tmp_path / "broken.pdf").read_bytes(), content_uid="c2"
    )
    scanned_entry = _pdf_entry(harness, _scanned_pdf_bytes(), content_uid="c3")

    text_sha = "e" * 64
    text_dir = _empty_vault_dir(harness, text_sha)
    text_entry = (
        _fake_content(
            sha256=text_sha,
            vault_path=text_dir,
            content_uid="c4",
            file_ext=".md",
            mime_type="text/markdown",
        ),
        _fake_vault_object(sha256=text_sha, vault_path=text_dir),
        text_sha,
    )

    entries = [digital_entry, broken_entry, scanned_entry, text_entry]
    _prepare_route_parsers(harness, [(item[0], item[1]) for item in entries])

    result = harness.service.route_parsers()

    assert result.errors == []
    assert result.candidates == 4
    assert result.routed == 4
    assert [decision.content_uid for decision in result.decisions] == ["c1", "c2", "c3", "c4"]

    by_uid = {decision.content_uid: decision for decision in result.decisions}
    assert by_uid["c1"].route_type is RouteType.PDF_DIGITAL
    assert by_uid["c2"].route_type is RouteType.PDF_SCANNED_OR_IMAGE
    assert by_uid["c2"].rule_name == "pdf_sniff_error"
    assert by_uid["c3"].route_type is RouteType.PDF_SCANNED_OR_IMAGE
    assert by_uid["c3"].rule_name == "pdf_sniff_scanned"
    assert "pdf_sniff" not in by_uid["c4"].to_dict()

    assert result.pdf_sniffed == 3
    assert result.pdf_digital == 1
    assert result.pdf_scanned == 2
    assert result.pdf_cache_hits == 0
    assert result.routing_rules_version == ROUTING_RULES_VERSION

    assert result.report_path is not None and result.report_path.is_file()
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["routing_rules_version"] == ROUTING_RULES_VERSION
    assert report["summary"]["pdf_sniffed"] == 3
    assert report["summary"]["pdf_cache_hits"] == 0
    assert len(report["decisions"]) == 4
    assert "pdf_sniff" not in report["decisions"][3]
    assert report["decisions"][2]["pdf_sniff"]["status"] == "TEXT_LAYER_ABSENT"


def test_route_parsers_second_run_hits_cache_and_is_field_identical(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC014-08：第二次 from_cache=true，且决策逐字段一致；缓存命中不得重算。"""

    payload = _digital_pdf_bytes(chars=240)
    content, vault_object, sha256 = _pdf_entry(harness, payload, content_uid="c1")

    _prepare_route_parsers(harness, [(content, vault_object)])
    first = harness.service.route_parsers()
    assert first.pdf_cache_hits == 0
    first_decision = first.decisions[0]
    assert first_decision.pdf_sniff.from_cache is False

    from app.core import pdf_sniffing as pdf_sniffing_module

    def _forbidden_sniff(*args: object, **kwargs: object) -> object:
        raise AssertionError("缓存命中时不得再执行 sniff_pdf")

    monkeypatch.setattr(pdf_sniffing_module, "sniff_pdf", _forbidden_sniff)

    _prepare_route_parsers(harness, [(content, vault_object)])
    second = harness.service.route_parsers()

    assert second.pdf_cache_hits == 1
    second_decision = second.decisions[0]
    assert second_decision.pdf_sniff.from_cache is True
    for field_name in ("route_type", "decision", "rule_name", "reason", "future_parser_hint"):
        assert getattr(second_decision, field_name) == getattr(first_decision, field_name)

    first_evidence = dict(first_decision.pdf_sniff.evidence())
    second_evidence = dict(second_decision.pdf_sniff.evidence())
    assert second_evidence.pop("from_cache") is True
    assert first_evidence.pop("from_cache") is False
    assert second_evidence == first_evidence

    assert second.report_path is not None
    report = json.loads(second.report_path.read_text(encoding="utf-8"))
    assert report["decisions"][0]["pdf_sniff"]["from_cache"] is True
    assert report["summary"]["pdf_cache_hits"] == 1
    assert cache_path_for(harness.sniff_cache_root, sha256).is_file()


def test_poisoned_cache_never_reaches_report(harness: _Harness) -> None:
    """TC014-09 / A014-07：篡改后的缓存内容绝不出现在报告 pdf_sniff 块中。"""

    payload = _scanned_pdf_bytes()
    content, vault_object, sha256 = _pdf_entry(harness, payload, content_uid="c1")

    _prepare_route_parsers(harness, [(content, vault_object)])
    harness.service.route_parsers()

    cache_file = cache_path_for(harness.sniff_cache_root, sha256)
    poisoned = json.loads(cache_file.read_text(encoding="utf-8"))
    poisoned["sha256"] = "0" * 64
    poisoned["status"] = "TEXT_LAYER_PRESENT"
    poisoned["text_chars"] = 999_999
    poisoned["avg_chars_per_page"] = 999_999.0
    cache_file.write_text(json.dumps(poisoned), encoding="utf-8")

    _prepare_route_parsers(harness, [(content, vault_object)])
    result = harness.service.route_parsers()

    assert result.errors == []
    assert result.report_path is not None
    report_text = result.report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    sniff_block = report["decisions"][0]["pdf_sniff"]

    assert sniff_block["status"] == "TEXT_LAYER_ABSENT"
    assert sniff_block["status"] != "TEXT_LAYER_PRESENT"
    assert sniff_block["text_chars"] != 999_999
    assert sniff_block["from_cache"] is False
    assert "999999" not in report_text
    assert report["decisions"][0]["route_type"] == RouteType.PDF_SCANNED_OR_IMAGE.value


def test_cache_dir_unwritable_still_routes(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """TC014-10：缓存目录不可写只 WARNING，路由结果照旧。"""

    content, vault_object, _sha = _pdf_entry(
        harness, _digital_pdf_bytes(chars=240), content_uid="c1"
    )
    harness.sniff_cache_root.parent.mkdir(parents=True, exist_ok=True)
    harness.sniff_cache_root.write_text("blocked", encoding="utf-8")

    _prepare_route_parsers(harness, [(content, vault_object)])
    with caplog.at_level(logging.WARNING, logger="app.core.pdf_sniffing"):
        result = harness.service.route_parsers()

    assert result.errors == []
    assert result.decisions[0].route_type is RouteType.PDF_DIGITAL
    assert result.decisions[0].pdf_sniff.from_cache is False
    assert any(
        "pdf sniff cache" in record.getMessage() for record in caplog.records
    ), "缓存写失败必须留 WARNING"


def test_dry_run_zero_disk_writes(harness: _Harness) -> None:
    """TC014-12：dry-run 不写缓存、不写报告，但判定与计数照旧产出。"""

    payload = _digital_pdf_bytes(chars=240)
    content, vault_object, sha256 = _pdf_entry(harness, payload, content_uid="c1")
    before = harness.snapshot()

    _prepare_route_parsers(harness, [(content, vault_object)])
    result = harness.service.route_parsers(dry_run=True)

    assert result.dry_run is True
    assert result.report_path is None
    assert not harness.reports_root.exists()
    assert not cache_path_for(harness.sniff_cache_root, sha256).exists()
    assert harness.snapshot() == before

    assert result.routed == 1
    assert result.pdf_sniffed == 1
    assert result.pdf_digital == 1
    assert result.decisions[0].pdf_sniff.status is SniffStatus.TEXT_LAYER_PRESENT

    # 对照：同一输入在非 dry-run 下必须落缓存 + 报告
    _prepare_route_parsers(harness, [(content, vault_object)])
    live = harness.service.route_parsers()
    assert live.report_path is not None and live.report_path.is_file()
    assert cache_path_for(harness.sniff_cache_root, sha256).is_file()


def test_route_parsers_never_mutates_vault_artifacts(harness: _Harness) -> None:
    """TC014-15：original.bin 与 vault 目录内容在处理前后不变（缓存不落进 vault）。"""

    payload = _digital_pdf_bytes(chars=240)
    content, vault_object, sha256 = _pdf_entry(harness, payload, content_uid="c1")
    vault_dir = build_vault_dir(harness.raw_vault_root, sha256)
    original_bin = build_vault_artifact_paths(vault_dir)["original_bin"]

    before_hash = hashlib.sha256(original_bin.read_bytes()).hexdigest()
    before_listing = sorted(item.name for item in vault_dir.iterdir())

    _prepare_route_parsers(harness, [(content, vault_object)])
    result = harness.service.route_parsers()

    assert result.errors == []
    assert hashlib.sha256(original_bin.read_bytes()).hexdigest() == before_hash
    assert sorted(item.name for item in vault_dir.iterdir()) == before_listing
    assert original_bin.read_bytes() == payload


def test_chinese_vault_path_routes_without_mangling(harness: _Harness) -> None:
    """TC014-16：中文目录 + 中文文件名路径；缓存名仍为纯 ASCII sha256。"""

    payload = _digital_pdf_bytes(chars=240)
    sha256 = _sha256_bytes(payload)
    chinese_dir = harness.raw_vault_root / "原始仓库_014" / sha256[:8] / "子目录"
    content, vault_object, _sha = _pdf_entry(
        harness, payload, content_uid="c1", vault_dir=chinese_dir
    )

    _prepare_route_parsers(harness, [(content, vault_object)])
    result = harness.service.route_parsers()

    assert result.errors == []
    assert result.decisions[0].route_type is RouteType.PDF_DIGITAL
    assert result.decisions[0].vault_path == chinese_dir.as_posix()

    cache_file = cache_path_for(harness.sniff_cache_root, sha256)
    assert cache_file.name == f"{sha256}.json"
    assert cache_file.is_file()

    assert result.report_path is not None
    report_text = result.report_path.read_text(encoding="utf-8")
    assert "原始仓库_014" in report_text, "报告必须 ensure_ascii=False 保留中文"
    report = json.loads(report_text)
    assert report["decisions"][0]["vault_path"] == chinese_dir.as_posix()


# --------------------------------------------------------------------------- #
# F. 服务层守卫：只读强制 + 非法 rules_version
# --------------------------------------------------------------------------- #


def test_service_refuses_writable_original_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, workspace_root)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "original_files_readonly: true", "original_files_readonly: false"
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="original_files_readonly must be true"):
        ParserRouterService(load_config(config_path))


@pytest.mark.parametrize("bad_version", ["999_made_up", "", "v2"])
def test_unsupported_rules_version_rejected(harness: _Harness, bad_version: str) -> None:
    with pytest.raises(ValueError, match="Unsupported routing rules version"):
        harness.service.route_parsers(rules_version=bad_version)


# --------------------------------------------------------------------------- #
# G. CLI 三新选项（T007）
#
# 本环境 Typer/Click 版本不兼容导致 `--help` 渲染必然抛
# `Parameter.make_metavar() missing 1 required positional argument: 'ctx'`
# （改动前基线即失败，不在白名单内不得修）。因此这里改为直接检查 Typer 注册的
# 选项声明与命令回调签名，并直接调用命令函数本体验证参数透传，不依赖 --help。
# --------------------------------------------------------------------------- #


def _route_parsers_command_info():
    from app.cli.main import app

    for candidate in app.registered_commands:
        if candidate.name == "route-parsers":
            return candidate
        callback = candidate.callback
        if callback is not None and callback.__name__ == "route_parsers":
            return candidate
    raise AssertionError("route-parsers 命令未在 Typer app 中注册")


def _option_declaration(cli_flag: str):
    """按 CLI 标志找 Click Option。

    兼容两种写法：单标志 `--dry-run`，以及布尔双向 `--sniff-cache/--no-sniff-cache`
    （后者 Click 会把两侧拆进 opts / secondary_opts）。
    """

    info = _route_parsers_command_info()
    for parameter in inspect.signature(info.callback).parameters.values():
        option = parameter.default
        declarations: list[str] = []
        for attribute in ("param_decls", "opts", "secondary_opts"):
            declarations.extend(getattr(option, attribute, None) or [])
        for declaration in list(declarations):
            if "/" in declaration:
                declarations.extend(declaration.split("/"))
        if cli_flag in declarations:
            return option
    return None


def test_cli_route_parsers_exposes_v2_options() -> None:
    from app.core.parser_routing import ROUTING_RULES_VERSIONS

    info = _route_parsers_command_info()
    parameter_names = set(inspect.signature(info.callback).parameters)
    assert {"rules_version", "sniff_cache", "dry_run"} <= parameter_names

    version_option = _option_declaration("--routing-rules-version")
    assert version_option is not None, "缺少 --routing-rules-version"
    assert version_option.default == ROUTING_RULES_VERSION
    assert version_option.default in ROUTING_RULES_VERSIONS

    dry_run_option = _option_declaration("--dry-run")
    assert dry_run_option is not None, "缺少 --dry-run"
    assert dry_run_option.default is False

    sniff_cache_option = _option_declaration("--sniff-cache")
    assert sniff_cache_option is not None, "缺少 --sniff-cache/--no-sniff-cache"
    assert sniff_cache_option.default is True
    declared_flags: set[str] = set()
    for attribute in ("param_decls", "opts", "secondary_opts"):
        for declaration in getattr(sniff_cache_option, attribute, None) or []:
            declared_flags.update(declaration.split("/"))
    assert "--sniff-cache" in declared_flags
    assert "--no-sniff-cache" in declared_flags, "缺少 --no-sniff-cache 反向开关"


def test_cli_route_parsers_forwards_v2_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import app.cli.main as cli_main
    from app.services.parser_router import ParserRouteDecision, ParserRouteResult

    captured: dict[str, object] = {}

    class _FakeService:
        def __init__(self, config: object) -> None:
            captured["config"] = config

        def route_parsers(self, **kwargs: object) -> ParserRouteResult:
            captured["call"] = kwargs
            return ParserRouteResult(
                routing_rules_version=str(kwargs.get("rules_version")),
                pdf_sniffed=1,
                pdf_digital=1,
                pdf_scanned=0,
                pdf_cache_hits=1,
                dry_run=bool(kwargs.get("dry_run")),
                decisions=[
                    ParserRouteDecision(
                        content_uid="c1",
                        sha256="a" * 64,
                        file_ext=".pdf",
                        mime_type="application/pdf",
                        vault_path="/vault/c1",
                        route_type=RouteType.PDF_DIGITAL,
                        decision="ROUTE",
                        rule_name="pdf_sniff_digital",
                        reason="PDF text layer present",
                        future_parser_hint="MINERU_FAMILY",
                        parse_status=None,
                    )
                ],
            )

    monkeypatch.setattr(cli_main, "ParserRouterService", _FakeService)
    monkeypatch.setattr(cli_main, "load_config", lambda path: object())

    cli_main.route_parsers(
        config=tmp_path / "app.yaml",
        limit=5,
        sha256=None,
        content_uid=None,
        rules_version="004_mvp_v1",
        sniff_cache=False,
        dry_run=True,
    )

    call = captured["call"]
    assert call["rules_version"] == "004_mvp_v1"
    assert call["use_sniff_cache"] is False
    assert call["dry_run"] is True
    assert call["limit"] == 5

    printed = capsys.readouterr().out
    assert "PDF sniffed: 1" in printed, "dry-run 也必须打印嗅探计数摘要"
    assert "PDF cache hits: 1" in printed
    assert "Routing rules version: 004_mvp_v1" in printed
    assert "PDF_DIGITAL" in printed and "pdf_sniff_digital" in printed


def test_cli_route_parsers_rejects_unknown_rules_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import typer

    import app.cli.main as cli_main

    def _must_not_build_service(config: object) -> object:
        raise AssertionError("非法 rules_version 必须在构造服务/连库之前就退出")

    monkeypatch.setattr(cli_main, "load_config", lambda path: object())
    monkeypatch.setattr(cli_main, "ParserRouterService", _must_not_build_service)

    with pytest.raises(typer.Exit) as excinfo:
        cli_main.route_parsers(
            config=tmp_path / "app.yaml",
            limit=1,
            sha256=None,
            content_uid=None,
            rules_version="004_mvp_v9",
            sniff_cache=True,
            dry_run=False,
        )

    assert excinfo.value.exit_code == 1
    assert "ERROR: --routing-rules-version" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# H. 007 `_build_plan` 嗅探注入（T006 / plan §7.4）
#
# 007 与 router 复用同一条嗅探管线，故「router 判 scanned 的内容，007 不得按
# digital 处理」。同样用假 session，无 MySQL 也能真实跑 _build_plan。
# --------------------------------------------------------------------------- #


def _mineru_plan_for(
    harness: _Harness,
    payload: bytes,
    *,
    content_uid: str = "c1",
    dry_run: bool = False,
    place_bin: bool = True,
):
    from app.services.mineru_pdf_parser import MineruPdfParserService

    sha256 = _sha256_bytes(payload)
    vault_dir = build_vault_dir(harness.raw_vault_root, sha256)
    if place_bin:
        _place_vault_pdf(vault_dir, payload)
    else:
        vault_dir.mkdir(parents=True, exist_ok=True)

    content = _fake_content(sha256=sha256, vault_path=vault_dir, content_uid=content_uid)
    vault_object = _fake_vault_object(sha256=sha256, vault_path=vault_dir)

    service = MineruPdfParserService(harness.config)
    service.session_factory = _FakeSessionFactory([content, vault_object])  # type: ignore[assignment]
    plan = service._build_plan(content=content, force=False, dry_run=dry_run)
    return plan, sha256, vault_dir


def test_mineru_in_scope_route_types_unchanged() -> None:
    """Q8：`IN_SCOPE_ROUTE_TYPES` 不得因 014 而改动。"""

    from app.services.mineru_pdf_parser import IN_SCOPE_ROUTE_TYPES

    assert IN_SCOPE_ROUTE_TYPES == frozenset(
        {RouteType.PDF_DIGITAL, RouteType.PDF_SCANNED_OR_IMAGE}
    )


def test_mineru_build_plan_scanned_is_never_digital(harness: _Harness) -> None:
    """T006 验收：scanned fixture 经 `_build_plan` 必须得 scanned，否定 digital。"""

    from app.services.mineru_pdf_parser import DECISION_PARSED

    plan, sha256, _vault_dir = _mineru_plan_for(harness, _scanned_pdf_bytes())

    assert plan.route_type == RouteType.PDF_SCANNED_OR_IMAGE.value
    assert plan.route_type != RouteType.PDF_DIGITAL.value
    assert plan.rule_name == "pdf_sniff_scanned"
    # 007 的 in-scope 集合含 scanned，故交给 MinerU（OCR）解析
    assert plan.decision == DECISION_PARSED
    # 与 router 同一条管线：缓存已落盘
    assert cache_path_for(harness.sniff_cache_root, sha256).is_file()


def test_mineru_build_plan_digital_control(harness: _Harness) -> None:
    """反假绿对照：同一条 `_build_plan` 路径上换成 digital fixture 必须得 digital。"""

    plan, _sha, _vault_dir = _mineru_plan_for(harness, _digital_pdf_bytes(chars=240))

    assert plan.route_type == RouteType.PDF_DIGITAL.value
    assert plan.rule_name == "pdf_sniff_digital"
    assert plan.route_type != RouteType.PDF_SCANNED_OR_IMAGE.value


def test_mineru_build_plan_reuses_router_sniff_cache(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """router 先跑一遍落缓存后，007 必须复用缓存而非重算（同管线、零重复开销）。"""

    from app.core import pdf_sniffing as pdf_sniffing_module

    payload = _scanned_pdf_bytes()
    sha256 = _sha256_bytes(payload)
    vault_dir = build_vault_dir(harness.raw_vault_root, sha256)
    _place_vault_pdf(vault_dir, payload)
    content = _fake_content(sha256=sha256, vault_path=vault_dir, content_uid="c1")
    _prepare_route_parsers(
        harness, [(content, _fake_vault_object(sha256=sha256, vault_path=vault_dir))]
    )
    assert harness.service.route_parsers().errors == []

    def _forbidden_sniff(*args: object, **kwargs: object) -> object:
        raise AssertionError("007 必须复用 router 已落盘的嗅探缓存")

    monkeypatch.setattr(pdf_sniffing_module, "sniff_pdf", _forbidden_sniff)

    plan, _sha, _dir = _mineru_plan_for(harness, payload)

    assert plan.route_type == RouteType.PDF_SCANNED_OR_IMAGE.value
    assert plan.rule_name == "pdf_sniff_scanned"


def test_mineru_build_plan_missing_bin_does_not_sniff(harness: _Harness) -> None:
    """D-3：bin 不可达时 007 不嗅探，沿用 004 判定，且不写缓存。

    注意（实测发现，见 DEV_SELF_CHECK R-8）：此时 `skip_reason` 会被更具体的
    `missing_original_bin`（OUTPUT_CONTRACT_VIOLATION 分支）覆盖，因此 007 侧
    看不到 `sniff skipped` 文案——信息由 `missing_original_bin` 承载。
    """

    plan, sha256, _dir = _mineru_plan_for(harness, _scanned_pdf_bytes(), place_bin=False)

    assert plan.route_type == RouteType.PDF_DIGITAL.value
    assert plan.rule_name == "ext_pdf_digital"
    assert plan.skip_reason == "missing_original_bin"
    assert not harness.sniff_cache_root.exists()


def test_mineru_build_plan_dry_run_writes_no_cache(harness: _Harness) -> None:
    plan, sha256, _dir = _mineru_plan_for(
        harness, _digital_pdf_bytes(chars=240), dry_run=True
    )

    assert plan.route_type == RouteType.PDF_DIGITAL.value
    assert not cache_path_for(harness.sniff_cache_root, sha256).exists()
