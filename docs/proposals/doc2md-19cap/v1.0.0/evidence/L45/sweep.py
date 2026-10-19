"""Score -> line-correctness curve on degraded real invoices.

Reference is the OCR of the *undegraded* page, not a human transcription. The question
answered here is narrow: "when the engine reports a low score, is the line more likely
to differ from what it read on a clean page?" It cannot answer "what is the absolute
accuracy", and it must not be used on its own to justify a production constant.
"""

import difflib
import json
import pathlib
import re
import subprocess

import paddle
from PIL import Image, ImageEnhance, ImageFilter
from paddleocr import PaddleOCR

CORPUS = pathlib.Path("/media/szf/share/Home/Work/报销发票")
WORK = pathlib.Path("/tmp/l45")
WORK.mkdir(parents=True, exist_ok=True)

norm = lambda text: re.sub(r"\s+", "", text)

VARIANTS = {
    "clean": lambda im: im,
    "blur1": lambda im: im.filter(ImageFilter.GaussianBlur(1)),
    "blur2": lambda im: im.filter(ImageFilter.GaussianBlur(2)),
    "blur3": lambda im: im.filter(ImageFilter.GaussianBlur(3)),
    "down70": lambda im: im.resize((int(im.width * 0.7), int(im.height * 0.7))).resize(im.size),
    "down50": lambda im: im.resize((int(im.width * 0.5), int(im.height * 0.5))).resize(im.size),
    "contrast": lambda im: ImageEnhance.Contrast(im).enhance(0.45),
    "mix": lambda im: ImageEnhance.Contrast(im.filter(ImageFilter.GaussianBlur(2))).enhance(0.5),
    "blur4": lambda im: im.filter(ImageFilter.GaussianBlur(4)),
    "down40": lambda im: im.resize((int(im.width * 0.4), int(im.height * 0.4))).resize(im.size),
}

BUCKETS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9),
           (0.9, 0.95), (0.95, 1.0001)]


def bucket_of(score: float) -> str:
    for low, high in BUCKETS:
        if low <= score < high:
            return f"{low:.2f}-{min(high, 1.0):.2f}"
    return ">1"


def judge(text: str, reference: list[str], *, hit: float = 0.9) -> str | None:
    """'correct', 'wrong', or None when no reference line is close enough to compare."""
    probe = norm(text)
    if not probe:
        return None
    best = max((difflib.SequenceMatcher(None, probe, ref).ratio() for ref in reference),
               default=0.0)
    if best >= hit:
        return "correct"
    if best >= 0.5:
        return "wrong"
    return None


def sources():
    items = []
    for index, pdf in enumerate(sorted(CORPUS.rglob("*.pdf"))):
        stem = f"src{index:02d}"
        target = WORK / f"{stem}.png"
        if not target.exists():
            subprocess.run(
                ["pdftoppm", "-f", "1", "-l", "1", "-r", "200", "-png", "-singlefile",
                 str(pdf), str(WORK / stem)],
                check=True, capture_output=True, timeout=180,
            )
        items.append((pdf.name, target))
    for image in sorted(CORPUS.rglob("*")):
        if image.suffix.lower() in (".png", ".jpg", ".jpeg"):
            items.append((image.name, image))
    return items


def main() -> None:
    ocr = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False,
                    use_textline_orientation=False, device="gpu")

    stats = {f"{lo:.2f}-{min(hi, 1.0):.2f}": {"lines": 0, "correct": 0, "wrong": 0,
                                               "unjudged": 0} for lo, hi in BUCKETS}
    pages = []

    for name, path in sources():
        image = Image.open(path).convert("RGB")
        clean = ocr.predict(str(path))[0].json["res"]
        reference = [norm(t) for t in clean.get("rec_texts") or [] if norm(t)]
        if not reference:
            print(f"skip {name}: no clean text", flush=True)
            continue

        for variant, transform in VARIANTS.items():
            target = WORK / f"{path.stem}_{variant}.png"
            if variant != "clean":
                transform(image).save(target)
            res = ocr.predict(str(target if variant != "clean" else path))[0].json["res"]
            texts = [str(t) for t in res.get("rec_texts") or []]
            scores = [float(s) for s in res.get("rec_scores") or []]

            counts = {"correct": 0, "wrong": 0, "unjudged": 0}
            for text, score in zip(texts, scores):
                verdict = judge(text, reference)
                counts[verdict if verdict else "unjudged"] += 1
                slot = stats[bucket_of(score)]
                slot["lines"] += 1
                slot[verdict if verdict else "unjudged"] += 1

            pages.append({"source": name, "variant": variant, "lines": len(texts),
                          "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
                          **counts})
            print(f"{name[:28]:28} {variant:8} lines={len(texts):3} "
                  f"correct={counts['correct']:3} wrong={counts['wrong']:3}", flush=True)
            paddle.device.cuda.empty_cache()

    for slot in stats.values():
        judged = slot["correct"] + slot["wrong"]
        slot["judged"] = judged
        slot["precision"] = round(slot["correct"] / judged, 4) if judged else None
        slot["coverage"] = round(judged / slot["lines"], 4) if slot["lines"] else None

    total_lines = sum(p["lines"] for p in pages)
    total_wrong = sum(p["wrong"] for p in pages)
    summary = {
        "pages": pages,
        "total_lines": total_lines,
        "total_wrong": total_wrong,
        "buckets": stats,
        "reference_note": "reference is OCR of the undegraded page, not human annotation",
    }
    (WORK / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("total_lines", "total_wrong")}, ensure_ascii=False))
    for key, slot in stats.items():
        print(key, slot)


if __name__ == "__main__":
    main()
