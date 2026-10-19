"""Controlled degradation: the clean OCR text is the reference, not the PDF layer."""
import json, pathlib, re
from PIL import Image, ImageFilter, ImageEnhance
import paddle
from paddleocr import PaddleOCR
import difflib

SRC = pathlib.Path("/tmp/l44pages/inv01.png")
OUT = pathlib.Path("/tmp/l44deg"); OUT.mkdir(exist_ok=True)
norm = lambda t: re.sub(r"\s+", "", t)

def variants(img):
    yield "clean", img
    yield "blur2", img.filter(ImageFilter.GaussianBlur(2))
    yield "blur4", img.filter(ImageFilter.GaussianBlur(4))
    small = img.resize((img.width//2, img.height//2)).resize(img.size)
    yield "halfres", small
    yield "lowcontrast", ImageEnhance.Contrast(img).enhance(0.35)
    yield "blur4+lowcontrast", ImageEnhance.Contrast(img.filter(ImageFilter.GaussianBlur(4))).enhance(0.4)

ocr = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False,
                use_textline_orientation=False, device="gpu")
base = Image.open(SRC)
ref = None
rows = []
for name, img in variants(base):
    path = OUT / f"{name}.png"; img.save(path)
    res = ocr.predict(str(path))[0].json["res"]
    texts = [str(t) for t in res.get("rec_texts") or []]
    scores = [float(s) for s in res.get("rec_scores") or []]
    joined = norm("".join(texts))
    if ref is None: ref = joined
    sim = difflib.SequenceMatcher(None, ref, joined).ratio()
    rows.append({"variant": name, "lines": len(texts),
                 "mean": round(sum(scores)/len(scores), 4) if scores else None,
                 "min": round(min(scores), 4) if scores else None,
                 "under_0_6": sum(1 for s in scores if s < 0.6),
                 "text_similarity_to_clean": round(sim, 4)})
    print(rows[-1], flush=True)
    paddle.device.cuda.empty_cache()
json.dump(rows, open(OUT/"degrade.json","w"), ensure_ascii=False, indent=1)
