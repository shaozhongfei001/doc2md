"""Run PaddleOCR/PP-StructureV3 in its own interpreter and dump raw results.

Why a separate process: the OCR stack pins numpy/protobuf/opencv versions that do
not match the extension test environment. Importing it here would move the test
baseline without changing a single test. This worker is invoked with the OCR
interpreter and communicates through JSON files, so the two environments never
share a ``sys.path``.

It deliberately does **no** interpretation: it returns the engine's own ``res``
mapping per page. Turning boxes into cells is done by
``adapters.paddle_structure.normalise_response``, which already has the geometry
verification and the two known ``cell_box_list`` shapes.

Usage::

    python paddle_worker.py --jobs jobs.json --out results.json [--device gpu]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import time


def _page_count(pdf: pathlib.Path) -> int:
    try:
        proc = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, timeout=60, check=False)
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
    except Exception:  # noqa: BLE001 - a missing count only changes the strategy
        pass
    return 0


def _render_first_pages(pdf: pathlib.Path, limit: int, workdir: pathlib.Path) -> list[pathlib.Path]:
    target = workdir / "pages"
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["pdftoppm", "-f", "1", "-l", str(limit), "-r", "150", "-png", str(pdf), str(target / "p")],
        check=True, capture_output=True, timeout=1800,
    )
    return sorted(target.glob("p*.png"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--device", default="gpu")
    parser.add_argument("--max-pages", type=int, default=20)
    args = parser.parse_args()

    jobs = json.loads(pathlib.Path(args.jobs).read_text(encoding="utf-8"))
    results: dict[str, dict] = {}

    from paddleocr import PPStructureV3

    started = time.time()
    pipe = PPStructureV3(
        device=args.device,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    load_seconds = round(time.time() - started, 1)

    for job in jobs:
        identifier = job["id"]
        path = pathlib.Path(job["path"])
        raw_limit = job.get("max_pages")
        limit = int(args.max_pages if raw_limit is None else raw_limit)
        if limit <= 0:
            limit = 0  # 0 means "no cap"; resolved against the real page count below
        record: dict = {"id": identifier, "ok": False, "pages": [], "error": None}
        began = time.time()
        try:
            if path.suffix.lower() == ".pdf":
                total = _page_count(path)
                record["page_count"] = total
                if limit <= 0:
                    limit = total or 10**9
                if total and total > limit:
                    with tempfile.TemporaryDirectory(prefix="intake-ocr-") as tmp:
                        images = _render_first_pages(path, limit, pathlib.Path(tmp))
                        for image in images:
                            for item in pipe.predict(str(image)):
                                record["pages"].append(item.json["res"])
                    record["truncated"] = True
                    record["pages_total"] = total
                else:
                    for item in pipe.predict(str(path)):
                        record["pages"].append(item.json["res"])
            else:
                for item in pipe.predict(str(path)):
                    record["pages"].append(item.json["res"])
            record["ok"] = True
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
            record["error"] = f"{type(exc).__name__}: {exc}"[:500]
        record["seconds"] = round(time.time() - began, 2)
        results[identifier] = record
        print(
            f"[paddle] {identifier} ok={record['ok']} pages={len(record['pages'])} "
            f"{record['seconds']}s",
            file=sys.stderr,
        )

    payload = {"load_seconds": load_seconds, "device": args.device, "results": results}
    pathlib.Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
