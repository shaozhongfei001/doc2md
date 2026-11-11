"""P1-L2 · T-D2MD-02 反例与断言（P1-L2-PLAN.md §二）。

B1 --dry-run 零写入 / B2 失败项保留带 fail_reason / B3 缺路由结果拒绝入队
R-1 空 diff / R-2 removed 不入队且不复活 / R-3 同 sha256 去重
R-4 两次 dry-run 逐字节相同 / R-5 parser_adapter_version 不一致重排队
R-6 中断重放幂等 / R-7（L1 结转）仅内容变 → CONTENT_CHANGED
A3 优先级哨兵（L1 结转）/ 既有命令回归哨兵（L1 结转）
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from app.services.kb_diff import compute_kb_diff, load_kb_manifest
from app.services.parse_queue import (
    REASON_ROUTE_MISSING,
    REASON_ROUTE_UNSUPPORTED,
    STATUS_DONE,
    STATUS_FAILED,
    BuildQueueResult,
    ParseOutcome,
    QueueItem,
    RouteInfo,
    build_parse_queue,
    consume_queue,
)

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent
FIXTURES = TESTS_DIR / "fixtures" / "kb_diff_corpus"  # L1 夹具，B1 的输入
CONFIG_PATH = REPO_ROOT / "config" / "app.yaml"

# L1 收口后的命令清单（20 个既有命令名；L2 只替换其中 2 个实现，名字不变）
BASELINE_20 = [
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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(file_id: str, data: bytes, version: str = "kb_diff_v1",
           status: str = "ACTIVE") -> dict:
    return {
        "file_id": file_id,
        "sha256": _sha(data),
        "source_path": file_id,
        "read_set_digest": "0" * 64,
        "status": status,
        "parser_adapter_version": version,
    }


def _corpus(tmp_path: Path, disk: dict[str, bytes], manifest_entries: list[dict],
            fp: str = "fp-v1") -> tuple[Path, dict, object]:
    root = tmp_path / "corpus"
    root.mkdir()
    for name, data in disk.items():
        (root / name).write_bytes(data)
    manifest = {
        "schema": "llmke.doc2md.kb-manifest/1",
        "kb": "kb_9",
        "cursor": "cursor-base",
        "set_digest": "0" * 64,
        "generated_at": "2026-11-08T00:00:00+00:00",
        "parser_fingerprint": fp,
        "entries": manifest_entries,
    }
    (root / "kb-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    prev = load_kb_manifest(root / "kb-manifest.json")
    diff = compute_kb_diff(
        kb="kb_9", scan_path=root, previous_manifest=prev, parser_fingerprint=fp
    )
    return root, prev, diff


def _routes(diff, *, missing: tuple[str, ...] = (), override: dict | None = None,
            route_type: str = "TEXT_OR_MARKDOWN",
            version: str = "kb_diff_v1") -> dict[str, RouteInfo | None]:
    out: dict[str, RouteInfo | None] = {
        e["sha256"]: RouteInfo(route_type, version)
        for e in diff.active_entries
    }
    for sha in missing:
        out.pop(sha, None)
    if override:
        out.update(override)
    return out


class RecordingRunner:
    """测试替身：只记录调用，不真正解析（不引入第二套解析者）。"""

    def __init__(self, fail_shas: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_shas = fail_shas or set()

    def parse(self, item: QueueItem) -> ParseOutcome:
        self.calls.append(item.sha256)
        return ParseOutcome(ok=item.sha256 not in self.fail_shas, message="stub")


# ---------------------------------------------------------------------------
# B1 · --dry-run 零写入
# ---------------------------------------------------------------------------
def test_b1_dry_run_zero_writes() -> None:
    manifest = FIXTURES / "kb-manifest.json"
    before = manifest.read_bytes()
    before_mtime = manifest.stat().st_mtime_ns
    tree_before = sorted(str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*"))

    proc = _run_cli([
        "build-parse-queue", "--kb", "kb_9", "--config", str(CONFIG_PATH), "--dry-run",
    ])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is True
    assert payload["diff_reason"] == "FILE_SET_CHANGED"
    fids = sorted(fid for item in payload["items"] for fid in item["file_ids"])
    assert fids == ["F2", "F5"]  # 仅 added + changed
    assert payload["rejected"] == []
    for item in payload["items"]:
        assert len(item["sha256"]) == 64
        assert item["route_type"] and item["route_type"] != "STUB"
        assert item["parser_adapter_version"]
    # 零写入
    assert manifest.read_bytes() == before
    assert manifest.stat().st_mtime_ns == before_mtime
    assert sorted(str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*")) == tree_before
    assert not (FIXTURES / "parse_queue.json").exists()


# ---------------------------------------------------------------------------
# B2 · 失败项保留且带 fail_reason
# ---------------------------------------------------------------------------
def test_b2_failed_items_retained_with_fail_reason(tmp_path: Path) -> None:
    good, bad = b"GOOD\n", b"BAD\n"
    root, prev, diff = _corpus(tmp_path, {"G.md": good, "B.md": bad}, [])
    good_sha, bad_sha = _sha(good), _sha(bad)
    routes = _routes(diff, override={bad_sha: RouteInfo("UNSUPPORTED", "kb_diff_v1")})
    built = build_parse_queue(kb="kb_9", diff=diff, previous_manifest=prev, routes=routes)

    runner = RecordingRunner(fail_shas={bad_sha})
    r1 = consume_queue(queue=built, parse_runner=runner)
    assert good_sha in r1.parsed
    assert bad_sha in r1.failed
    assert good_sha in runner.calls  # 正常项进入解析
    failed_items = [i for i in r1.queue.items if i.sha256 == bad_sha]
    assert failed_items
    assert failed_items[0].status == STATUS_FAILED
    assert failed_items[0].fail_reason == REASON_ROUTE_UNSUPPORTED
    assert any(i.sha256 == good_sha and i.status == STATUS_DONE for i in r1.queue.items)

    # 再次执行：异常项仍留在队列且可被重试，不静默丢弃、不阻断其它项
    runner2 = RecordingRunner(fail_shas={bad_sha})
    r2 = consume_queue(queue=r1.queue, parse_runner=runner2)
    assert bad_sha in runner2.calls
    assert any(i.sha256 == bad_sha for i in r2.queue.items)


# ---------------------------------------------------------------------------
# B3 · 缺路由结果的 sha256 不得进入 parse 队列
# ---------------------------------------------------------------------------
def test_b3_missing_route_rejected(tmp_path: Path) -> None:
    data = b"A\n"
    root, prev, diff = _corpus(tmp_path, {"A.md": data}, [])
    sha = _sha(data)
    routes = _routes(diff, missing=(sha,))
    built = build_parse_queue(kb="kb_9", diff=diff, previous_manifest=prev, routes=routes)
    assert built.items == []
    assert any(
        r["sha256"] == sha and r["reason"] == REASON_ROUTE_MISSING
        for r in built.rejected
    )
    runner = RecordingRunner()
    result = consume_queue(queue=built, parse_runner=runner)
    assert sha not in result.parsed
    assert sha not in [i.sha256 for i in result.queue.items]


# ---------------------------------------------------------------------------
# R-1 · DiffResult 四集合全空
# ---------------------------------------------------------------------------
def test_r1_empty_diff_no_parse(tmp_path: Path) -> None:
    data = b"A\n"
    root, prev, diff = _corpus(tmp_path, {"A.md": data}, [_entry("A.md", data)])
    assert diff.reason == "NO_CHANGE"
    built = build_parse_queue(
        kb="kb_9", diff=diff, previous_manifest=prev, routes=_routes(diff)
    )
    assert built.items == []
    assert built.rejected == []
    runner = RecordingRunner()
    result = consume_queue(queue=built, parse_runner=runner)
    assert runner.calls == []
    assert result.parsed == []
    proc = _run_cli([
        "parse", "--kb", "kb_9", "--config", str(CONFIG_PATH),
        "--path", str(root), "--dry-run",
    ])
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# R-2 · removed / TOMBSTONE 不进入解析队列，且不复活
# ---------------------------------------------------------------------------
def test_r2_removed_not_queued_and_not_revived() -> None:
    prev = load_kb_manifest(FIXTURES / "kb-manifest.json")
    assert prev is not None
    diff = compute_kb_diff(
        kb="kb_9", scan_path=FIXTURES, previous_manifest=prev, parser_fingerprint="fp-v1"
    )
    assert diff.removed == ["F3"]
    built = build_parse_queue(
        kb="kb_9", diff=diff, previous_manifest=prev, routes=_routes(diff)
    )
    all_fids = [f for i in built.items for f in i.file_ids]
    assert "F3" not in all_fids

    prev2 = json.loads(json.dumps(prev))
    prev2["entries"].append({
        "file_id": "OLD_TOMB", "sha256": "0" * 64, "status": "TOMBSTONE",
        "revoked_at": "2026-11-08T00:00:00+00:00", "reason": "SOURCE_REMOVED",
    })
    diff2 = compute_kb_diff(
        kb="kb_9", scan_path=FIXTURES, previous_manifest=prev2, parser_fingerprint="fp-v1"
    )
    built2 = build_parse_queue(
        kb="kb_9", diff=diff2, previous_manifest=prev2, routes=_routes(diff2)
    )
    assert "OLD_TOMB" not in [f for i in built2.items for f in i.file_ids]


# ---------------------------------------------------------------------------
# R-3 · 同一 sha256 重复出现 → 队列去重（按内容寻址身份）
# ---------------------------------------------------------------------------
def test_r3_same_sha256_dedup(tmp_path: Path) -> None:
    same = b"SAME\n"
    root, prev, diff = _corpus(tmp_path, {"A.md": same, "B.md": same}, [])
    assert diff.added == ["A.md", "B.md"]
    built = build_parse_queue(
        kb="kb_9", diff=diff, previous_manifest=prev, routes=_routes(diff)
    )
    assert len(built.items) == 1
    assert sorted(built.items[0].file_ids) == ["A.md", "B.md"]


# ---------------------------------------------------------------------------
# R-4 · --dry-run 连续两次逐字节相同
# ---------------------------------------------------------------------------
def test_r4_two_dry_runs_identical() -> None:
    args = [
        "build-parse-queue", "--kb", "kb_9", "--config", str(CONFIG_PATH), "--dry-run",
    ]
    a = _run_cli(args)
    b = _run_cli(args)
    assert a.returncode == 0 and b.returncode == 0
    assert a.stdout.encode() == b.stdout.encode()  # 逐字节
    assert a.stdout == b.stdout


# ---------------------------------------------------------------------------
# R-5 · parser_adapter_version 与 manifest 不一致 → 标 changed 重排队
# ---------------------------------------------------------------------------
def test_r5_parser_adapter_version_mismatch_requeues(tmp_path: Path) -> None:
    data = b"A\n"
    root, prev, diff = _corpus(
        tmp_path, {"A.md": data}, [_entry("A.md", data, version="OLD_VERSION")]
    )
    assert diff.reason == "NO_CHANGE"
    built = build_parse_queue(
        kb="kb_9", diff=diff, previous_manifest=prev, routes=_routes(diff),
        parser_adapter_version="kb_diff_v1",
    )
    assert len(built.items) == 1
    assert built.items[0].diff_reason == "PARSER_VERSION_CHANGED"


# ---------------------------------------------------------------------------
# R-6 · 解析中断后重放：已完成项幂等跳过，未完成项保留
# ---------------------------------------------------------------------------
def test_r6_interrupt_replay_idempotent(tmp_path: Path) -> None:
    a, b = b"A\n", b"B\n"
    root, prev, diff = _corpus(tmp_path, {"A.md": a, "B.md": b}, [])
    sha_a, sha_b = _sha(a), _sha(b)
    built = build_parse_queue(
        kb="kb_9", diff=diff, previous_manifest=prev, routes=_routes(diff)
    )
    r1 = consume_queue(queue=built, parse_runner=RecordingRunner(fail_shas={sha_b}))
    assert sha_a in r1.parsed and sha_b in r1.failed
    assert [i.status for i in r1.queue.items if i.sha256 == sha_a] == [STATUS_DONE]
    assert [i.status for i in r1.queue.items if i.sha256 == sha_b] == [STATUS_FAILED]

    runner2 = RecordingRunner()
    r2 = consume_queue(
        queue=r1.queue, parse_runner=runner2, skip_checker=lambda s: s == sha_a
    )
    assert sha_a in r2.skipped and sha_a not in runner2.calls  # 已完成幂等跳过
    assert sha_b in runner2.calls  # 未完成项保留并重试


# ---------------------------------------------------------------------------
# R-7 · L1 覆盖缺口回归：仅内容变 → CONTENT_CHANGED（不改 kb_diff.py 语义）
# ---------------------------------------------------------------------------
def test_r7_content_changed_regression(tmp_path: Path) -> None:
    root, prev, diff = _corpus(
        tmp_path, {"A.md": b"NEW\n"}, [_entry("A.md", b"OLD\n")]
    )
    assert diff.reason == "CONTENT_CHANGED"
    assert diff.added == [] and diff.removed == []
    assert diff.changed == ["A.md"]


# ---------------------------------------------------------------------------
# A3 优先级哨兵（L1 结转）：主夹具 + 错 fp 仍 FILE_SET_CHANGED
# ---------------------------------------------------------------------------
def test_a3_priority_sentinel() -> None:
    prev = load_kb_manifest(FIXTURES / "kb-manifest.json")
    assert prev is not None
    diff = compute_kb_diff(
        kb="kb_9", scan_path=FIXTURES, previous_manifest=prev,
        parser_fingerprint="WRONG-FP",
    )
    assert diff.reason == "FILE_SET_CHANGED"
    assert diff.added == ["F5"]
    assert diff.changed == ["F2"]
    assert diff.removed == ["F3"]
    assert diff.unchanged == ["F1", "F4"]


# ---------------------------------------------------------------------------
# 回归哨兵：既有命令名不变（L2 只替换 2 个 placeholder 的实现）
# ---------------------------------------------------------------------------
def test_existing_commands_unchanged() -> None:
    from app.cli.main import app

    names = sorted(c.name or c.callback.__name__ for c in app.registered_commands)
    assert set(BASELINE_20) <= set(names)
    assert set(names) - set(BASELINE_20) == {"scan-diff"}
