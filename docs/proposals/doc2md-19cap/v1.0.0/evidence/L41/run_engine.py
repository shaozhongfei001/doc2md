"""Run PP-StructureV3 over the sampled pages and keep a compact, replayable response.

The heavy engine runs in its own venv (paddleocr 3.7.0); the adapter under test runs
in the project venv. Keeping the captured JSON makes the evaluation reproducible
without the GPU.
"""

import json
import pathlib
import time

import paddle
from paddleocr import PPStructureV3

OUT = pathlib.Path("/tmp/l41out")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(pathlib.Path("/tmp/l41pages/manifest.json").read_text(encoding="utf-8"))

    started = time.time()
    pipe = PPStructureV3(device="gpu", use_doc_orientation_classify=False, use_doc_unwarping=False)
    print(f"loaded in {time.time() - started:.1f}s", flush=True)

    records = []
    for entry in manifest:
        stem = pathlib.Path(entry["image"]).stem
        target = OUT / f"{stem}.json"
        if target.exists():
            records.append(json.loads(target.read_text(encoding="utf-8"))["_meta"])
            continue
        t0 = time.time()
        response = pipe.predict(entry["image"])[0].json["res"]
        payload = {
            "width": response.get("width"),
            "height": response.get("height"),
            "table_res_list": response.get("table_res_list") or [],
            "_meta": {**entry, "seconds": round(time.time() - t0, 2),
                      "n_tables": len(response.get("table_res_list") or [])},
        }
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        records.append(payload["_meta"])
        print(f"{stem}: {payload['_meta']['n_tables']} table(s) in {payload['_meta']['seconds']}s",
              flush=True)
        # The allocator keeps a pool per process; 20 dense pages exhaust 6 GB without this.
        paddle.device.cuda.empty_cache()

    (OUT / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print("pages", len(records), "total tables", sum(r["n_tables"] for r in records))


if __name__ == "__main__":
    main()
