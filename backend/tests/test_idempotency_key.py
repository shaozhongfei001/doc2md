"""P1-L3 · T-D2MD-03 反例与断言（P1-L3-PLAN.md §二）。

C1 第二次解析命中幂等 / C2 generated_at 可复现、read_set_digest 稳定 /
C3 变更才触发重解析 / C4 平台脚本不再主动破坏幂等 / C5 additive 与四件套不变
R-1~R-6 对应反例；R-7 已按 TL 02:58 批准口径补齐两条
（队列状态 + 调度事实 mock.patch 双拦截），本文件共 13 条断言。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from app.core.parsed_paths import (
    KB_MANIFEST_FILENAME,
    build_kb_manifest_path,
    build_manifest_additive_fields,
    compute_parser_fingerprint,
    compute_read_set_digest,
    deterministic_generated_at,
    should_reparse,
)

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent
FIXTURES = TESTS_DIR / "fixtures" / "idempotency_corpus"
PLATFORM_SCRIPT = Path("/home/szf/data/dev/llmke/scripts/v2_ingest_doc2md.py")

SHA = "c4679e05e6a78f0378c69d0e525fe2e1740a952d9af67832367d681e3c8aa869"
PARSER_NAME = "markitdown"
ADAPTER_VERSION = "005_mvp_v1"
ROUTING_VERSION = "004_mvp_v2"


def _artifact_dir(root: Path, text: str, *, generated_at: str = "2026-11-08T00:00:00Z") -> Path:
    """构造一份离线四件套（不调用任何真实解析器 / DB / 服务）。"""
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "parsed_text.md").write_text(text, encoding="utf-8")
    (d / "parsed_metadata.json").write_text(
        json.dumps({"parser_name": PARSER_NAME, "generated_at": generated_at}) + "\n",
        encoding="utf-8",
    )
    (d / "document_ir.json").write_text(
        json.dumps({"schema_version": "1.0", "blocks": []}) + "\n", encoding="utf-8"
    )
    manifest = json.loads((FIXTURES / "parse_manifest.sample.json").read_text(encoding="utf-8"))
    manifest["generated_at"] = generated_at
    manifest["output_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest["output_size_bytes"] = len(text.encode("utf-8"))
    (d / "parse_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return d


def _fields(d: Path) -> dict:
    return build_manifest_additive_fields(
        kb="kb_9", source_root="kb_9", parsed_dir=d,
        parser_name=PARSER_NAME, adapter_version=ADAPTER_VERSION,
        routing_version=ROUTING_VERSION,
    )


def _manifest(d: Path) -> dict:
    return json.loads((d / "parse_manifest.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- C1 / R-1
def test_c1_second_parse_skipped_and_bytes_unchanged(tmp_path: Path) -> None:
    d = _artifact_dir(tmp_path / "a", "P1L3 stable content\n")
    digest = compute_read_set_digest(d)
    fp = compute_parser_fingerprint(PARSER_NAME, ADAPTER_VERSION, ROUTING_VERSION)
    before = {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}

    assert should_reparse(
        None, sha256=SHA, parser_fingerprint=fp, read_set_digest=digest
    ) is True  # 首次无既有成功清单 → 解析
    merged = {**_manifest(d), **_fields(d)}
    assert should_reparse(
        merged, sha256=SHA, parser_fingerprint=fp, read_set_digest=digest
    ) is False  # 第二次命中幂等 → 跳过
    after = {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}
    assert before == after  # 四件套字节逐字节不变


# --------------------------------------------------------------------------- C2 / R-2
def test_c2_generated_at_excluded_from_read_set_digest(tmp_path: Path) -> None:
    d1 = _artifact_dir(tmp_path / "a", "same bytes\n", generated_at="2026-01-01T00:00:00Z")
    d2 = _artifact_dir(tmp_path / "b", "same bytes\n", generated_at="2026-02-02T12:34:56Z")
    h1 = compute_read_set_digest(d1)
    h2 = compute_read_set_digest(d2)
    assert len(h1) == 64 and len(h2) == 64
    assert h1 == h2  # generated_at 不进摘要


def test_c2_deterministic_generated_at() -> None:
    digest = "a" * 64
    a = deterministic_generated_at(digest)
    b = deterministic_generated_at(digest)
    assert a == b and a != "stub"
    assert a.endswith("Z") and len(a) >= 20  # 合法 ISO UTC 字符串


# --------------------------------------------------------------------------- C3 / R-3 / R-4
def test_c3_source_byte_change_triggers_reparse(tmp_path: Path) -> None:
    d1 = _artifact_dir(tmp_path / "a", "version one\n")
    d2 = _artifact_dir(tmp_path / "b", "version two\n")
    assert compute_read_set_digest(d1) != compute_read_set_digest(d2)
    assert should_reparse(
        {**_manifest(d1), "read_set_digest": compute_read_set_digest(d1),
         "parser_fingerprint": "fp", "sha256": SHA, "status": "SUCCESS"},
        sha256=SHA, parser_fingerprint="fp",
        read_set_digest=compute_read_set_digest(d2),
    ) is True


def test_c3_parser_fingerprint_change_triggers_reparse() -> None:
    fp1 = compute_parser_fingerprint(PARSER_NAME, ADAPTER_VERSION, ROUTING_VERSION)
    fp2 = compute_parser_fingerprint(PARSER_NAME, "005_mvp_v2", ROUTING_VERSION)
    assert fp1 != fp2
    assert should_reparse(
        {"sha256": SHA, "parser_fingerprint": fp1, "read_set_digest": "d" * 64,
         "status": "SUCCESS"},
        sha256=SHA, parser_fingerprint=fp2, read_set_digest="d" * 64,
    ) is True


def test_c3_unchanged_items_still_skipped(tmp_path: Path) -> None:
    d = _artifact_dir(tmp_path / "a", "unchanged\n")
    digest = compute_read_set_digest(d)
    fp = compute_parser_fingerprint(PARSER_NAME, ADAPTER_VERSION, ROUTING_VERSION)
    merged = {**_manifest(d), **_fields(d)}
    merged["read_set_digest"] = digest
    merged["parser_fingerprint"] = fp
    assert should_reparse(
        merged, sha256=SHA, parser_fingerprint=fp, read_set_digest=digest
    ) is False


# --------------------------------------------------------------------------- C4 / R-5
def test_c4_platform_script_no_forced_reparse() -> None:
    src = PLATFORM_SCRIPT.read_text(encoding="utf-8")
    assert "_reset_vault_state" not in src  # 移除定义与调用
    assert "--force" not in src              # 移除 PDF 强制重解析
    assert "read_set_digest" in src and "parser_fingerprint" in src  # 条件重解析判据


# --------------------------------------------------------------------------- C5 / R-6
def test_c5_additive_fields_preserve_existing_keys(tmp_path: Path) -> None:
    d = _artifact_dir(tmp_path / "a", "additive\n")
    existing = _manifest(d)
    fields = _fields(d)
    assert set(fields) == {"kb", "source_root", "read_set_digest", "parser_fingerprint"}
    merged = {**existing, **fields}
    assert all(merged[k] == v for k, v in existing.items())  # 既有键语义不变
    assert len(fields["read_set_digest"]) == 64
    assert len(fields["parser_fingerprint"]) == 64

    # TL 03:0x 裁定 A：四字段全部存在（不得缩水成只查两字段），且 parser 实际写入的
    # kb / source_root 为 JSON null（非缺字段、非空串）。
    real = build_manifest_additive_fields(
        kb=None, source_root=None, parsed_dir=d,
        parser_name=PARSER_NAME, adapter_version=ADAPTER_VERSION,
        routing_version=ROUTING_VERSION,
    )
    assert set(real) == {"kb", "source_root", "read_set_digest", "parser_fingerprint"}
    assert real["kb"] is None and real["source_root"] is None
    assert len(real["read_set_digest"]) == 64
    assert len(real["parser_fingerprint"]) == 64
    # 摘要输入不含 kb/source_root（两 null 字段不参与哈希）；库隔离由 per-kb
    # kb-manifest.json 与 (file_id, sha256) 复合键承担，而非 read_set_digest 自身。
    assert real["read_set_digest"] == fields["read_set_digest"]

    # TL 03:0x 裁定 B：C5「语义不变」限缩为 键存在 / 类型 / ISO 格式 / 既有消费者可解析；
    # generated_at（及 converted_at）取值语义由 datetime.now(UTC) 改为确定性派生，
    # 是本 loop 唯一授权的取值语义变更，不悄悄绕过。
    ga = deterministic_generated_at(SHA)
    assert isinstance(ga, str) and ga.endswith("Z")
    assert datetime.fromisoformat(ga.replace("Z", "+00:00"))
    assert ga == deterministic_generated_at(SHA)  # 可复现
    assert "generated_at" in existing and isinstance(existing["generated_at"], str)


def test_c5_parsed_text_hash_unchanged(tmp_path: Path) -> None:
    d = _artifact_dir(tmp_path / "a", "hash stable\n")
    text_bytes = (d / "parsed_text.md").read_bytes()
    manifest = _manifest(d)
    assert manifest["output_hash"] == hashlib.sha256(text_bytes).hexdigest()
    _ = _fields(d)  # additive 字段计算不得改写 parsed_text.md
    assert (d / "parsed_text.md").read_bytes() == text_bytes
    assert _manifest(d)["output_hash"] == hashlib.sha256(text_bytes).hexdigest()


def test_r6_parsed_text_tamper_rejected(tmp_path: Path) -> None:
    d = _artifact_dir(tmp_path / "a", "evidence span\n")
    manifest = _manifest(d)
    assert manifest["output_hash"] == hashlib.sha256(
        (d / "parsed_text.md").read_bytes()
    ).hexdigest()
    (d / "parsed_text.md").write_bytes(
        (d / "parsed_text.md").read_bytes() + b"X"
    )  # 企图改一个字节
    assert manifest["output_hash"] != hashlib.sha256(
        (d / "parsed_text.md").read_bytes()
    ).hexdigest()  # store._check_source_consistency 的 output_hash 分支必然拒绝


def test_c5_kb_manifest_path_constant(tmp_path: Path) -> None:
    assert KB_MANIFEST_FILENAME == "kb-manifest.json"
    assert build_kb_manifest_path(tmp_path / "kb_9").name == KB_MANIFEST_FILENAME


# --------------------------------------------------------------------------- R-7（TL 02:58 批准口径）
def test_r7_unknown_route_queue_state(tmp_path: Path) -> None:
    """队列状态：UNKNOWN 项显式登记、不静默丢弃、不整批失败（只调用 L2，不改其语义）。"""
    from app.services.kb_diff import compute_kb_diff, load_kb_manifest
    from app.services.parse_queue import RouteInfo, build_parse_queue

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "U").write_bytes(b"unknown-route\n")
    (corpus / "N.md").write_bytes(b"normal\n")
    manifest = {
        "schema": "llmke.doc2md.kb-manifest/1", "kb": "kb_9",
        "cursor": "cursor-base", "set_digest": "0" * 64,
        "generated_at": "2026-11-08T00:00:00+00:00",
        "parser_fingerprint": "fp-v1", "entries": [],
    }
    (corpus / "kb-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    prev = load_kb_manifest(corpus / "kb-manifest.json")
    diff = compute_kb_diff(
        kb="kb_9", scan_path=corpus, previous_manifest=prev,
        parser_fingerprint="fp-v1",
    )
    sha_u = hashlib.sha256(b"unknown-route\n").hexdigest()
    sha_n = hashlib.sha256(b"normal\n").hexdigest()
    routes = {
        sha_u: RouteInfo("UNKNOWN", "kb_diff_v1"),
        sha_n: RouteInfo("TEXT_OR_MARKDOWN", "kb_diff_v1"),
    }
    built = build_parse_queue(
        kb="kb_9", diff=diff, previous_manifest=prev, routes=routes
    )
    unknown_items = [i for i in built.items if i.sha256 == sha_u]
    assert len(unknown_items) == 1
    item = unknown_items[0]
    assert item.route_type == "UNKNOWN"
    assert item.status == "PENDING" and item.fail_reason is None
    assert not any(r.get("sha256") == sha_u for r in built.rejected)
    assert len(built.items) == 2  # 正常项仍在，不整批失败
    normal = [i for i in built.items if i.sha256 == sha_n]
    assert normal and normal[0].status == "PENDING"


def test_r7_unknown_route_dispatch_fact() -> None:
    """调度事实（TL 02:58 追加）：UNKNOWN 确实走默认解析器 MarkItDown 分支，
    返回可观测 ParseOutcome；Mineru 分支未被调用。不改 L2 代码。"""
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.core.config import load_config
    from app.services.parse_queue import DefaultParseRunner, ParseOutcome, QueueItem

    unknown = QueueItem(
        sha256="b" * 64, file_ids=["U"], source_path="U",
        route_type="UNKNOWN", parser_adapter_version="kb_diff_v1",
        diff_reason="added", status="PENDING",
    )
    fake = SimpleNamespace(failed_count=0, parsed_count=1)
    config = load_config(REPO_ROOT / "config" / "app.yaml")
    with patch(
        "app.services.markitdown_parser.MarkItDownParserService.parse_markitdown",
        return_value=fake,
    ) as md, patch(
        "app.services.mineru_pdf_parser.MineruPdfParserService.parse_many",
        return_value=fake,
    ) as mn:
        outcome = DefaultParseRunner(config).parse(unknown)
    assert md.call_count == 1
    assert md.call_args.kwargs.get("sha256") == unknown.sha256
    assert mn.call_count == 0
    assert isinstance(outcome, ParseOutcome)
    assert outcome.ok is True
    assert isinstance(outcome.message, str) and outcome.message
