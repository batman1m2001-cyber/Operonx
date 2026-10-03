"""Layout quality and speed against three references (see operonx_kb.testing.layout_eval).

    PYTHONPATH=<operonx branch> uv run python scripts/eval_layout.py \\
        [--layouts heuristic,model] [--docling-tests <docling>/tests/data/pdf] [--threads 4] [--pages 20]

1. The golden PDFs against tests/golden/truth.
2. The hand reference (tests/layout_reference/reference.json): real pages
   annotated by hand; pages from docling's test set need ``--docling-tests``.
3. With ``--docling-tests``: docling's 17 test PDFs against docling's own
   (model) outputs.

``model`` needs the ``layout`` extra; it is timed after a warm-up parse (model
loading is reported separately). Times are seconds per page, wall clock, CPU.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "tests" / "golden" / "docs"
TRUTH = ROOT / "tests" / "golden" / "truth"
REFERENCE = ROOT / "tests" / "layout_reference" / "reference.json"


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


class Totals:
    """Pooled counts over many scored documents or pages."""

    def __init__(self) -> None:
        self.n = self.found = self.kind = self.cells_ok = self.cells = 0
        self.figs = self.figs_found = self.spurious = self.pages = 0
        self.seconds = 0.0

    def add(self, s: dict, pages: int, seconds: float) -> None:
        self.n += s["truth_blocks"]
        self.found += s["found"]
        self.kind += s["kind_found"]
        self.cells_ok += s["cells_ok"]
        self.cells += s["cells_total"]
        self.figs += s["figures_total"]
        self.figs_found += s["figures_found"]
        self.spurious += s["spurious"]
        self.pages += pages
        self.seconds += seconds

    def row(self, label: str, layout: str) -> str:
        cells = f"{self.cells_ok / self.cells:.2f}" if self.cells else ""
        figs = f"{self.figs_found}/{self.figs}" if self.figs else ""
        return (
            f"| **{label}** | {layout} | {self.pages} | {self.found / self.n:.2f} | "
            f"{self.kind / self.n:.2f} | | {cells} | {figs} | {self.spurious} | "
            f"{self.seconds / max(1, self.pages):.3f} |"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layouts", default="heuristic,model")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--pages", type=int, default=20)
    ap.add_argument("--reference", type=Path, default=REFERENCE)
    ap.add_argument(
        "--docling-tests",
        type=Path,
        help="a docling checkout's tests/data/pdf: its pages of the hand reference, and "
        "its reference outputs",
    )
    ap.add_argument("--json", type=Path, help="also write every score here")
    args = ap.parse_args()

    from operonx_kb.pdf.layout import HeuristicLayout
    from operonx_kb.pdf.parser import PdfParser
    from operonx_kb.testing.layout_eval import (
        load_reference,
        load_truth,
        page_blocks,
        score_layout,
        truth_from_docling,
    )

    parsers = {}
    load_s = None
    for label in args.layouts.split(","):
        if label == "heuristic":
            parsers[label] = PdfParser(layout=HeuristicLayout())
            parsers[label].parse((DOCS / "table_report.pdf").read_bytes())  # warm-up (imports)
        elif label == "model":
            from operonx_kb.pdf.models import HeronDetector, ModelLayout, TableFormer

            t0 = time.perf_counter()
            parsers[label] = PdfParser(
                layout=ModelLayout(
                    detector=HeronDetector(num_threads=args.threads),
                    tables=TableFormer(num_threads=args.threads),
                )
            )
            parsers[label].parse((DOCS / "table_report.pdf").read_bytes())  # loads both models
            load_s = time.perf_counter() - t0
        else:
            ap.error(f"unknown layout {label!r}: use heuristic and/or model")
    record: dict = defaultdict(dict)

    header = "| {} | layout | pages | text recall | kind acc. | order | table cells | figures | spurious | s/page |"
    rule = "|---|---|---|---|---|---|---|---|---|---|"

    def keep(s: dict) -> dict:
        return {k: v for k, v in s.items() if k != "matches"}

    print("## Golden PDFs\n")
    print(header.format("file"))
    print(rule)
    for truth_file in sorted(TRUTH.glob("*.json")):
        name = truth_file.name[: -len(".json")]
        data = (DOCS / name).read_bytes()
        truth = load_truth(truth_file)
        for label, parser in parsers.items():
            t = time.perf_counter()
            doc = parser.parse(data)
            per_page = (time.perf_counter() - t) / len(doc.pages)
            s = score_layout(truth, doc.blocks)
            record["golden"][f"{name}/{label}"] = keep(s)
            print(f"| {name} | {label} | {len(doc.pages)} | {s['text_recall']:.2f} | {s['kind_accuracy']:.2f} | "
                  f"{s['order']:.2f} | {s['table_cells']:.2f} | | {s['spurious']} | {per_page:.3f} |")  # fmt: skip

    print("\n## Hand reference\n")
    print(header.format("page"))
    print(rule)
    pages = load_reference(args.reference, args.docling_tests)
    totals = {label: Totals() for label in parsers}
    skipped = [p["id"] for p in pages if p["path"] is None]
    for page in pages:
        if page["path"] is None:
            continue
        data = page["path"].read_bytes()
        for label, parser in parsers.items():
            t = time.perf_counter()
            doc = parser.parse(data)
            per_page = (time.perf_counter() - t) / len(doc.pages)
            blocks = page_blocks(doc.blocks, page["page"], page.get("scope"))
            s = score_layout(page["blocks"], blocks)
            totals[label].add(s, 1, per_page)
            record["hand"][f"{page['id']}/{label}"] = keep(s)
            figs = f"{s['figures_found']}/{s['figures_total']}" if s["figures_total"] else ""
            cells = f"{s['table_cells']:.2f}" if s["cells_total"] else ""
            print(f"| {page['id']} | {label} | 1 | {s['text_recall']:.2f} | {s['kind_accuracy']:.2f} | "
                  f"{s['order']:.2f} | {cells} | {figs} | {s['spurious']} | {per_page:.3f} |")  # fmt: skip
    for label, tot in totals.items():
        if tot.pages:
            print(tot.row(f"all ({tot.pages} pages)", label))
    if skipped:
        print(f"\nnot scored (pass --docling-tests): {', '.join(skipped)}")

    if args.docling_tests:
        print("\n## docling's reference outputs (agreement with docling's model)\n")
        print(header.format("file"))
        print(rule)
        totals = {label: Totals() for label in parsers}
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
                totals[label].add(s, len(doc.pages), elapsed)
                record["docling"][f"{source.name}/{label}"] = keep(s)
                figs = f"{s['figures_found']}/{s['figures_total']}" if s["figures_total"] else ""
                print(f"| {source.name} | {label} | {len(doc.pages)} | {s['text_recall']:.2f} | {s['kind_accuracy']:.2f} | "
                      f"{s['order']:.2f} | {s['table_cells']:.2f} | {figs} | {s['spurious']} | {elapsed / max(1, len(doc.pages)):.3f} |")  # fmt: skip
        for label, tot in totals.items():
            print(tot.row(f"all ({tot.pages} pages)", label))

    print("\n## Speed on a generated two-column PDF\n")
    data = big_pdf(args.pages)
    for label, parser in parsers.items():
        t = time.perf_counter()
        doc = parser.parse(data)
        print(
            f"- {label}: {(time.perf_counter() - t) / len(doc.pages):.3f} s/page over {len(doc.pages)} pages"
        )
    if load_s is not None:
        print(f"\nmodel load + first parse: {load_s:.1f} s; threads={args.threads}")
    if args.json:
        args.json.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
