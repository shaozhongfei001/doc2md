"""Batch entry point: a directory in, one JSON+Markdown per file out.

Design rules, all three learned from the failures of ad-hoc loops:

* **One bad file never ends the run.** A failure is a row in the manifest.
* **Resume is by content hash.** Renaming a file does not re-do work; editing it
  does. The hash is also the output directory name, so two identical files do not
  produce two copies.
* **Visual work is batched.** Loading PP-StructureV3 costs ~20 s; loading it per
  page would dominate the run. All OCR-needing files go to one worker process.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any

from .model import Document
from .readers import apply_visual, read
from .visual import normalise_page
from .writers import write_document

_SKIP_DIRS = {"__pycache__", ".git", ".svn", "node_modules", ".venv", "venv"}


def iter_files(targets: list[str]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for raw in targets:
        path = pathlib.Path(raw)
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if not candidate.is_file():
                    continue
                if any(part in _SKIP_DIRS for part in candidate.parts):
                    continue
                if candidate.name.startswith("~$") or candidate.name.startswith("."):
                    continue
                found.append(candidate)
        elif path.is_file():
            found.append(path)
        else:
            print(f"[batch] skip missing path: {raw}", file=sys.stderr)
    return found


def _load_done(manifest: pathlib.Path) -> set[str]:
    done: set[str] = set()
    if not manifest.exists():
        return done
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("ok"):
            done.add(record["sha256"])
    return done


def _run_visual(
    pending: list[tuple[pathlib.Path, Document]],
    *,
    paddle_python: str,
    device: str,
    max_pages: int,
) -> dict[str, dict[str, Any]]:
    if not pending:
        return {}
    jobs = [
        {"id": doc.sha256[:16], "path": str(path), "max_pages": max_pages}
        for path, doc in pending
    ]
    with tempfile.TemporaryDirectory(prefix="intake-jobs-") as tmp:
        jobs_path = pathlib.Path(tmp) / "jobs.json"
        out_path = pathlib.Path(tmp) / "results.json"
        jobs_path.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
        worker = pathlib.Path(__file__).with_name("paddle_worker.py")
        command = [
            paddle_python, str(worker),
            "--jobs", str(jobs_path),
            "--out", str(out_path),
            "--device", device,
            "--max-pages", str(max_pages),
        ]
        print(f"[batch] visual pass: {len(jobs)} file(s) -> {device}", file=sys.stderr)
        proc = subprocess.run(command, capture_output=True, text=True, timeout=7200)
        if proc.returncode != 0 or not out_path.exists():
            return {
                job["id"]: {
                    "ok": False,
                    "error": (proc.stderr or "worker produced no output")[-500:],
                    "pages": [],
                }
                for job in jobs
            }
        return json.loads(out_path.read_text(encoding="utf-8"))["results"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.extensions.intake",
        description="Parse a directory of mixed documents into JSON + Markdown.",
    )
    parser.add_argument("targets", nargs="+", help="files or directories")
    parser.add_argument("--out", required=True, help="output root")
    parser.add_argument("--manifest", default=None, help="defaults to <out>/manifest.jsonl")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=20,
                        help="visual engine page cap per document (reported when hit)")
    parser.add_argument("--device", default="gpu")
    parser.add_argument("--paddle-python", default=os.environ.get("PADDLE_PYTHON", ""))
    parser.add_argument("--no-visual", action="store_true")
    parser.add_argument(
        "--deep", action="store_true",
        help="also run the visual engine on text-layer PDFs, for tables",
    )
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args(argv)

    out_root = pathlib.Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = pathlib.Path(args.manifest) if args.manifest else out_root / "manifest.jsonl"
    done = set() if args.no_resume else _load_done(manifest_path)

    files = iter_files(args.targets)
    if args.limit:
        files = files[: args.limit]
    print(f"[batch] {len(files)} candidate file(s)", file=sys.stderr)

    stats = {"ok": 0, "failed": 0, "skipped": 0, "ocr": 0, "tables": 0}
    seen: set[str] = set()
    queue: list[tuple[pathlib.Path, Document]] = []
    started = time.time()

    with manifest_path.open("a", encoding="utf-8") as manifest:
        for number, path in enumerate(files, 1):
            try:
                doc = read(path, deep=args.deep)
            except Exception as exc:  # noqa: BLE001 - I/O failure on one file
                record = {
                    "path": str(path), "sha256": "", "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
                manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
                manifest.flush()
                stats["failed"] += 1
                print(f"[{number}/{len(files)}] FAIL {path} {record['error']}", file=sys.stderr)
                continue

            if doc.sha256 in done or doc.sha256 in seen:
                stats["skipped"] += 1
                continue
            seen.add(doc.sha256)

            if doc.meta.get("needs_ocr") or doc.meta.get("needs_visual"):
                if args.paddle_python and not args.no_visual:
                    queue.append((path, doc))
                    continue  # written after the visual pass, in the loop below
                if args.no_visual:
                    doc.add_issue(
                        "VISUAL_DISABLED",
                        "page needs the visual engine and --no-visual was set",
                        severity="ERROR" if doc.meta.get("needs_ocr") else "WARNING",
                    )
                else:
                    doc.add_issue(
                        "OCR_INTERPRETER_MISSING",
                        "set --paddle-python (or PADDLE_PYTHON) to a venv with paddleocr",
                        severity="ERROR",
                    )
                doc.meta["needs_ocr"] = False  # nothing further will fill it in
                doc.meta["needs_visual"] = False

            stats["tables"] += len(doc.tables)
            stats["ok"] += 1
            write_document(doc, out_root)
            manifest.write(json.dumps(
                {"path": str(path), "sha256": doc.sha256, "ok": True,
                 "kind": doc.kind, "engine": doc.engine,
                 "tables": len(doc.tables), "issues": len(doc.issues)},
                ensure_ascii=False) + "\n")
            manifest.flush()
            print(f"[{number}/{len(files)}] ok {path.name} tables={len(doc.tables)}",
                  file=sys.stderr)

    visual = _run_visual(
        queue,
        paddle_python=args.paddle_python or "python",
        device=args.device,
        max_pages=args.max_pages,
    )

    with manifest_path.open("a", encoding="utf-8") as manifest:
        for path, doc in queue:
            stats["ocr"] += 1
            result = visual.get(doc.sha256[:16]) or {}
            if not result.get("ok"):
                doc.add_issue(
                    "VISUAL_ENGINE_FAILED",
                    str(result.get("error") or "worker returned no result")[:300],
                    severity="ERROR",
                )
            else:
                pages = [
                    normalise_page(page, page_no=index + 1)
                    for index, page in enumerate(result.get("pages") or [])
                ]
                apply_visual(
                    path,
                    doc,
                    {"pages": pages, "issues": []},
                    tables_only=bool(doc.meta.get("visual_tables_only")),
                )
                if result.get("truncated"):
                    scope = (
                        "visual pass only; the text layer covers every page"
                        if doc.meta.get("visual_tables_only")
                        else "content beyond this page was not read"
                    )
                    doc.add_issue(
                        "PAGES_TRUNCATED",
                        f"{result.get('page_count')} pages, first {args.max_pages} "
                        f"processed ({scope})",
                        severity="WARNING",
                        detail={"pages_total": result.get("page_count")},
                    )
            stats["tables"] += len(doc.tables)
            stats["ok"] += 1
            write_document(doc, out_root)
            manifest.write(json.dumps(
                {"path": str(path), "sha256": doc.sha256, "ok": True,
                 "kind": doc.kind, "engine": doc.engine,
                 "tables": len(doc.tables), "issues": len(doc.issues)},
                ensure_ascii=False) + "\n")
            manifest.flush()
            print(f"[{path.name}] ok tables={len(doc.tables)} issues={len(doc.issues)}",
                  file=sys.stderr)

    elapsed = round(time.time() - started, 1)
    print(json.dumps({**stats, "files": len(files), "seconds": elapsed,
                      "out": str(out_root)}, ensure_ascii=False))
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
