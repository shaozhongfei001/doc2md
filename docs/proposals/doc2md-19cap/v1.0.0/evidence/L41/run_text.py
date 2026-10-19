"""Text-side statistics on the same 20 real pages (English illustrated books).

Purpose is narrow: show what scores look like on real, dense, non-synthetic pages.
It cannot calibrate the Chinese financial threshold — different script, different
layout, different degradation.
"""

import json
import pathlib
import statistics

from paddleocr import PaddleOCR

OUT = pathlib.Path("/tmp/l41out")


def main() -> None:
    manifest = json.loads(pathlib.Path("/tmp/l41pages/manifest.json").read_text(encoding="utf-8"))
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="gpu",
    )

    pages = []
    for entry in manifest:
        res = ocr.predict(entry["image"])[0].json["res"]
        scores = [float(s) for s in res.get("rec_scores") or []]
        pages.append(
            {
                "book": entry["book"],
                "page": entry["page"],
                "lines": len(scores),
                "mean": round(statistics.fmean(scores), 4) if scores else None,
                "min": round(min(scores), 4) if scores else None,
                "under_0_6": sum(1 for s in scores if s < 0.6),
                "under_0_8": sum(1 for s in scores if s < 0.8),
            }
        )
        print(entry["page"], pages[-1]["lines"], pages[-1]["mean"], flush=True)

    all_scores = [
        float(s)
        for entry in manifest
        for s in ocr.predict(entry["image"])[0].json["res"].get("rec_scores") or []
    ] if False else []

    summary = {
        "pages": pages,
        "total_lines": sum(p["lines"] for p in pages),
        "pages_with_zero_lines": sum(1 for p in pages if p["lines"] == 0),
        "page_mean_of_means": round(statistics.fmean([p["mean"] for p in pages if p["mean"]]), 4),
        "pages_with_any_below_0_6": sum(1 for p in pages if p["under_0_6"]),
    }
    (OUT / "text_stats.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "pages"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
