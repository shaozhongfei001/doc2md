from __future__ import annotations

from pathlib import Path
from typing import TypedDict


class ParsedArtifactPaths(TypedDict):
    parsed_dir: Path
    parsed_text: Path
    parsed_metadata: Path
    parse_manifest: Path
    # 017 additive：Document IR 第 4 件产物 + 渲染轨 + 解析器结构化中间件源目录
    document_ir: Path
    rendered_md: Path
    ir_source: Path


def build_parsed_content_dir(parsed_root: Path, sha256: str) -> Path:
    prefix_a = sha256[:2].lower()
    prefix_b = sha256[2:4].lower()
    return parsed_root / "by_hash" / prefix_a / prefix_b / sha256


def build_parsed_artifact_paths(parsed_dir: Path) -> ParsedArtifactPaths:
    return ParsedArtifactPaths(
        parsed_dir=parsed_dir,
        parsed_text=parsed_dir / "parsed_text.md",
        parsed_metadata=parsed_dir / "parsed_metadata.json",
        parse_manifest=parsed_dir / "parse_manifest.json",
        document_ir=parsed_dir / "document_ir.json",
        rendered_md=parsed_dir / "rendered.md",
        ir_source=parsed_dir / "ir_source",
    )


# P1-L1 additive：库级清单文件名（scan-diff 的默认读/写目标）。
# 仅新增常量，既有 ParsedArtifactPaths / build_parsed_* 未改。
KB_MANIFEST_FILENAME = "kb-manifest.json"


def build_kb_manifest_path(kb_root: Path) -> Path:
    """P1-L3 additive：库级清单路径（受控对象 ID，不用任意主机路径）。"""
    return Path(kb_root) / KB_MANIFEST_FILENAME


# ---------------------------------------------------------------------------
# P1-L3 additive：稳定幂等键（对齐平台 parse_artifact/store.py:160 read_set_digest）
# 既有 ParsedArtifactPaths / build_parsed_* / KB_MANIFEST_FILENAME 一字未改。
# ---------------------------------------------------------------------------
import hashlib as _hashlib
import json as _json
from datetime import UTC as _UTC
from datetime import datetime as _datetime

from app.core.ids import compute_sha256 as _compute_sha256

_TRANSIENT_METADATA_KEYS = frozenset({"generated_at", "converted_at"})


def _part_role(relative_path: str) -> str:
    if relative_path.startswith("assets/"):
        return "asset"
    if relative_path.startswith("ir_source/"):
        return "ir_source"
    if relative_path == "parsed_text.md":
        return "text"
    if relative_path == "parsed_metadata.json":
        return "metadata"
    if relative_path == "document_ir.json":
        return "ir"
    if relative_path == "rendered.md":
        return "rendered"
    return "extra"


def _normalized_part(path: Path) -> tuple[str, int]:
    """JSON 产物剔除 generated_at / converted_at 瞬态键再规范化；其余按文件字节。"""
    if path.suffix.lower() == ".json":
        try:
            payload = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload = {
                    k: v for k, v in payload.items()
                    if k not in _TRANSIENT_METADATA_KEYS
                }
                canonical = _json.dumps(
                    payload, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                return _hashlib.sha256(canonical).hexdigest(), len(canonical)
        except (OSError, ValueError):
            pass
    return _compute_sha256(path), path.stat().st_size


def compute_read_set_digest(parsed_dir: Path) -> str:
    """读取集合摘要：对除 ``parse_manifest.json`` 自身外的产物按
    ``(relative_path, role, sha256, size_bytes)`` 规范序 sha256。

    - 排除 parse_manifest.json：避免把 read_set_digest / generated_at 写进被摘要文件
      自身造成永不收敛（GK-KE FAIL-2026-09-12-03 自引用教训）；
    - JSON 产物剔除 generated_at / converted_at 瞬态键：C2「仅 generated_at 不同」摘要相同；
    - 对齐平台 store.read_set_digest 的 (role, relative_path, sha256, size_bytes) 口径。
    """
    root = Path(parsed_dir)
    parts = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == "parse_manifest.json" or any(
            part.startswith(".") for part in Path(rel).parts
        ):
            continue
        digest, size = _normalized_part(path)
        parts.append({
            "role": _part_role(rel),
            "relative_path": rel,
            "sha256": digest,
            "size_bytes": size,
        })
    core = sorted(parts, key=lambda x: (x["relative_path"], x["role"]))
    canonical = _json.dumps(
        core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_parser_fingerprint(
    parser_name: str, adapter_version: str, routing_version: str
) -> str:
    """确定性解析器指纹（代码/适配器/路由版本，不依赖时间）。"""
    canonical = _json.dumps(
        {"parser_name": parser_name,
         "parser_adapter_version": adapter_version,
         "routing_rules_version": routing_version},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return _hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deterministic_generated_at(source_sha256: str) -> str:
    """可复现时间：由源文件 sha256 派生的确定性 UTC 时刻（合法 ISO Z 字符串）。

    不依赖 datetime.now()，且输入在解析前即可得，避免「digest ↔ metadata.generated_at」
    循环依赖。同一源文件两次解析 → 同一 parse_manifest 字节。
    """
    seconds = int(source_sha256[:12], 16) % 1_500_000_000 + 1_400_000_000
    return _datetime.fromtimestamp(seconds, _UTC).isoformat().replace("+00:00", "Z")


def build_manifest_additive_fields(
    *,
    kb: str | None = None,
    source_root: str | None = None,
    parsed_dir: Path,
    parser_name: str,
    adapter_version: str,
    routing_version: str,
) -> dict:
    """parse_manifest.json additive 四字段（既有键不删不改语义）。"""
    return {
        "kb": kb,
        "source_root": source_root,
        "read_set_digest": compute_read_set_digest(parsed_dir),
        "parser_fingerprint": compute_parser_fingerprint(
            parser_name, adapter_version, routing_version
        ),
    }


def should_reparse(
    existing_manifest: dict | None,
    *,
    sha256: str,
    parser_fingerprint: str,
    read_set_digest: str,
) -> bool:
    """仅当 status / sha256 / parser_fingerprint / read_set_digest 任一变化才重解析。"""
    if not existing_manifest or existing_manifest.get("status") != "SUCCESS":
        return True
    return (
        existing_manifest.get("sha256") != sha256
        or existing_manifest.get("parser_fingerprint") != parser_fingerprint
        or existing_manifest.get("read_set_digest") != read_set_digest
    )
