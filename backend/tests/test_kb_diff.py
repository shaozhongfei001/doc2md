"""P1-L1 · T-D2MD-01 反例与断言（开发前锁定，见 docs/v2-tasks/loops/P1-L1-PLAN.md §二）。

9 条核心用例：
  A1 精确分集 / A2 TOMBSTONE+重放幂等 / A3 错 parser_fingerprint
  R-1 只有不变文件 / R-2 删除后重扫 / R-3 错 fingerprint 全标 changed
  R-4 dry-run 零写入 / R-5 清单损坏抛明确错误 / R-6 同 sha256 不同 file_id
外加 1 条回归哨兵：既有 20 个 CLI 命令未因本 loop 改变。

语料（backend/tests/fixtures/kb_diff_corpus/）：
  上轮 manifest ACTIVE = {F1, F2(旧hash), F3, F4}
  磁盘              = {F1, F2(新hash), F4, F5}       # F3 删除、F5 新增
  F1 与 F4 磁盘字节完全相同（同 sha256、不同 file_id）—— R-6 用例
  no_change/ 为 R-1/A3 的独立 KB 根（磁盘与 ACTIVE 完全一致，无增删）
  corrupt/   为 R-5 的损坏清单
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.kb_diff import (
    KBManifestCorruptError,
    build_next_manifest,
    compute_kb_diff,
    compute_set_digest,
    load_kb_manifest,
)

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent
FIXTURES = TESTS_DIR / "fixtures" / "kb_diff_corpus"
CONFIG_PATH = REPO_ROOT / "config" / "app.yaml"
FIXED_TS = "2026-11-08T00:00:00+00:00"

# 采集自改动前 `app.registered_commands`（20 个，见证据日志基线段落）。
BASELINE_COMMANDS = [
    "build-curated-project", "build-evidence-chain", "build-parse-queue",
    "check-parse-quality", "copy-to-vault", "govern-duplicates",
    "list-parse-jobs", "list-parse-results", "list-parsed-artifacts", "parse",
    "parse-images", "parse-markitdown", "parse-mineru-pdf",
    "reconcile-parsed-artifacts", "register-parse-report", "route-parsers",
    "scan", "search-kb", "show-parse-job", "summarize-parse-quality",
]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "app.cli.main", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _scan_diff_cli_args(scan_path: Path, *extra: str) -> list[str]:
    return [
        "scan-diff",
        "--kb", "kb_9",
        "--path", str(scan_path),
        "--config", str(CONFIG_PATH),
        *extra,
    ]


# ---------------------------------------------------------------------------
# A1 · 精确分集
# ---------------------------------------------------------------------------
def test_a1_exact_partition() -> None:
    manifest = load_kb_manifest(FIXTURES / "kb-manifest.json")
    assert manifest is not None
    result = compute_kb_diff(
        kb="kb_9",
        scan_path=FIXTURES,
        previous_manifest=manifest,
        parser_fingerprint="fp-v1",
    )
    assert result.added == ["F5"]
    assert result.changed == ["F2"]
    assert result.removed == ["F3"]
    assert result.unchanged == ["F1", "F4"]
    assert result.reason == "FILE_SET_CHANGED"
    # cursor 单调推进（相对上轮基线），且由 ACTIVE 集合内容寻址
    assert result.cursor != manifest["cursor"]
    assert result.set_digest != manifest["set_digest"]
    assert result.cursor == "cursor-" + result.set_digest
    # set_digest 不受条目枚举顺序影响
    triples = [
        (e["file_id"], e["sha256"], e["read_set_digest"])
        for e in result.active_entries
    ]
    assert len(triples) == 4
    assert compute_set_digest(triples) == result.set_digest
    assert compute_set_digest(list(reversed(triples))) == result.set_digest


# ---------------------------------------------------------------------------
# A2 · TOMBSTONE 与重放幂等
# ---------------------------------------------------------------------------
def test_a2_tombstone_replay_idempotent() -> None:
    manifest = load_kb_manifest(FIXTURES / "kb-manifest.json")
    assert manifest is not None
    first = compute_kb_diff(
        kb="kb_9",
        scan_path=FIXTURES,
        previous_manifest=manifest,
        parser_fingerprint="fp-v1",
    )
    assert first.removed == ["F3"]

    next_manifest = build_next_manifest(
        first, manifest, parser_fingerprint="fp-v1", generated_at=FIXED_TS
    )
    # 删除不物理删条目：F3 保留为 TOMBSTONE，且不再属于 ACTIVE
    tombstones = [
        e for e in next_manifest["entries"] if e.get("status") == "TOMBSTONE"
    ]
    assert any(e["file_id"] == "F3" for e in tombstones)
    assert any(
        e.get("reason") == "SOURCE_REMOVED" and e.get("revoked_at")
        for e in tombstones
        if e["file_id"] == "F3"
    )
    active_ids = {
        e["file_id"] for e in next_manifest["entries"] if e.get("status") == "ACTIVE"
    }
    assert "F3" not in active_ids
    assert "F5" in active_ids
    # set_digest 只覆盖 ACTIVE：与被撤权的 F3 无关
    assert len(tombstones) >= 1

    # 重放：同一份磁盘语料 + 写回后的清单
    replay = compute_kb_diff(
        kb="kb_9",
        scan_path=FIXTURES,
        previous_manifest=next_manifest,
        parser_fingerprint="fp-v1",
    )
    assert replay.added == []
    assert replay.changed == []
    assert replay.removed == []  # 不产生第二个 removed
    assert replay.reason == "NO_CHANGE"
    assert replay.cursor == first.cursor
    assert replay.set_digest == first.set_digest


# ---------------------------------------------------------------------------
# A3 · 反例：错 parser_fingerprint
# ---------------------------------------------------------------------------
def test_a3_wrong_parser_fingerprint() -> None:
    nc = FIXTURES / "no_change"
    manifest = load_kb_manifest(nc / "kb-manifest.json")
    assert manifest is not None
    result = compute_kb_diff(
        kb="kb_9",
        scan_path=nc,
        previous_manifest=manifest,
        parser_fingerprint="WRONG-FP",
    )
    assert result.reason == "PARSER_FINGERPRINT_CHANGED"
    assert result.added == []
    assert result.removed == []
    assert result.changed == ["N1", "N2"]  # 所有 ACTIVE 都标 changed
    assert result.unchanged == []


# ---------------------------------------------------------------------------
# R-1 · 夹具只给 2 个不变文件
# ---------------------------------------------------------------------------
def test_r1_no_change_when_only_unchanged_files() -> None:
    nc = FIXTURES / "no_change"
    manifest = load_kb_manifest(nc / "kb-manifest.json")
    assert manifest is not None
    result = compute_kb_diff(
        kb="kb_9",
        scan_path=nc,
        previous_manifest=manifest,
        parser_fingerprint="fp-v1",
    )
    assert result.reason == "NO_CHANGE"
    assert result.added == []
    assert result.changed == []
    assert result.removed == []
    assert result.unchanged == ["N1", "N2"]


# ---------------------------------------------------------------------------
# R-2 · 删除源文件后再次扫描同一目录
# ---------------------------------------------------------------------------
def test_r2_rescan_after_removal_no_second_removed() -> None:
    manifest = load_kb_manifest(FIXTURES / "kb-manifest.json")
    assert manifest is not None
    assert not (FIXTURES / "F3").exists()  # F3 已不在磁盘
    first = compute_kb_diff(
        kb="kb_9",
        scan_path=FIXTURES,
        previous_manifest=manifest,
        parser_fingerprint="fp-v1",
    )
    assert first.removed == ["F3"]
    next_manifest = build_next_manifest(
        first, manifest, parser_fingerprint="fp-v1", generated_at=FIXED_TS
    )
    second = compute_kb_diff(
        kb="kb_9",
        scan_path=FIXTURES,
        previous_manifest=next_manifest,
        parser_fingerprint="fp-v1",
    )
    assert second.removed == []
    assert not any(
        e["file_id"] == "F3" and e.get("status") == "ACTIVE"
        for e in next_manifest["entries"]
    )


# ---------------------------------------------------------------------------
# R-3 · 错 parser_fingerprint → 所有 ACTIVE 标 changed（独立 3 文件语料）
# ---------------------------------------------------------------------------
def test_r3_wrong_fingerprint_marks_all_active(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    contents = {"A": b"aaa\n", "B": b"bbb\n", "C": b"ccc\n"}
    entries = []
    for file_id, data in contents.items():
        (corpus / file_id).write_bytes(data)
        entries.append(
            {
                "file_id": file_id,
                "sha256": hashlib.sha256(data).hexdigest(),
                "source_path": file_id,
                "read_set_digest": "0" * 64,
                "status": "ACTIVE",
            }
        )
    manifest = {
        "schema": "llmke.doc2md.kb-manifest/1",
        "kb": "kb_9",
        "cursor": "cursor-baseline",
        "set_digest": "0" * 64,
        "generated_at": FIXED_TS,
        "parser_fingerprint": "fp-v1",
        "entries": entries,
    }
    result = compute_kb_diff(
        kb="kb_9",
        scan_path=corpus,
        previous_manifest=manifest,
        parser_fingerprint="WRONG",
    )
    assert result.reason == "PARSER_FINGERPRINT_CHANGED"
    assert result.added == []
    assert result.removed == []
    assert set(result.changed) == set(contents)  # 无遗漏
    assert result.unchanged == []


# ---------------------------------------------------------------------------
# R-4 · scan-diff --dry-run 零写入
# ---------------------------------------------------------------------------
def test_r4_cli_dry_run_zero_writes() -> None:
    manifest_path = FIXTURES / "kb-manifest.json"
    before_bytes = manifest_path.read_bytes()
    before_mtime = manifest_path.stat().st_mtime_ns
    before_tree = sorted(
        str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*")
    )

    proc = _run_cli(_scan_diff_cli_args(FIXTURES, "--dry-run"))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["reason"] == "FILE_SET_CHANGED"
    assert payload["added"] == ["F5"]
    assert payload["removed"] == ["F3"]
    assert payload["changed"] == ["F2"]
    assert payload["unchanged"] == ["F1", "F4"]

    assert manifest_path.read_bytes() == before_bytes
    assert manifest_path.stat().st_mtime_ns == before_mtime
    assert sorted(
        str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*")
    ) == before_tree  # 无报告落盘


# ---------------------------------------------------------------------------
# R-5 · manifest JSON 损坏 → 抛明确错误，禁止静默按空库重建
# ---------------------------------------------------------------------------
def test_r5_corrupt_manifest_raises_clear_error(tmp_path: Path) -> None:
    corrupt = FIXTURES / "corrupt" / "kb-manifest.json"
    with pytest.raises(KBManifestCorruptError) as excinfo:
        load_kb_manifest(corrupt)
    assert "KB_MANIFEST_CORRUPT" in str(excinfo.value)

    # 合法 JSON 但结构不符（缺 entries）也必须抛错，不得按空库处理
    struct_bad = tmp_path / "kb-manifest.json"
    struct_bad.write_text(
        json.dumps({"schema": "llmke.doc2md.kb-manifest/1"}), encoding="utf-8"
    )
    with pytest.raises(KBManifestCorruptError):
        load_kb_manifest(struct_bad)

    # CLI 侧：明确错误码 + 非零退出，且不得产出正常 DiffResult
    proc = _run_cli(
        _scan_diff_cli_args(
            FIXTURES / "no_change", "--cursor", str(corrupt), "--dry-run"
        )
    )
    assert proc.returncode != 0
    assert "KB_MANIFEST_CORRUPT" in (proc.stdout + proc.stderr)
    assert '"added"' not in proc.stdout


# ---------------------------------------------------------------------------
# R-6 · 同 sha256 不同 file_id 不误判为重复
# ---------------------------------------------------------------------------
def test_r6_same_sha256_distinct_file_ids() -> None:
    assert (FIXTURES / "F1").read_bytes() == (FIXTURES / "F4").read_bytes()
    manifest = load_kb_manifest(FIXTURES / "kb-manifest.json")
    assert manifest is not None
    result = compute_kb_diff(
        kb="kb_9",
        scan_path=FIXTURES,
        previous_manifest=manifest,
        parser_fingerprint="fp-v1",
    )
    ids = [e["file_id"] for e in result.active_entries]
    assert "F1" in ids and "F4" in ids  # 两个 file_id 都完整登记
    same_sha = hashlib.sha256((FIXTURES / "F1").read_bytes()).hexdigest()
    assert len([e for e in result.active_entries if e["sha256"] == same_sha]) == 2
    assert result.unchanged == ["F1", "F4"]


# ---------------------------------------------------------------------------
# 回归哨兵：既有 20 个命令仍在，且只新增了 scan-diff
# ---------------------------------------------------------------------------
def test_existing_commands_unchanged() -> None:
    from app.cli.main import app

    names = sorted(
        c.name or c.callback.__name__ for c in app.registered_commands
    )
    assert set(BASELINE_COMMANDS) <= set(names)
    assert set(names) - set(BASELINE_COMMANDS) == {"scan-diff"}
