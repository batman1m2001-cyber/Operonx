"""OCR on real scans (CollectionSpec.ocr): coverage, speed and accuracy.

    uv run python scripts/bench_ocr.py CORPUS [--pages 2] [--accuracy-pages 40] [--languages vie]

``CORPUS`` is a folder of PDFs (D5's ``vi_public`` legal documents). For each PDF the
text backend (docling-parse) reads every page; a page with fewer than ``min_words``
words is a scan.

1. Coverage: the scans' share, and for the first ``--pages`` scanned pages of each
   document, the words and characters OCR recovers and the seconds per page.
2. Accuracy: ``--accuracy-pages`` pages that *have* a text layer are OCR'd too and
   compared with it — the character error rate (edit distance over the text layer's
   length, whitespace collapsed), on real Vietnamese legal pages; also on the text
   with its diacritics stripped, to separate tone marks from letters.

Results go to ``CORPUS/../ocr.json``; ``docs/bench/ocr.md`` is written from them.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from operonx_kb.pdf.backend import DoclingParseBackend, PdfiumRenderer  # noqa: E402
from operonx_kb.pdf.ocr import TesseractEngine  # noqa: E402


def text_of(words) -> str:
    return " ".join(w.text for w in words)


def norm(text: str) -> str:
    return " ".join(text.split())


def strip_marks(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def distance(a: str, b: str) -> int:
    """Levenshtein distance, two rows."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    return distance(ref, hyp) / max(1, len(ref))


def main(args) -> None:
    backend = DoclingParseBackend()
    engine = TesseractEngine(languages=args.languages)
    scale = args.dpi / 72.0
    pdfs = sorted(args.corpus.glob("*.pdf"))
    pages_total = scanned_total = 0
    scan_rows, acc_rows = [], []
    for pdf in pdfs:
        data = pdf.read_bytes()
        pages = backend.pages(data)
        scanned = [p for p in pages if len(p.words) < args.min_words]
        texted = [p for p in pages if len(p.words) >= args.min_words]
        pages_total += len(pages)
        scanned_total += len(scanned)
        with PdfiumRenderer(data) as renderer:
            for p in scanned[: args.pages]:
                t = time.perf_counter()
                words = engine.words(renderer.render(p.page_no, scale=scale), scale)
                scan_rows.append({"doc": pdf.name, "page": p.page_no, "words": len(words),
                                  "chars": len(text_of(words)),
                                  "seconds": round(time.perf_counter() - t, 2)})  # fmt: skip
            for p in texted[:1]:
                if len(acc_rows) >= args.accuracy_pages:
                    break
                ref = norm(text_of(p.words))
                t = time.perf_counter()
                hyp = norm(text_of(engine.words(renderer.render(p.page_no, scale=scale), scale)))
                acc_rows.append({"doc": pdf.name, "page": p.page_no, "chars": len(ref),
                                 "cer": round(cer(ref, hyp), 4),
                                 "cer_no_marks": round(cer(strip_marks(ref), strip_marks(hyp)), 4),
                                 "seconds": round(time.perf_counter() - t, 2)})  # fmt: skip
        print(f"{pdf.name}: {len(pages)} pages, {len(scanned)} scanned", flush=True)

    def med(rows, key):
        return round(statistics.median(r[key] for r in rows), 3) if rows else None

    out = {
        "documents": len(pdfs),
        "pages": pages_total,
        "scanned_pages": scanned_total,
        "scanned_documents": len({r["doc"] for r in scan_rows}),
        "ocr": {"pages": len(scan_rows), "empty": sum(1 for r in scan_rows if r["words"] == 0),
                "median_words": med(scan_rows, "words"), "median_seconds": med(scan_rows, "seconds")},
        "accuracy": {"pages": len(acc_rows), "median_cer": med(acc_rows, "cer"),
                     "mean_cer": round(statistics.mean(r["cer"] for r in acc_rows), 4) if acc_rows else None,
                     "median_cer_no_marks": med(acc_rows, "cer_no_marks"),
                     "pages_cer_under_5pct": sum(1 for r in acc_rows if r["cer"] < 0.05)},
        "settings": {"languages": args.languages, "dpi": args.dpi, "min_words": args.min_words,
                     "engine": engine.version()},
        "scan_rows": scan_rows,
        "accuracy_rows": acc_rows,
    }  # fmt: skip
    path = args.corpus.parent / "ocr.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), "utf-8")
    print(json.dumps({k: v for k, v in out.items() if not k.endswith("_rows")}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--pages", type=int, default=2, help="scanned pages OCR'd per document")
    ap.add_argument("--accuracy-pages", type=int, default=40)
    ap.add_argument("--languages", default="vie")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--min-words", type=int, default=3)
    main(ap.parse_args())
