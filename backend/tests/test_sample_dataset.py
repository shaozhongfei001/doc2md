"""902 测试样本集 —— 只读校验。

约束（见 specs/902-test-dataset/）：
* 不连接 MySQL，不 `create_engine`，不 `session.commit`；
* 不 import `app.services.*` 解析服务，不 spawn `magic-pdf`；
* 不修改 `backend/tests/fixtures/中文路径/**`（001–010 封存资产）；
* 只依赖标准库 + pytest，便于在 venv 就绪前运行。
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import subprocess
import sys
import tokenize
import zipfile
from pathlib import Path
from typing import Iterable

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_ROOT = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "sample_dataset"
SEALED_ROOT = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "中文路径"
SCRIPT = PROJECT_ROOT / "scripts" / "create_sample_dataset.py"
SH_SCRIPT = PROJECT_ROOT / "scripts" / "create_sample_dataset.sh"

# --- PATTERNS_BEGIN ---
# 下列字面量仅是"禁止模式"清单，不是真实调用。
# 本文件的自检用例会整段跳过此区间，避免自指误判。
FORBIDDEN_DB_PATTERNS = (
    "create_engine",
    "pymysql",
    "session.commit",
    "sqlalchemy",
    "mysql.connector",
)

# 只禁"真实调用"，不禁文档里的路由名词（脚本 docstring 会提到 markitdown/mineru）。
FORBIDDEN_PARSER_PATTERNS = (
    "magic-pdf",
    "magic_pdf",
    "from app.services",
    "import app",
    "MinerUParser",
    "MarkItDownParser",
    "ParserService",
    "subprocess.run",
)
# --- PATTERNS_END ---

# spec §3.1 清单
EXPECTED_REL_PATHS: tuple[str, ...] = (
    "中文路径/银行项目/方案.txt",
    "中文路径/银行项目/方案副本.txt",
    "中文路径/银行项目/会议纪要.md",
    "中文路径/银行项目/指标口径.csv",
    "中文路径/银行项目/说明.html",
    "中文路径/银行项目/元数据.json",
    "office_like/最小文档.docx",
    "pdf/hello.pdf",
)

DUP_PAIR = (
    "中文路径/银行项目/方案.txt",
    "中文路径/银行项目/方案副本.txt",
)

TEXT_LIKE = (
    "中文路径/银行项目/方案.txt",
    "中文路径/银行项目/方案副本.txt",
    "中文路径/银行项目/会议纪要.md",
)

SENSITIVE_PATTERNS = (
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
    "-----BEGIN",
    "password=",
    "password:",
    "passwd=",
    "secret=",
    "api_key=",
    "apikey=",
    "token=",
    "AKIA",
)


# --------------------------------------------------------------------------
# helpers（manifest.yaml 用极小解析，避免引入 PyYAML 依赖）
# --------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def parse_manifest(text: str) -> list[dict[str, str | None]]:
    entries: list[dict[str, str | None]] = []
    current: dict[str, str | None] | None = None
    field_pattern = re.compile(r'^\s{4,}([A-Za-z_][A-Za-z0-9_]*):\s*(?:"(.*)"|null)\s*$')
    item_pattern = re.compile(r'^\s*-\s+rel_path:\s*"(.*)"\s*$')
    for line in text.splitlines():
        item_match = item_pattern.match(line)
        if item_match:
            current = {
                "rel_path": _unescape(item_match.group(1)),
                "sha256": None,
                "duplicate_group": None,
                "intended_route": None,
            }
            entries.append(current)
            continue
        field = field_pattern.match(line)
        if field and current is not None:
            current[field.group(1)] = (
                None if field.group(2) is None else _unescape(field.group(2))
            )
    return entries


def load_manifest_entries() -> list[dict[str, str | None]]:
    return parse_manifest((SAMPLE_ROOT / "manifest.yaml").read_text(encoding="utf-8"))


def load_script_module():
    spec = importlib.util.spec_from_file_location("_sample_dataset_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run_generate(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_noise(source: str) -> str:
    """只保留真实代码：剥离注释、文档串与字符串字面量。

    禁词扫描若直接扫源码，会把「禁止事项说明」里的名词误判为违规调用。
    同时跳过本文件的「禁止模式清单」区间，避免自检用例扫到自身字面量。
    """
    without_block = re.sub(
        r"# --- PATTERNS_BEGIN ---.*?# --- PATTERNS_END ---",
        "",
        source,
        flags=re.DOTALL,
    )
    kept: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(without_block).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(token.string)
    return " ".join(kept)


def assert_no_pattern(source: str, patterns: Iterable[str], label: str) -> None:
    lowered = source.lower()
    hits = [pattern for pattern in patterns if pattern.lower() in lowered]
    assert not hits, f"{label} 命中禁止模式: {hits}"


# --------------------------------------------------------------------------
# TC001 / TC002 / TC015 —— 清单、中文路径、中文内容
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rel_path", EXPECTED_REL_PATHS)
def test_fixture_exists_and_non_empty(rel_path: str) -> None:
    target = SAMPLE_ROOT / rel_path
    assert target.is_file(), f"缺少样本文件: {rel_path}"
    assert target.stat().st_size > 0, f"样本文件为空: {rel_path}"


def test_chinese_path_readable() -> None:
    target = SAMPLE_ROOT / "中文路径" / "银行项目" / "方案.txt"
    assert target.read_text(encoding="utf-8").strip(), "中文路径样本内容为空"


@pytest.mark.parametrize("rel_path", TEXT_LIKE)
def test_text_samples_contain_chinese(rel_path: str) -> None:
    content = (SAMPLE_ROOT / rel_path).read_text(encoding="utf-8")
    assert any("一" <= ch <= "鿿" for ch in content), f"{rel_path} 不含中文内容"


# --------------------------------------------------------------------------
# TC003 / TC004 —— 重复对与互异
# --------------------------------------------------------------------------


def test_exact_duplicate_pair_same_sha256() -> None:
    first, second = (SAMPLE_ROOT / rel for rel in DUP_PAIR)
    digest = sha256_file(first)
    assert digest, "重复对 sha256 不得为空"
    assert digest == sha256_file(second), "重复对 sha256 不一致（A004）"


def test_non_duplicate_samples_are_distinct() -> None:
    digests = {
        rel: sha256_file(SAMPLE_ROOT / rel)
        for rel in EXPECTED_REL_PATHS
        if rel not in DUP_PAIR
    }
    assert len(set(digests.values())) == len(digests), f"存在非预期重复: {digests}"


# --------------------------------------------------------------------------
# TC005 / TC006 —— 路由覆盖文件的可识别性
# --------------------------------------------------------------------------


def test_pdf_has_pdf_header() -> None:
    head = (SAMPLE_ROOT / "pdf" / "hello.pdf").read_bytes()[:5]
    assert head == b"%PDF-", "hello.pdf 不是合法 PDF 头"


def test_docx_is_zip_with_document_part() -> None:
    path = SAMPLE_ROOT / "office_like" / "最小文档.docx"
    assert path.read_bytes()[:2] == b"PK", "docx 缺少 zip(PK) 头"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert "word/document.xml" in names, f"docx 缺少 word/document.xml: {names}"


# --------------------------------------------------------------------------
# TC007 —— manifest 与磁盘一致
# --------------------------------------------------------------------------


def test_manifest_covers_expected_files() -> None:
    entries = {entry["rel_path"] for entry in load_manifest_entries()}
    assert entries == set(EXPECTED_REL_PATHS), "manifest 清单与 spec §3.1 不一致"


def test_manifest_sha256_matches_disk() -> None:
    for entry in load_manifest_entries():
        rel_path = entry["rel_path"]
        assert rel_path is not None
        target = SAMPLE_ROOT / rel_path
        assert target.is_file(), f"manifest 指向不存在的文件: {rel_path}"
        assert entry["sha256"] == sha256_file(target), f"sha256 与磁盘不一致: {rel_path}"


def test_manifest_marks_duplicate_group() -> None:
    entries = {entry["rel_path"]: entry for entry in load_manifest_entries()}
    groups = [entries[rel]["duplicate_group"] for rel in DUP_PAIR]
    assert groups[0] is not None, "重复对缺少 duplicate_group"
    assert groups[0] == groups[1], "重复对 duplicate_group 不一致"
    others = [
        entry["duplicate_group"]
        for rel, entry in entries.items()
        if rel not in DUP_PAIR
    ]
    assert all(group is None for group in others), "非重复文件不应带 duplicate_group"


# --------------------------------------------------------------------------
# TC008 —— 敏感串扫描
# --------------------------------------------------------------------------


def test_samples_and_manifest_have_no_secrets() -> None:
    for path in sorted(SAMPLE_ROOT.rglob("*")):
        if not path.is_file() or path.suffix in {".docx", ".pdf"}:
            continue  # 二进制样本为程序构造，无文本语义
        text = path.read_text(encoding="utf-8").lower()
        hits = [pattern for pattern in SENSITIVE_PATTERNS if pattern.lower() in text]
        assert not hits, f"{path.relative_to(SAMPLE_ROOT)} 命中敏感模式: {hits}"


# --------------------------------------------------------------------------
# TC009 —— 既有封存 fixture 未被改动
# --------------------------------------------------------------------------


def test_sealed_fixtures_untouched() -> None:
    for path in sorted(SEALED_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        committed = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
        )
        if committed.returncode != 0:
            pytest.skip(f"{rel} 不在 git HEAD 中，跳过比对")
        assert committed.stdout == path.read_bytes(), f"封存 fixture 被改动: {rel}"


def test_script_refuses_to_write_sealed_fixtures() -> None:
    module = load_script_module()
    code = module.main(["--fixture-root", str(SEALED_ROOT), "--skip-sync"])
    assert code == 2, "脚本未拒绝写入封存 fixture 目录"


# --------------------------------------------------------------------------
# TC010 / TC011 —— 脚本生成与幂等
# --------------------------------------------------------------------------


def test_script_generates_fixture_tree(tmp_path: Path) -> None:
    fixture_root = tmp_path / "fx"
    runtime_root = tmp_path / "rt"
    result = run_generate(
        "--fixture-root",
        str(fixture_root),
        "--runtime-root",
        str(runtime_root),
    )
    assert result.returncode == 0, f"脚本失败: {result.stderr}"
    for rel in EXPECTED_REL_PATHS:
        assert (fixture_root / rel).is_file(), f"未生成: {rel}"
        assert (fixture_root / rel).read_bytes() == (SAMPLE_ROOT / rel).read_bytes()
    assert (runtime_root / "manifest.yaml").is_file(), "未同步 manifest 到 runtime"
    assert snapshot(runtime_root) == snapshot(fixture_root), (
        "runtime 副本与 fixture 哈希不一致"
    )


def test_script_is_idempotent(tmp_path: Path) -> None:
    fixture_root = tmp_path / "fx"
    first = run_generate("--fixture-root", str(fixture_root), "--skip-sync")
    assert first.returncode == 0, first.stderr
    before = snapshot(fixture_root)

    second = run_generate("--fixture-root", str(fixture_root), "--skip-sync")
    assert second.returncode == 0, second.stderr

    assert before == snapshot(fixture_root), "脚本不幂等：重跑后字节变化（A009/C5）"


def test_repo_fixtures_match_regeneration(tmp_path: Path) -> None:
    fresh = tmp_path / "fx"
    result = run_generate("--fixture-root", str(fresh), "--skip-sync")
    assert result.returncode == 0, result.stderr
    assert snapshot(fresh) == snapshot(SAMPLE_ROOT), "仓库 fixture 与重新生成结果不一致"


# --------------------------------------------------------------------------
# TC012 / TC013 —— 零数据库、零 parser
# --------------------------------------------------------------------------


def test_script_has_no_database_usage() -> None:
    source = strip_noise(read_source(SCRIPT) + read_source(SH_SCRIPT))
    assert_no_pattern(source, FORBIDDEN_DB_PATTERNS, "create_sample_dataset")


def test_script_has_no_parser_usage() -> None:
    source = strip_noise(read_source(SCRIPT) + read_source(SH_SCRIPT))
    assert_no_pattern(source, FORBIDDEN_PARSER_PATTERNS, "create_sample_dataset")


def test_this_test_has_no_database_or_parser_usage() -> None:
    source = strip_noise(read_source(Path(__file__)))
    assert_no_pattern(source, FORBIDDEN_DB_PATTERNS, "test_sample_dataset")
    assert_no_pattern(source, FORBIDDEN_PARSER_PATTERNS, "test_sample_dataset")


# --------------------------------------------------------------------------
# TC014 —— 路径逃逸防护
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "../raw_vault/escape.txt",
        "../../etc/passwd",
        "/etc/passwd",
        "中文路径/../../escape.txt",
    ],
)
def test_resolve_under_root_rejects_escape(tmp_path: Path, rel_path: str) -> None:
    module = load_script_module()
    with pytest.raises(module.PathEscapeError):
        module.resolve_under_root(tmp_path / "fx", rel_path)


def test_ensure_fixtures_refuses_escaping_manifest(tmp_path: Path) -> None:
    module = load_script_module()
    fixture_root = tmp_path / "fx"
    outside = tmp_path / "escape.txt"
    outside.write_text("must not be written", encoding="utf-8")

    malicious = (
        dict(module.SAMPLE_SPECS[0], rel_path="../escape.txt"),
        dict(module.SAMPLE_SPECS[1], rel_path="中文路径/../../escape.txt"),
    )
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(module, "SAMPLE_SPECS", malicious)
        with pytest.raises(module.PathEscapeError):
            module.ensure_fixtures(fixture_root)
    finally:
        monkeypatch.undo()

    assert outside.read_text(encoding="utf-8") == "must not be written", (
        "逃逸路径被写入，写路径未收敛（A010）"
    )
    assert not any(fixture_root.rglob("*")), "逃逸条目不应在 fixture 树留下文件"
