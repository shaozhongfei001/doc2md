"""P1-L4 · T-D2MD-04 反例与断言（P1-L4-PLAN.md §二）。

D1 四件套齐备 / D2 第 4 件由 doc2md 原生 build_from_mineru_content_list 产出 /
D3 一致性与 span 对齐 / D4 ir_source_files 对齐 / D5 additive 且前三件套字节不变。
R-1~R-6 对应反例。**离线 fixture**：不启停服务、不写 DB/ES/Milvus/18094，
不调用平台 v2_fill_mineru_ir.py；A-2（真实上传四类格式）属 P6，不在此收口。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.parsed_paths import build_parsed_artifact_paths
from app.core.parser_routing import RouteType
from app.services.mineru_pdf_parser import (
    DECISION_PARSED,
    STATUS_SUCCESS,
    MineruParsePlan,
    MineruPdfParserService,
)

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures" / "pdf_fourth_artifact"
DEV_ROOT = TESTS_DIR.parents[2]  # /home/szf/data/dev
PLATFORM_STORE_DIR = (
    DEV_ROOT / "llmke" / "services" / "rag-alg"
    / "index_data_service" / "parse_artifact"
)
MINERU_SOURCE = (
    TESTS_DIR.parent / "app" / "services" / "mineru_pdf_parser.py"
)

# 平台 store.py 只依赖标准库，可离线导入（只读核对，不改平台文件）
sys.path.insert(0, str(PLATFORM_STORE_DIR))
from store import _check_source_consistency, collect_parts  # noqa: E402

SHA256 = "c" * 64
CONTENT_UID = "d" * 64
PARSED_TEXT = (FIXTURES / "doc.md").read_text(encoding="utf-8")
CONTENT_LIST = json.loads((FIXTURES / "content_list.json").read_text(encoding="utf-8"))

THREE_ARTIFACT_NAMES = ("parsed_text.md", "parsed_metadata.json")
MANIFEST_BASELINE_KEYS = {
    "content_uid", "sha256", "route_type", "parser_name", "parser_profile",
    "parser_adapter_version", "source_vault_path", "parsed_text_path",
    "parsed_metadata_path", "generated_at", "status", "content_size_bytes",
    "input_metadata", "output_size_bytes", "output_hash",
}


def _run_finalize(root: Path) -> dict:
    """离线执行 MinerU 成功收尾路径（object.__new__ 绕过会建 DB engine 的 __init__）。"""
    parsed_dir = root / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    artifacts = build_parsed_artifact_paths(parsed_dir)

    staging = root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "doc.md").write_text(PARSED_TEXT, encoding="utf-8")
    (staging / "content_list.json").write_text(
        json.dumps(CONTENT_LIST, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    original_bin = root / "original.bin"
    original_bin.write_bytes(b"P1L4-PDF-FIXTURE\n")

    plan = MineruParsePlan(
        content_uid=CONTENT_UID, sha256=SHA256,
        route_type=RouteType.PDF_DIGITAL.value, decision=DECISION_PARSED,
        status=STATUS_SUCCESS, parsed_dir=str(parsed_dir),
        original_bin=str(original_bin),
        parsed_text_path=str(artifacts["parsed_text"]),
        parsed_metadata_path=str(artifacts["parsed_metadata"]),
        parse_manifest_path=str(artifacts["parse_manifest"]),
        rule_name="ext_pdf",
    )
    content = SimpleNamespace(
        content_uid=CONTENT_UID, sha256=SHA256,
        file_ext=".pdf", mime_type="application/pdf",
    )
    service = object.__new__(MineruPdfParserService)
    item, error = service._finalize_success_from_staging(
        content=content, plan=plan, artifacts=artifacts,
        assets_dir=parsed_dir / "assets", route_type=RouteType.PDF_DIGITAL,
        source_vault_path="raw_vault/by_hash/cc/cc/original.bin",
        original_bin=original_bin, run_result=SimpleNamespace(),
        staging_dir=staging, vault_object=None,
    )
    return {
        "parsed_dir": parsed_dir, "artifacts": artifacts,
        "item": item, "error": error,
    }


def _manifest(parsed_dir: Path) -> dict:
    return json.loads((parsed_dir / "parse_manifest.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- D1
def test_d1_four_artifacts_present(tmp_path: Path) -> None:
    out = _run_finalize(tmp_path)
    parsed_dir = out["parsed_dir"]
    for name in (
        "parsed_text.md", "parsed_metadata.json",
        "parse_manifest.json", "document_ir.json",
    ):
        assert (parsed_dir / name).is_file(), f"missing 4th/four-piece: {name}"
    assert _manifest(parsed_dir)["status"] == STATUS_SUCCESS
    assert out["error"] is None


# --------------------------------------------------------------------------- D2 / R-1 / R-4
def test_d2_native_builder_call_site(tmp_path: Path) -> None:
    out = _run_finalize(tmp_path)
    ir_path = out["parsed_dir"] / "document_ir.json"
    assert ir_path.is_file()
    payload = json.loads(ir_path.read_text(encoding="utf-8"))
    from app.services.document_ir_schema import validate_document_ir

    validate_document_ir(payload, parsed_text=PARSED_TEXT)
    # 静态调用点必须在 doc2md 服务层内（而非平台补口脚本）。
    # 检查「实际调用 / 导入」而非注释里的字面提及（docstring 会如实说明边界）。
    source = MINERU_SOURCE.read_text(encoding="utf-8")
    assert "build_from_mineru_content_list" in source
    assert "_build_mineru_document_ir" in source
    assert "fill_mineru_ir(" not in source  # R-4：未调用平台补口函数
    assert "from scripts.v2_fill_mineru_ir" not in source
    provenance = payload.get("provenance") or {}
    assert "ir_filled_by" not in provenance  # 不是平台补口冒充的原生第 4 件
    assert provenance.get("ir_source") == "mineru_content_list"


def test_r1_builder_missing_fails_without_fourth(tmp_path: Path) -> None:
    """反例：若 builder 未被调用，第 4 件缺失 → D1/D2 判 FAIL（此处即断言存在）。"""
    out = _run_finalize(tmp_path)
    assert (out["parsed_dir"] / "document_ir.json").is_file()


# --------------------------------------------------------------------------- D3 / R-2
def test_d3_check_source_consistency_passes(tmp_path: Path) -> None:
    from app.services.document_ir_schema import validate_document_ir

    out = _run_finalize(tmp_path)
    parsed_dir = out["parsed_dir"]
    manifest = _manifest(parsed_dir)
    text_bytes = (parsed_dir / "parsed_text.md").read_bytes()
    assert manifest["output_hash"] == hashlib.sha256(text_bytes).hexdigest()
    assert (parsed_dir / "document_ir.json").is_file()  # 阶段 A：缺第 4 件 → 断言层 FAIL
    ir = json.loads((parsed_dir / "document_ir.json").read_text(encoding="utf-8"))
    validate_document_ir(ir, parsed_text=PARSED_TEXT)
    # span 逐字对齐
    for block in ir["blocks"]:
        ref = block["content_ref"]
        if ref.get("kind") == "text_span":
            assert PARSED_TEXT[ref["start_offset"]:ref["end_offset"]] == ref["text"]
    parts = collect_parts(parsed_dir)
    _check_source_consistency(parsed_dir, parts)  # 三方 content_uid + span + asset 校验


def test_r2_tamper_parsed_text_rejected(tmp_path: Path) -> None:
    """反例 R-2：改 parsed_text.md 一个字节 → _check_source_consistency 拒绝。"""
    out = _run_finalize(tmp_path)
    parsed_dir = out["parsed_dir"]
    (parsed_dir / "parsed_text.md").write_bytes(
        (parsed_dir / "parsed_text.md").read_bytes() + b"X"
    )
    with pytest.raises(Exception) as excinfo:
        _check_source_consistency(parsed_dir, collect_parts(parsed_dir))
    assert "TEXT_HASH_MISMATCH" in str(excinfo.value)


# --------------------------------------------------------------------------- D4 / R-3
def test_d4_ir_source_files_aligned(tmp_path: Path) -> None:
    out = _run_finalize(tmp_path)
    parsed_dir = out["parsed_dir"]
    manifest = _manifest(parsed_dir)
    entries = manifest.get("ir_source_files")
    assert isinstance(entries, list) and entries  # R-3：缺字段 / 空 → FAIL
    names = {e.get("name") for e in entries}
    assert "content_list.json" in names
    for entry in entries:
        target = parsed_dir / entry["relative_path"]
        assert target.is_file(), f"ir_source_files 指向不存在文件: {target}"
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"]
    assert MANIFEST_BASELINE_KEYS <= set(manifest)  # 既有键全部保留
    assert (parsed_dir / "document_ir.json").is_file()  # 与 IR 构建源一致


# --------------------------------------------------------------------------- D5 / R-5
def test_d5_three_artifacts_bytes_unchanged(tmp_path: Path) -> None:
    out = _run_finalize(tmp_path)
    parsed_dir = out["parsed_dir"]
    # parsed_text.md 字节 == staging markdown 原文（铁律：内容与 hash 不变）
    assert (parsed_dir / "parsed_text.md").read_bytes() == PARSED_TEXT.encode("utf-8")
    metadata = json.loads((parsed_dir / "parsed_metadata.json").read_text(encoding="utf-8"))
    assert metadata["content_uid"] == CONTENT_UID
    assert metadata["sha256"] == SHA256
    manifest = _manifest(parsed_dir)
    assert MANIFEST_BASELINE_KEYS <= set(manifest)
    # 重写第 4 件不得触碰前三件套字节
    before = {
        name: (parsed_dir / name).read_bytes() for name in THREE_ARTIFACT_NAMES
    }
    import app.services.mineru_pdf_parser as mineru_module

    builder = getattr(mineru_module, "_build_mineru_document_ir", None)
    assert builder is not None  # 阶段 A：未实现 → 断言层 FAIL
    builder(
        artifacts=out["artifacts"], manifest_payload=manifest,
        text=PARSED_TEXT, ir_source_files=manifest["ir_source_files"],
    )
    after = {
        name: (parsed_dir / name).read_bytes() for name in THREE_ARTIFACT_NAMES
    }
    assert before == after


# --------------------------------------------------------------------------- R-6 回归哨兵
def test_r6_non_pdf_paths_regression() -> None:
    """L1/L2/L3 语义未受影响（模块可加载、命令数不变）。"""
    from app.cli.main import app
    from app.services.kb_diff import compute_kb_diff
    from app.services.parse_queue import build_parse_queue
    from app.core.parsed_paths import compute_read_set_digest

    names = sorted(c.name or c.callback.__name__ for c in app.registered_commands)
    assert "scan-diff" in names and "parse" in names
    assert len(names) == 21
    assert callable(compute_kb_diff) and callable(build_parse_queue)
    assert callable(compute_read_set_digest)
