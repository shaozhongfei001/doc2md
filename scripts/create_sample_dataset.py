#!/usr/bin/env python3
"""Create and sync the 902 non-sensitive sample dataset.

No MySQL. No parser adapters. No network. Does not write
backend/tests/fixtures/中文路径/ (sealed 001–010 fixtures).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import zipfile
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = "1.0"
SPEC_ID = "902-test-dataset"

# zipfile 默认把「当前系统时间」写进每个成员的 zip 头，导致每次生成的 docx
# 字节都不同，破坏脚本幂等（A009 / C5）。所有 zip 成员一律使用固定时间戳。
FIXED_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

DUP_GROUP_PLAN = "dup-plan-a"

PLAN_TEXT = "这是一份虚构的银行项目方案摘要，仅供测试使用。\n"
MINUTES_MD = "# 会议纪要\n\n虚构例会记录，不含真实业务数据。\n"
METRICS_CSV = "指标,口径\n示例覆盖率,测试用虚构口径\n"
NOTE_HTML = "<html><body><p>虚构说明页</p></body></html>\n"
META_JSON = '{"title": "虚构元数据", "project": "SAMPLE-902"}\n'


class PathEscapeError(ValueError):
    """Relative path would escape the allowed root."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def resolve_under_root(root: Path, rel: str) -> Path:
    if not rel or rel.startswith(("/", "\\")):
        raise PathEscapeError(f"absolute or empty rel_path rejected: {rel!r}")
    if any(part == ".." for part in Path(rel).parts):
        raise PathEscapeError(f"parent traversal rejected: {rel!r}")
    root_resolved = root.resolve()
    candidate = (root_resolved / rel).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise PathEscapeError(f"escaped root: {rel!r}")
    return candidate


def write_bytes_if_changed(path: Path, data: bytes) -> str:
    if path.exists() and path.read_bytes() == data:
        return "skipped"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return "written"


def minimal_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.1\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200]\n"
        b"   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
        b"4 0 obj\n<< /Length 51 >>\nstream\n"
        b"BT /F1 12 Tf 20 100 Td (hello) Tj ET\n"
        b"endstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
        b"%%EOF\n"
    )


def minimal_docx_bytes() -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>"
        "这是一份虚构的最小 Word 样本，仅供 902 测试。"
        "</w:t></w:r></w:p></w:body></w:document>\n"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>\n"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>\n"
    )
    members = (
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", rels),
        ("word/document.xml", document_xml),
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            # 固定「创建系统」标记，避免不同平台的外部属性差异。
            info.create_system = 0
            info.external_attr = 0o600 << 16
            zf.writestr(info, data)
    return buf.getvalue()


SAMPLE_SPECS: tuple[dict[str, str], ...] = (
    {
        "rel_path": "中文路径/银行项目/方案.txt",
        "purpose": "Chinese path; exact-duplicate pair A",
        "duplicate_group": DUP_GROUP_PLAN,
        "intended_route": "text",
        "kind": "plan_txt",
    },
    {
        "rel_path": "中文路径/银行项目/方案副本.txt",
        "purpose": "Exact sha256 duplicate of 方案.txt",
        "duplicate_group": DUP_GROUP_PLAN,
        "intended_route": "text",
        "kind": "plan_txt",
    },
    {
        "rel_path": "中文路径/银行项目/会议纪要.md",
        "purpose": "Markdown sample for MarkItDown / text route",
        "duplicate_group": "",
        "intended_route": "markitdown",
        "kind": "minutes_md",
    },
    {
        "rel_path": "中文路径/银行项目/指标口径.csv",
        "purpose": "CSV sample",
        "duplicate_group": "",
        "intended_route": "text",
        "kind": "metrics_csv",
    },
    {
        "rel_path": "中文路径/银行项目/说明.html",
        "purpose": "HTML sample for MarkItDown route",
        "duplicate_group": "",
        "intended_route": "markitdown",
        "kind": "note_html",
    },
    {
        "rel_path": "中文路径/银行项目/元数据.json",
        "purpose": "JSON sample for MarkItDown route",
        "duplicate_group": "",
        "intended_route": "markitdown",
        "kind": "meta_json",
    },
    {
        "rel_path": "office_like/最小文档.docx",
        "purpose": "Minimal OOXML for parser_rules office_to_markitdown",
        "duplicate_group": "",
        "intended_route": "markitdown",
        "kind": "docx",
    },
    {
        "rel_path": "pdf/hello.pdf",
        "purpose": "Minimal PDF for parser_rules pdf_to_mineru (file only; no magic-pdf)",
        "duplicate_group": "",
        "intended_route": "mineru",
        "kind": "pdf",
    },
)


def content_for_kind(kind: str) -> bytes:
    mapping = {
        "plan_txt": PLAN_TEXT.encode("utf-8"),
        "minutes_md": MINUTES_MD.encode("utf-8"),
        "metrics_csv": METRICS_CSV.encode("utf-8"),
        "note_html": NOTE_HTML.encode("utf-8"),
        "meta_json": META_JSON.encode("utf-8"),
        "docx": minimal_docx_bytes(),
        "pdf": minimal_pdf_bytes(),
    }
    try:
        return mapping[kind]
    except KeyError as exc:
        raise ValueError(f"unknown sample kind: {kind}") from exc


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_manifest(entries: Iterable[dict[str, str]]) -> str:
    lines = [
        f'schema_version: "{SCHEMA_VERSION}"',
        f'spec: "{SPEC_ID}"',
        "files:",
    ]
    for item in entries:
        group = item["duplicate_group"]
        group_yaml = "null" if not group else yaml_quote(group)
        lines.extend(
            [
                f"  - rel_path: {yaml_quote(item['rel_path'])}",
                f"    sha256: {yaml_quote(item['sha256'])}",
                f"    purpose: {yaml_quote(item['purpose'])}",
                f"    duplicate_group: {group_yaml}",
                f"    intended_route: {yaml_quote(item['intended_route'])}",
            ]
        )
    return "\n".join(lines) + "\n"


def ensure_fixtures(fixture_root: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for spec in SAMPLE_SPECS:
        dest = resolve_under_root(fixture_root, spec["rel_path"])
        payload = content_for_kind(spec["kind"])
        write_bytes_if_changed(dest, payload)
        entries.append(
            {
                "rel_path": spec["rel_path"],
                "sha256": sha256_file(dest),
                "purpose": spec["purpose"],
                "duplicate_group": spec["duplicate_group"],
                "intended_route": spec["intended_route"],
            }
        )
    manifest_path = fixture_root / "manifest.yaml"
    write_bytes_if_changed(manifest_path, render_manifest(entries).encode("utf-8"))
    return entries


def sync_to_runtime(fixture_root: Path, runtime_root: Path, entries: list[dict[str, str]]) -> None:
    runtime_root.mkdir(parents=True, exist_ok=True)
    for item in entries:
        src = resolve_under_root(fixture_root, item["rel_path"])
        dest = resolve_under_root(runtime_root, item["rel_path"])
        write_bytes_if_changed(dest, src.read_bytes())
    dest_manifest = resolve_under_root(runtime_root, "manifest.yaml")
    write_bytes_if_changed(dest_manifest, (fixture_root / "manifest.yaml").read_bytes())


def default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate backend/tests/fixtures/sample_dataset/ and sync a "
            "runtime copy to data/sample_dataset/. Does not connect to MySQL "
            "or invoke parsers."
        )
    )
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--fixture-root", type=Path, default=None)
    parser.add_argument("--runtime-root", type=Path, default=None)
    parser.add_argument("--skip-sync", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = (args.repo_root or default_repo_root()).resolve()
    fixture_root = (
        args.fixture_root or (repo / "backend/tests/fixtures/sample_dataset")
    ).resolve()
    runtime_root = (args.runtime_root or (repo / "data/sample_dataset")).resolve()

    sealed = (repo / "backend/tests/fixtures/中文路径").resolve()
    if fixture_root == sealed or sealed in fixture_root.parents:
        print("refusing to write sealed fixtures/中文路径", file=sys.stderr)
        return 2

    entries = ensure_fixtures(fixture_root)
    if not args.skip_sync:
        sync_to_runtime(fixture_root, runtime_root, entries)
    print(f"fixture_root={fixture_root}")
    if not args.skip_sync:
        print(f"runtime_root={runtime_root}")
    print(f"files={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
