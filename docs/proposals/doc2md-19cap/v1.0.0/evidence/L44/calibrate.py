"""Score-vs-correctness on real Chinese invoices, with the PDF text layer as gold.

Gold here is *the text layer*, i.e. the publisher's own machine text, not a human
transcription. It answers "does a low rec_score mean a wrong line?" but it cannot
establish an absolute accuracy figure (both sides can be wrong together, and
e-invoice PDFs are unusually clean input).
"""

import difflib
import json
import pathlib
import re
import subprocess

import paddle

OUT = pathlib.Path("/tmp/l44pages")
CORPUS = pathlib.Path("/media/szf/share/Home/Work/报销发票")


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def render(pdf: pathlib.Path, page: int, stem: str) -> pathlib.Path:
    target = OUT / f"{stem}.png"
    if not target.exists():
        subprocess.run(
            ["pdftoppm", "-f", str(page), "-l", str(page), "-r", "200", "-png",
             "-singlefile", str(pdf), str(OUT / stem)],
            check=True, capture_output=True, timeout=180,
        )
    return target


def main() -> None:
    from paddleocr import PaddleOCR

    OUT.mkdir(parents=True, exist_ok=True)
    pdfs = []
    for pdf in sorted(CORPUS.rglob("*.pdf")):
        text = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
            capture_output=True, text=True, timeout=120,
        ).stdout
        if len(norm(text)) >= 100:
            pdfs.append((pdf, text))

    print(f"pages with text layer: {len(pdfs)}", flush=True)
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="gpu",
    )

    pages = []
    buckets = {"0.0-0.6": [0, 0], "0.6-0.8": [0, 0], "0.8-0.9": [0, 0],
               "0.9-0.95": [0, 0], "0.95-1.0": [0, 0]}
    for index, (pdf, gold_text) in enumerate(pdfs):
        stem = f"inv{index:02d}"
        image = render(pdf, 1, stem)
        res = ocr.predict(str(image))[0].json["res"]
        texts = [str(t) for t in res.get("rec_texts") or []]
        scores = [float(s) for s in res.get("rec_scores") or []]

        gold = norm(gold_text)
        lines = []
        for text, score in zip(texts, scores):
            probe = norm(text)
            hit = bool(probe) and probe in gold
            lines.append({"text": text, "score": round(score, 4), "in_gold": hit})
            key = ("0.0-0.6" if score < 0.6 else "0.6-0.8" if score < 0.8
                   else "0.8-0.9" if score < 0.9 else "0.9-0.95" if score < 0.95
                   else "0.95-1.0")
            buckets[key][0] += 1
            buckets[key][1] += int(hit)

        joined = norm("".join(texts))
        similarity = difflib.SequenceMatcher(None, gold, joined).ratio()
        pages.append({
            "file": str(pdf.relative_to(CORPUS)),
            "lines": len(texts),
            "hits": sum(1 for line in lines if line["in_gold"]),
            "page_similarity": round(similarity, 4),
            "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
            "detail": lines,
        })
        print(f"{pdf.name[:40]}: {len(texts)} lines, "
              f"{pages[-1]['hits']} in gold, sim {similarity:.3f}", flush=True)
        paddle.device.cuda.empty_cache()

    summary = {
        "pages": pages,
        "total_lines": sum(p["lines"] for p in pages),
        "total_hits": sum(p["hits"] for p in pages),
        "buckets": {k: {"lines": v[0], "correct": v[1],
                        "precision": round(v[1] / v[0], 4) if v[0] else None}
                    for k, v in buckets.items()},
    }
    (OUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("total_lines", "total_hits")}, ensure_ascii=False))
    print(json.dumps(summary["buckets"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
