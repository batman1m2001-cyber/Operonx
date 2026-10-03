"""K1b gate: the ML layout (docling Heron + TableFormer) against the heuristic.

    PYTHONPATH=<operonx branch> uv run --extra layout python scripts/eval_layout.py [--threads 4] [--pages 20]

Scores both layouts on the golden PDFs against tests/golden/truth (see
operonx_kb.testing.layout_eval) and times them per page, CPU only, after a
warm-up parse (model loading is excluded and reported separately).
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "tests" / "golden" / "docs"
TRUTH = ROOT / "tests" / "golden" / "truth"


def big_pdf(pages: int) -> bytes:
    spec = importlib.util.spec_from_file_location(
        "make_pdfs", ROOT / "tests" / "golden" / "make_pdfs.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    buf = io.BytesIO()
    c = mod._canvas(buf)
    w = mod._Writer(c, [(56, 286), (309, 539)], top=780, bottom=60)
    section = 0
    while w.page <= pages:
        section += 1
        w.heading(f"{section} Section about retrieval")
        for _ in range(3):
            w.para(mod.LOREM + " " + mod.LOREM)
    c.save()
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--pages", type=int, default=20)
    ap.add_argument(
        "--docling-tests",
        type=Path,
        help="a docling checkout's tests/data/pdf: also score against its reference outputs",
    )
    args = ap.parse_args()

    import json

    from operonx_kb.pdf.layout import HeuristicLayout
    from operonx_kb.pdf.models import HeronDetector, ModelLayout, TableFormer
    from operonx_kb.pdf.parser import PdfParser
    from operonx_kb.testing.layout_eval import load_truth, score_layout, truth_from_docling

    t0 = time.perf_counter()
    model = ModelLayout(
        detector=HeronDetector(num_threads=args.threads),
        tables=TableFormer(num_threads=args.threads),
    )
    parsers = {"heuristic": PdfParser(layout=HeuristicLayout()), "model": PdfParser(layout=model)}
    parsers["model"].parse((DOCS / "table_report.pdf").read_bytes())  # loads both models
    load_s = time.perf_counter() - t0

    print(
        "| file | layout | text recall | kind acc. | heading level acc. | order | table cells | spurious | s/page |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    mismatches = {}
    for truth_file in sorted(TRUTH.glob("*.json")):
        name = truth_file.name[: -len(".json")]
        data = (DOCS / name).read_bytes()
        truth = load_truth(truth_file)
        for label, parser in parsers.items():
            t = time.perf_counter()
            doc = parser.parse(data)
            per_page = (time.perf_counter() - t) / len(doc.pages)
            s = score_layout(truth, doc.blocks)
            mismatches[(name, label)] = s["mismatches"]
            print(f"| {name} | {label} | {s['text_recall']:.2f} | {s['kind_accuracy']:.2f} | {s['level_accuracy']:.2f} | "
                  f"{s['order']:.2f} | {s['table_cells']:.2f} | {s['spurious']} | {per_page:.3f} |")  # fmt: skip
    if args.docling_tests:
        print(
            "\n| docling reference | layout | pages | text recall | kind acc. | order | table cells | spurious | s/page |"
        )
        print("|---|---|---|---|---|---|---|---|---|")
        totals = {
            label: {
                "n": 0,
                "found": 0,
                "kind": 0,
                "pairs": 0.0,
                "cells": 0.0,
                "tables": 0,
                "spurious": 0,
                "pages": 0,
                "s": 0.0,
            }
            for label in parsers
        }
        for ref in sorted((args.docling_tests / "groundtruth").glob("*.json")):
            if ref.name.endswith(".pages.meta.json"):
                continue
            source = args.docling_tests / "sources" / (ref.name[: -len(".json")] + ".pdf")
            if not source.exists():
                continue
            truth = truth_from_docling(json.loads(ref.read_text(encoding="utf-8")))
            for label, parser in parsers.items():
                t = time.perf_counter()
                doc = parser.parse(source.read_bytes())
                elapsed = time.perf_counter() - t
                s = score_layout(truth, doc.blocks)
                n = s["truth_blocks"]
                tot = totals[label]
                tot["n"] += n
                tot["found"] += round(s["text_recall"] * n)
                tot["kind"] += round(s["kind_accuracy"] * n)
                tot["spurious"] += s["spurious"]
                tot["pages"] += len(doc.pages)
                tot["s"] += elapsed
                print(f"| {source.name} | {label} | {len(doc.pages)} | {s['text_recall']:.2f} | {s['kind_accuracy']:.2f} | "
                      f"{s['order']:.2f} | {s['table_cells']:.2f} | {s['spurious']} | {elapsed / max(1, len(doc.pages)):.3f} |")  # fmt: skip
        for label, tot in totals.items():
            print(f"| **all ({tot['pages']} pages)** | {label} | {tot['pages']} | {tot['found'] / tot['n']:.2f} | "
                  f"{tot['kind'] / tot['n']:.2f} | | | {tot['spurious']} | {tot['s'] / tot['pages']:.3f} |")  # fmt: skip
    data = big_pdf(args.pages)
    for label, parser in parsers.items():
        t = time.perf_counter()
        doc = parser.parse(data)
        print(
            f"| generated {len(doc.pages)}p | {label} | | | | | | | {(time.perf_counter() - t) / len(doc.pages):.3f} |"
        )
    print(f"\nmodel load + first parse: {load_s:.1f} s; threads={args.threads}")
    print("\nmismatches (truth kind -> layout kind):")
    for (name, label), rows in mismatches.items():
        for row in rows:
            print(f"- {name} / {label}: {row['truth']} -> {row['got']}: {row['text']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
