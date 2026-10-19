"""Render a reproducible sample of pages from the authorised corpus.

The corpus is DK English illustrated books. It is used here for **layout** realism
(dense pages, tables, diagrams), not for accuracy or threshold claims: the books have
no usable text layer, so there is no machine-checkable ground truth.
"""

import json
import pathlib
import subprocess

CORPUS = pathlib.Path("/media/szf/share/Storage/DK_P2")
OUT = pathlib.Path("/tmp/l41pages")

# Four books with dense layouts; pages chosen mid-book to skip covers and indices.
BOOKS = [
    ("DK How the Body Works.pdf", [40, 41, 42, 43, 44]),
    ("Civil War.pdf", [6, 7, 8, 9, 10]),
    ("Aztec, Inca & Maya (DK Eyewitness)(gnv64).pdf", [12, 13, 14, 15, 16]),
    ("Monet (DK Art Ebook).pdf", [30, 31, 32, 33, 34]),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    for name, pages in BOOKS:
        source = CORPUS / name
        if not source.exists():
            raise SystemExit(f"missing corpus file: {source}")
        for page in pages:
            stem = f"{name[:12].replace(' ', '_')}_p{page:03d}"
            target = OUT / f"{stem}.png"
            if not target.exists():
                subprocess.run(
                    [
                        "pdftoppm",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-r",
                        "110",
                        "-png",
                        "-singlefile",
                        str(source),
                        str(OUT / stem),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
            records.append({"book": name, "page": page, "image": str(target)})

    (OUT / "manifest.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("pages", len(records), "->", OUT)


if __name__ == "__main__":
    main()
