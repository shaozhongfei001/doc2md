"""014 PDF 内容嗅探单测（纯函数 + 文件缓存）。

覆盖 TC014-01 ~ TC014-07、TC014-10 的纯函数部分，TC014-03 的「阈值真实接线」，
以及 A014-10 的「只读」约束。所有 PDF fixture 现场生成，仓库不提交二进制。

反假绿约定：
- 每个「必须产生 X」的断言都配有对照路径（换 fixture 后必须不再产生 X）；
- FROZEN 阈值（100 / 20 / 20）既有代码常量断言、又有 `config/pdf_sniffing.yaml`
  的逐字段读取断言，任何阈值漂移（含变异 100→999）必然变红；
- 缓存投毒用例把脏数据写成「看起来可信的 PRESENT 大数」，一旦被采纳必然暴露。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pypdf import PdfReader, PdfWriter

from app.core import pdf_sniffing
from app.core.parser_routing import (
    DECISION_ROUTE,
    PDF_SNIFF_RULE_NAMES,
    RouteType,
    route_pdf_with_sniff,
)
from app.core.pdf_sniffing import (
    CACHE_TYPE,
    DEFAULT_DIGITAL_MIN_AVG_CHARS_PER_PAGE,
    DEFAULT_DIGITAL_MIN_TOTAL_CHARS,
    DEFAULT_PAGES_MAX,
    SNIFFER_VERSION,
    PdfSniffConfig,
    PdfSniffResult,
    SniffStatus,
    cache_path_for,
    load_sniff_cache,
    load_sniff_config,
    resolve_pdf_sniff,
    save_sniff_cache,
    sniff_pdf,
)

FROZEN_TOTAL_CHARS = 100
FROZEN_AVG_CHARS = 20
FROZEN_PAGES_MAX = 20


# --------------------------------------------------------------------------- #
# fixture 生成：未压缩、xref 合法的极简 PDF（Helvetica / WinAnsiEncoding）
# --------------------------------------------------------------------------- #


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream_for(text: str) -> bytes:
    if not text:
        return b""
    return f"BT /F1 12 Tf 72 720 Td ({_escape_pdf_text(text)}) Tj ET".encode("latin-1")


def build_pdf_bytes(*, page_texts: list[str]) -> bytes:
    """构造合法 PDF 字节流。`page_texts` 为空即 0 页；元素为空串即无文本层页。"""

    page_count = len(page_texts)
    page_ids = [4 + 2 * index for index in range(page_count)]
    content_ids = [5 + 2 * index for index in range(page_count)]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)

    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("latin-1"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    ]
    for page_id, content_id, text in zip(page_ids, content_ids, page_texts):
        bodies.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("latin-1")
        )
        stream = _content_stream_for(text)
        bodies.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for object_id, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{object_id} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_offset = len(out)
    size = len(bodies) + 1
    out += f"xref\n0 {size}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def _write_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def write_digital_pdf(path: Path, *, chars: int = 240) -> Path:
    """单页含文本层 PDF（无空格，抽取字符数即 `chars`）。"""

    return _write_bytes(path, build_pdf_bytes(page_texts=["A" * chars]))


def write_multi_page_pdf(path: Path, *, pages: int, chars_per_page: int) -> Path:
    return _write_bytes(
        path, build_pdf_bytes(page_texts=["A" * chars_per_page for _ in range(pages)])
    )


def write_scanned_pdf(path: Path, *, pages: int = 1) -> Path:
    """无文本层的空白页 PDF（模拟扫描件）。"""

    return _write_bytes(path, build_pdf_bytes(page_texts=["" for _ in range(pages)]))


def write_empty_pdf(path: Path) -> Path:
    """0 页 PDF。"""

    return _write_bytes(path, build_pdf_bytes(page_texts=[]))


def write_corrupted_pdf(path: Path) -> Path:
    """截断 / 非法字节的 .pdf（声明是 PDF，但结构不可解析）。"""

    return _write_bytes(
        path,
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\ntrailer\n",
    )


def write_encrypted_pdf(path: Path, *, password: str = "pw", chars: int = 240) -> Path:
    plain = path.with_name(f"{path.name}.plain")
    plain.write_bytes(build_pdf_bytes(page_texts=["A" * chars]))
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(str(plain)))
    writer.encrypt(password)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        writer.write(handle)
    plain.unlink()
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# FROZEN 阈值接线（Owner O-5）+ 配置读取降级
# --------------------------------------------------------------------------- #


def test_frozen_thresholds_wired_to_code_and_yaml() -> None:
    """TC014-03 接线断言：代码常量与 yaml 都必须等于 FROZEN 的 100 / 20 / 20。

    本用例是「阈值漂移哨兵」——任何把 100 改成别的值的变异都会在此变红。
    """

    assert DEFAULT_DIGITAL_MIN_TOTAL_CHARS == FROZEN_TOTAL_CHARS
    assert DEFAULT_DIGITAL_MIN_AVG_CHARS_PER_PAGE == FROZEN_AVG_CHARS
    assert DEFAULT_PAGES_MAX == FROZEN_PAGES_MAX
    assert SNIFFER_VERSION == "pdf_sniff_v1"

    config_path = pdf_sniffing.SNIFF_CONFIG_PATH
    assert config_path.is_file(), "config/pdf_sniffing.yaml 必须存在（FROZEN 阈值来源）"

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    section = raw["pdf_sniffing"]
    assert section["sniffer_version"] == SNIFFER_VERSION
    assert section["pages_max"] == FROZEN_PAGES_MAX
    assert section["digital_min_total_chars"] == FROZEN_TOTAL_CHARS
    assert section["digital_min_avg_chars_per_page"] == FROZEN_AVG_CHARS
    assert section["cache_enabled"] is True

    loaded = load_sniff_config()
    assert loaded == load_sniff_config(config_path)
    assert loaded.pages_max == FROZEN_PAGES_MAX
    assert loaded.digital_min_total_chars == FROZEN_TOTAL_CHARS
    assert loaded.digital_min_avg_chars_per_page == FROZEN_AVG_CHARS


def test_sniff_config_falls_back_to_defaults(tmp_path: Path) -> None:
    assert load_sniff_config(tmp_path / "missing.yaml") == PdfSniffConfig()

    broken = tmp_path / "broken.yaml"
    broken.write_text("pdf_sniffing: [1, 2\n", encoding="utf-8")
    assert load_sniff_config(broken) == PdfSniffConfig()

    wrong_section = tmp_path / "other.yaml"
    wrong_section.write_text("something_else:\n  pages_max: 3\n", encoding="utf-8")
    assert load_sniff_config(wrong_section) == PdfSniffConfig()

    bad_values = tmp_path / "bad.yaml"
    bad_values.write_text('pdf_sniffing:\n  pages_max: "abc"\n', encoding="utf-8")
    assert load_sniff_config(bad_values) == PdfSniffConfig()


# --------------------------------------------------------------------------- #
# TC014-01 / 02 / 03：文本层判定与阈值边界
# --------------------------------------------------------------------------- #


def test_sniff_digital_pdf_text_layer_present(tmp_path: Path) -> None:
    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)

    result = sniff_pdf(pdf, config=PdfSniffConfig())

    assert result.status is SniffStatus.TEXT_LAYER_PRESENT
    assert result.text_chars >= FROZEN_TOTAL_CHARS
    assert result.pages_total == 1
    assert result.pages_sampled == 1
    assert result.avg_chars_per_page >= FROZEN_AVG_CHARS
    assert result.from_cache is False
    assert result.sniffer_version == SNIFFER_VERSION

    evidence = result.evidence()
    assert evidence["status"] == "TEXT_LAYER_PRESENT"
    assert set(evidence) == {
        "status",
        "text_chars",
        "pages_total",
        "pages_sampled",
        "avg_chars_per_page",
        "from_cache",
        "sniffer_version",
    }


def test_sniff_scanned_pdf_text_layer_absent(tmp_path: Path) -> None:
    scanned = write_scanned_pdf(tmp_path / "scanned.pdf")

    absent = sniff_pdf(scanned, config=PdfSniffConfig())

    assert absent.status is SniffStatus.TEXT_LAYER_ABSENT
    assert absent.status is not SniffStatus.TEXT_LAYER_PRESENT
    assert absent.text_chars < FROZEN_TOTAL_CHARS

    # 反假绿对照：同一断言路径换 digital fixture 必须得到 PRESENT（证明断言非恒真）
    digital = write_digital_pdf(tmp_path / "digital.pdf", chars=240)
    assert sniff_pdf(digital, config=PdfSniffConfig()).status is SniffStatus.TEXT_LAYER_PRESENT


def test_sniff_threshold_boundary_and_config_wiring(tmp_path: Path) -> None:
    """TC014-03：页均阈值上下各一档；阈值改自配置后结果必须翻转。"""

    upper = write_multi_page_pdf(tmp_path / "upper.pdf", pages=10, chars_per_page=20)
    lower = write_multi_page_pdf(tmp_path / "lower.pdf", pages=10, chars_per_page=19)
    config = PdfSniffConfig()

    upper_result = sniff_pdf(upper, config=config)
    lower_result = sniff_pdf(lower, config=config)

    assert upper_result.pages_sampled == 10
    assert lower_result.pages_sampled == 10
    # 两者总字符数都在下限之上，只有「页均」这一条区分它们
    assert lower_result.text_chars >= FROZEN_TOTAL_CHARS
    assert upper_result.avg_chars_per_page >= FROZEN_AVG_CHARS
    assert lower_result.avg_chars_per_page < FROZEN_AVG_CHARS
    assert upper_result.status is SniffStatus.TEXT_LAYER_PRESENT
    assert lower_result.status is SniffStatus.TEXT_LAYER_ABSENT

    # 阈值真实接线：页均下限下调 → 同一 fixture 翻转
    relaxed = PdfSniffConfig(digital_min_avg_chars_per_page=10)
    assert sniff_pdf(lower, config=relaxed).status is SniffStatus.TEXT_LAYER_PRESENT

    # 阈值真实接线：总字符下限上调 → 上档 fixture 也翻转
    strict = PdfSniffConfig(digital_min_total_chars=500)
    assert sniff_pdf(upper, config=strict).status is SniffStatus.TEXT_LAYER_ABSENT

    # pages_max 真实接线：采样页数受配置约束
    assert sniff_pdf(lower, config=PdfSniffConfig(pages_max=3)).pages_sampled == 3

    # yaml → 配置对象 → 判定 的端到端接线（不改动 FROZEN 的仓库内 yaml）
    custom_yaml = tmp_path / "custom.yaml"
    custom_yaml.write_text(
        "pdf_sniffing:\n"
        "  sniffer_version: pdf_sniff_v1\n"
        "  pages_max: 20\n"
        "  digital_min_total_chars: 100\n"
        "  digital_min_avg_chars_per_page: 10\n"
        "  cache_enabled: false\n",
        encoding="utf-8",
    )
    loaded = load_sniff_config(custom_yaml)
    assert loaded.digital_min_avg_chars_per_page == 10
    assert loaded.cache_enabled is False
    assert sniff_pdf(lower, config=loaded).status is SniffStatus.TEXT_LAYER_PRESENT


# --------------------------------------------------------------------------- #
# TC014-04 ~ TC014-07：fail-closed（任何不确定都不得返回 TEXT_LAYER_PRESENT）
# --------------------------------------------------------------------------- #


def test_sniff_encrypted_pdf_fail_closed(tmp_path: Path, monkeypatch) -> None:
    pdf = write_encrypted_pdf(tmp_path / "encrypted.pdf")
    assert PdfReader(str(pdf)).is_encrypted is True, "fixture 必须真的是加密 PDF"

    def _forbidden_decrypt(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("sniff 绝不允许尝试解密/猜测口令")

    monkeypatch.setattr(PdfReader, "decrypt", _forbidden_decrypt)

    result = sniff_pdf(pdf, config=PdfSniffConfig())

    assert result.status is SniffStatus.ENCRYPTED
    assert result.status is not SniffStatus.TEXT_LAYER_PRESENT
    assert result.text_chars == 0
    assert result.pages_sampled == 0


def test_sniff_corrupted_pdf_fail_closed(tmp_path: Path) -> None:
    pdf = write_corrupted_pdf(tmp_path / "broken.pdf")

    result = sniff_pdf(pdf, config=PdfSniffConfig())

    assert result.status is SniffStatus.SNIFF_ERROR
    assert result.status is not SniffStatus.TEXT_LAYER_PRESENT
    assert result.detail, "detail 应记录异常类型，便于定位损坏样本"


def test_sniff_empty_pdf_fail_closed(tmp_path: Path) -> None:
    pdf = write_empty_pdf(tmp_path / "empty.pdf")

    result = sniff_pdf(pdf, config=PdfSniffConfig())

    assert result.status is SniffStatus.EMPTY_DOCUMENT
    assert result.pages_total == 0
    assert result.pages_sampled == 0
    assert result.status is not SniffStatus.TEXT_LAYER_PRESENT


def test_sniff_pypdf_missing_fail_closed(tmp_path: Path, monkeypatch) -> None:
    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)

    def _raise_import_error():
        raise ImportError("No module named 'pypdf'")

    monkeypatch.setattr(pdf_sniffing, "_load_pdf_reader", _raise_import_error)

    result = sniff_pdf(pdf, config=PdfSniffConfig())

    assert result.status is SniffStatus.DEPENDENCY_MISSING
    assert result.status is not SniffStatus.TEXT_LAYER_PRESENT
    assert result.text_chars == 0

    # 对照：注入解除后同一 fixture 必须恢复为 PRESENT（证明上面不是恒真）
    monkeypatch.undo()
    assert sniff_pdf(pdf, config=PdfSniffConfig()).status is SniffStatus.TEXT_LAYER_PRESENT


def test_sniff_exception_never_propagates(tmp_path: Path, monkeypatch) -> None:
    """嗅探自身异常必须转成 SNIFF_ERROR，不得冒泡到调用方。"""

    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)

    def _broken_reader(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("reader exploded")

    monkeypatch.setattr(pdf_sniffing, "_load_pdf_reader", lambda: _broken_reader)

    result = sniff_pdf(pdf, config=PdfSniffConfig())

    assert result.status is SniffStatus.SNIFF_ERROR
    assert result.detail == "RuntimeError"


def test_sniff_is_read_only(tmp_path: Path) -> None:
    """A014-10：嗅探不得改动 original.bin，也不得在旁写任何文件。"""

    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)
    before_stat = pdf.stat()
    before_hash = _sha256(pdf)
    before_listing = sorted(item.name for item in tmp_path.iterdir())

    sniff_pdf(pdf, config=PdfSniffConfig())

    after_stat = pdf.stat()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert _sha256(pdf) == before_hash
    assert sorted(item.name for item in tmp_path.iterdir()) == before_listing


# --------------------------------------------------------------------------- #
# 缓存：命中幂等 / 开关 / 投毒 / 写失败降级
# --------------------------------------------------------------------------- #


def test_sniff_cache_hit_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)
    sha256 = _sha256(pdf)
    cache_root = tmp_path / "sniff_cache"
    config = PdfSniffConfig()

    first = resolve_pdf_sniff(pdf, sha256, config=config, cache_root=cache_root)
    assert first.from_cache is False
    cache_file = cache_path_for(cache_root, sha256)
    assert cache_file.is_file()
    assert cache_file.name == f"{sha256}.json"

    calls: list[str] = []
    real_sniff = pdf_sniffing.sniff_pdf

    def _counting_sniff(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("sniff_pdf")
        return real_sniff(*args, **kwargs)

    monkeypatch.setattr(pdf_sniffing, "sniff_pdf", _counting_sniff)

    second = resolve_pdf_sniff(pdf, sha256, config=config, cache_root=cache_root)

    assert second.from_cache is True
    assert calls == [], "命中缓存时绝不重算（A014-06 否定断言）"
    for field_name in (
        "status",
        "text_chars",
        "pages_total",
        "pages_sampled",
        "avg_chars_per_page",
        "sniffer_version",
    ):
        assert getattr(second, field_name) == getattr(first, field_name)


def test_sniff_cache_switches_respected(tmp_path: Path) -> None:
    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)
    sha256 = _sha256(pdf)
    cache_root = tmp_path / "sniff_cache"
    config = PdfSniffConfig()
    cache_file = cache_path_for(cache_root, sha256)

    # write_cache=False：不落缓存
    no_write = resolve_pdf_sniff(
        pdf, sha256, config=config, cache_root=cache_root, write_cache=False
    )
    assert no_write.from_cache is False
    assert not cache_file.exists()

    # use_cache=False：即便已有缓存也必须重算
    save_sniff_cache(cache_root, sha256, no_write)
    assert load_sniff_cache(cache_root, sha256, config.sniffer_version) is not None
    recomputed = resolve_pdf_sniff(
        pdf, sha256, config=config, cache_root=cache_root, use_cache=False
    )
    assert recomputed.from_cache is False

    # cache_enabled=False：既不读也不写
    cache_file.unlink()
    disabled = resolve_pdf_sniff(
        pdf,
        sha256,
        config=PdfSniffConfig(cache_enabled=False),
        cache_root=cache_root,
    )
    assert disabled.from_cache is False
    assert not cache_file.exists()

    # cache_root=None：无缓存根时直接嗅探
    assert (
        resolve_pdf_sniff(pdf, sha256, config=config, cache_root=None).from_cache is False
    )


@pytest.mark.parametrize(
    "mutation",
    ["sha256", "sniffer_version", "cache_type", "invalid_json"],
)
def test_sniff_cache_poisoning_rejected(tmp_path: Path, mutation: str) -> None:
    """TC014-09 / A014-07：三重校验任一不符即弃用重算，脏数据绝不进入结果。"""

    pdf = write_scanned_pdf(tmp_path / "scanned.pdf")
    sha256 = _sha256(pdf)
    cache_root = tmp_path / "sniff_cache"
    config = PdfSniffConfig()

    truth = sniff_pdf(pdf, config=config)
    assert truth.status is SniffStatus.TEXT_LAYER_ABSENT

    save_sniff_cache(cache_root, sha256, truth)
    cache_file = cache_path_for(cache_root, sha256)
    assert load_sniff_cache(cache_root, sha256, config.sniffer_version) is not None

    if mutation == "invalid_json":
        cache_file.write_text("{not-json", encoding="utf-8")
    else:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if mutation == "sha256":
            payload["sha256"] = "0" * 64
        elif mutation == "sniffer_version":
            payload["sniffer_version"] = "pdf_sniff_v0"
        else:
            payload["cache_type"] = "not_a_sniff_cache"
        # 篡改内容伪装成"看起来可信的 PRESENT 大数"：一旦被采纳，下方断言必然暴露
        payload["status"] = SniffStatus.TEXT_LAYER_PRESENT.value
        payload["text_chars"] = 999_999
        payload["avg_chars_per_page"] = 999_999.0
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

    assert load_sniff_cache(cache_root, sha256, config.sniffer_version) is None

    result = resolve_pdf_sniff(pdf, sha256, config=config, cache_root=cache_root)

    assert result.from_cache is False
    assert result.status is SniffStatus.TEXT_LAYER_ABSENT
    assert result.status is not SniffStatus.TEXT_LAYER_PRESENT
    assert result.text_chars == truth.text_chars
    assert result.text_chars != 999_999

    # 重算后缓存被覆盖为真实值，再次命中即为可信数据
    reloaded = load_sniff_cache(cache_root, sha256, config.sniffer_version)
    assert reloaded is not None
    assert reloaded.from_cache is True
    assert reloaded.status is SniffStatus.TEXT_LAYER_ABSENT


def test_sniff_cache_write_failure_degrades(tmp_path: Path, caplog) -> None:
    """TC014-10：缓存目录不可写只 WARNING，路由结果照旧产出。"""

    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)
    sha256 = _sha256(pdf)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    cache_root = blocker / "sniff_cache"

    with caplog.at_level(logging.WARNING, logger="app.core.pdf_sniffing"):
        result = resolve_pdf_sniff(pdf, sha256, config=PdfSniffConfig(), cache_root=cache_root)

    assert result.status is SniffStatus.TEXT_LAYER_PRESENT
    assert result.from_cache is False
    assert not cache_path_for(cache_root, sha256).exists()
    assert any(
        "pdf sniff cache" in record.getMessage() for record in caplog.records
    ), "缓存写失败必须留下 WARNING"


# --------------------------------------------------------------------------- #
# 六状态 → 路由判定闭集映射 + fail-closed 兜底
# --------------------------------------------------------------------------- #

_EXPECTED_MAPPING = {
    SniffStatus.TEXT_LAYER_PRESENT: (RouteType.PDF_DIGITAL, "pdf_sniff_digital"),
    SniffStatus.TEXT_LAYER_ABSENT: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_scanned"),
    SniffStatus.ENCRYPTED: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_encrypted"),
    SniffStatus.EMPTY_DOCUMENT: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_empty"),
    SniffStatus.SNIFF_ERROR: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_error"),
    SniffStatus.DEPENDENCY_MISSING: (RouteType.PDF_SCANNED_OR_IMAGE, "pdf_sniff_unavailable"),
}


def test_pdf_sniff_route_mapping_is_closed_set() -> None:
    assert set(_EXPECTED_MAPPING) == set(SniffStatus), "SniffStatus 新增成员必须同步映射"
    assert {rule for _route_type, rule in _EXPECTED_MAPPING.values()} == set(
        PDF_SNIFF_RULE_NAMES
    )

    for status, (route_type, rule_name) in _EXPECTED_MAPPING.items():
        sniff = PdfSniffResult(status=status, sniffer_version=SNIFFER_VERSION)
        routed, decision, rule, hint, reason = route_pdf_with_sniff(sniff)

        assert (routed, rule) == (route_type, rule_name)
        assert decision == DECISION_ROUTE
        assert hint == "MINERU_FAMILY"
        assert reason
        # 只有 PRESENT 允许判 PDF_DIGITAL；其余一律 fail-closed 到 scanned
        if status is SniffStatus.TEXT_LAYER_PRESENT:
            assert routed is RouteType.PDF_DIGITAL
        else:
            assert routed is RouteType.PDF_SCANNED_OR_IMAGE
            assert routed is not RouteType.PDF_DIGITAL


def test_pdf_sniff_route_fail_closed_on_unmapped_status() -> None:
    """映射表未覆盖的未来状态必须 fail-closed 到 scanned，而不是默认 digital。"""

    unknown = SimpleNamespace(
        status="SOME_FUTURE_STATUS",
        detail="future status",
        text_chars=0,
        pages_total=0,
        pages_sampled=0,
        avg_chars_per_page=0.0,
    )

    routed, _decision, rule, hint, reason = route_pdf_with_sniff(unknown)

    assert routed is RouteType.PDF_SCANNED_OR_IMAGE
    assert routed is not RouteType.PDF_DIGITAL
    assert rule == "pdf_sniff_error"
    assert hint == "MINERU_FAMILY"
    assert reason


# --------------------------------------------------------------------------- #
# MUST-FIX：环境态 / 瞬时态不得入缓存（spec §7 规则 4 / plan §8.4 D-4 澄清）
#
# 判据（TL 预登记）：必须走 resolve_pdf_sniff 且传 cache_root；必须是结果断言
# （缓存文件不存在 / 再跑一次 from_cache is False）；DEPENDENCY_MISSING 与
# SNIFF_ERROR 两类都要覆盖；把白名单去掉后本组必须变红。
# --------------------------------------------------------------------------- #


def test_cache_whitelist_is_closed_set() -> None:
    """白名单 = 全部六状态减去两个非内容态；新增 SniffStatus 必须显式表态。"""

    assert set(pdf_sniffing.CACHEABLE_SNIFF_STATUSES) == set(SniffStatus) - {
        SniffStatus.DEPENDENCY_MISSING,
        SniffStatus.SNIFF_ERROR,
    }
    assert SniffStatus.TEXT_LAYER_PRESENT in pdf_sniffing.CACHEABLE_SNIFF_STATUSES
    assert SniffStatus.DEPENDENCY_MISSING not in pdf_sniffing.CACHEABLE_SNIFF_STATUSES
    assert SniffStatus.SNIFF_ERROR not in pdf_sniffing.CACHEABLE_SNIFF_STATUSES


def test_dependency_missing_is_never_cached(tmp_path: Path, monkeypatch) -> None:
    """环境态不入缓存：装上 pypdf 后必须自愈，而不是永久命中 pdf_sniff_unavailable。"""

    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)
    sha256 = _sha256(pdf)
    cache_root = tmp_path / "sniff_cache"
    config = PdfSniffConfig()

    def _raise_import_error():  # noqa: ANN202 - 模拟 pypdf 未安装
        raise ImportError("No module named 'pypdf'")

    monkeypatch.setattr(pdf_sniffing, "_load_pdf_reader", _raise_import_error)

    result = resolve_pdf_sniff(pdf, sha256, config=config, cache_root=cache_root)

    assert result.status is SniffStatus.DEPENDENCY_MISSING
    assert not cache_path_for(cache_root, sha256).exists(), "环境态不得落盘"
    assert load_sniff_cache(cache_root, sha256, config.sniffer_version) is None

    # 解除依赖缺失后必须重算并自愈（若上面误写缓存，这里会命中 unavailable）
    monkeypatch.undo()
    healed = resolve_pdf_sniff(pdf, sha256, config=config, cache_root=cache_root)
    assert healed.status is SniffStatus.TEXT_LAYER_PRESENT
    assert healed.from_cache is False


def test_sniff_error_is_never_cached(tmp_path: Path) -> None:
    """瞬时态不入缓存：损坏文件每次都重算，不被固化为长期判定。"""

    pdf = write_corrupted_pdf(tmp_path / "broken.pdf")
    sha256 = _sha256(pdf)
    cache_root = tmp_path / "sniff_cache"
    config = PdfSniffConfig()

    first = resolve_pdf_sniff(pdf, sha256, config=config, cache_root=cache_root)

    assert first.status is SniffStatus.SNIFF_ERROR
    assert not cache_path_for(cache_root, sha256).exists(), "瞬时态不得落盘"
    assert load_sniff_cache(cache_root, sha256, config.sniffer_version) is None

    second = resolve_pdf_sniff(pdf, sha256, config=config, cache_root=cache_root)

    assert second.from_cache is False, "非内容态不得被缓存命中"
    assert second.status is SniffStatus.SNIFF_ERROR
    assert not cache_path_for(cache_root, sha256).exists()


@pytest.mark.parametrize(
    "status",
    [
        SniffStatus.TEXT_LAYER_PRESENT,
        SniffStatus.TEXT_LAYER_ABSENT,
        SniffStatus.ENCRYPTED,
        SniffStatus.EMPTY_DOCUMENT,
    ],
)
def test_content_derived_statuses_are_still_cached(
    tmp_path: Path, monkeypatch, status: SniffStatus
) -> None:
    """正对照：白名单只拦环境态/瞬时态，四类内容派生态仍必须落盘并命中。"""

    pdf = write_digital_pdf(tmp_path / "digital.pdf", chars=240)
    sha256 = _sha256(pdf)
    cache_root = tmp_path / "sniff_cache"
    config = PdfSniffConfig()

    stub = PdfSniffResult(status=status, sniffer_version=config.sniffer_version)
    monkeypatch.setattr(pdf_sniffing, "sniff_pdf", lambda *args, **kwargs: stub)

    result = resolve_pdf_sniff(pdf, sha256, config=config, cache_root=cache_root)

    assert result.status is status
    assert cache_path_for(cache_root, sha256).is_file(), f"{status.value} 必须落盘"
    cached = load_sniff_cache(cache_root, sha256, config.sniffer_version)
    assert cached is not None
    assert cached.status is status
