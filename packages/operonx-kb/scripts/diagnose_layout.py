"""Why a layout misses reference blocks and emits spurious ones, with counts.

    PYTHONPATH=<operonx branch> uv run python scripts/diagnose_layout.py \\
        [--docling-tests <docling>/tests/data/pdf] [--examples 3] [--layout model] [--page form-irs-w9]

Scores a layout (the heuristic by default; ``model`` needs the ``layout``
extra) like scripts/eval_layout.py (hand reference, and
docling's outputs with ``--docling-tests``) and sorts every unmatched block into
one cause. Texts are compared after lower-casing and removing whitespace and
hyphens, so a block cut at a line break still counts as a piece of its block.

Spurious (a predicted block that matches nothing), first cause that applies:

- ``figure_text``: it lies inside a reference figure (text drawn in a picture);
- ``false_table``: a predicted table where the reference has none;
- ``table_text``: its text is part of a reference table (a table not found);
- ``piece``: its text is part of one reference block (that block was split);
- ``merge``: it contains a whole reference block and more (blocks were joined);
- ``figure``: a predicted figure the reference does not have;
- ``other``.

Missed (a reference block that matches nothing):

- ``joined``: its text is inside a larger predicted block;
- ``in_table``: its text is inside a predicted table;
- ``split``: predicted blocks hold pieces of it;
- ``table``: a reference table no predicted table matches well enough;
- ``text``: none of these (text differs: order, extraction).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "tests" / "layout_reference" / "reference.json"


def _key(text: str) -> str:
    from operonx_kb.text.normalize import normalize_inline

    return re.sub(r"[\s\-­]", "", normalize_inline(text or "").lower())


def _table_text(rows: List[List[str]]) -> str:
    return " ".join(" ".join(r) for r in rows or [])


def classify(truth: List[Dict[str, Any]], blocks: List[Any], score: Dict[str, Any]) -> Dict:
    """Causes of the spurious and missed blocks of one scored page or document."""
    used = {m for m in score["matches"] if m is not None}
    tkeys = [_key(t.get("text", "")) for t in truth if t["kind"] not in ("table", "figure")]
    tables = [_key(_table_text(t["rows"])) for t in truth if t["kind"] == "table"]
    figures = [t for t in truth if t["kind"] == "figure"]
    pkeys = [
        _key(_table_text(b.attrs.get("rows")) if b.kind == "table" else b.text) for b in blocks
    ]
    spurious, missed = [], []
    for i, b in enumerate(blocks):
        if i in used:
            continue
        k = pkeys[i]
        r = b.regions[0] if b.regions else None
        centre = r and ((r.bbox[0] + r.bbox[2]) / 2, (r.bbox[1] + r.bbox[3]) / 2)
        if (
            b.kind != "figure"
            and r
            and any(
                f.get("page") == r.page_no
                and f["bbox"][0] <= centre[0] <= f["bbox"][2]
                and f["bbox"][1] <= centre[1] <= f["bbox"][3]
                for f in figures
            )
        ):
            cause = "figure_text"
        elif b.kind == "table":
            cause = "false_table" if not tables else "table_text"
        elif k and any(k in t for t in tables):
            cause = "table_text"
        elif k and any(k in t and len(k) < len(t) for t in tkeys):
            cause = "piece"
        elif k and any(t and t in k for t in tkeys):
            cause = "merge"
        elif b.kind == "figure":
            cause = "figure"
        else:
            cause = "other"
        spurious.append((cause, b.kind, (b.text or "")[:80]))
    for t, m in zip(truth, score["matches"]):
        if m is not None or t["kind"] == "figure":
            continue
        if t["kind"] == "table":
            missed.append(("table", "table", ""))
            continue
        k = _key(t["text"])
        if any(k in p and len(p) > len(k) for p, b in zip(pkeys, blocks) if b.kind != "table"):
            cause = "joined"
        elif any(k in p for p, b in zip(pkeys, blocks) if b.kind == "table"):
            cause = "in_table"
        elif any(p and p in k for p in pkeys):
            cause = "split"
        else:
            cause = "text"
        missed.append((cause, t["kind"], t["text"][:80]))
    return {"spurious": spurious, "missed": missed}


def report(title: str, rows: Dict[str, List], examples: int) -> None:
    for side in ("spurious", "missed"):
        counts = Counter(c for c, _, _ in rows[side])
        total = len(rows[side])
        print(f"\n### {title}: {side} ({total})\n")
        print("| cause | count | share | by kind |")
        print("|---|---|---|---|")
        for cause, n in counts.most_common():
            kinds = Counter(k for c, k, _ in rows[side] if c == cause).most_common(3)
            print(
                f"| {cause} | {n} | {n / max(1, total):.0%} | {', '.join(f'{k} {v}' for k, v in kinds)} |"
            )
        if examples:
            for cause, _ in counts.most_common():
                shown = [t for c, _, t in rows[side] if c == cause][:examples]
                print(f"- {cause}: " + " · ".join(repr(t) for t in shown))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", type=Path, default=REFERENCE)
    ap.add_argument("--docling-tests", type=Path)
    ap.add_argument("--examples", type=int, default=3)
    ap.add_argument("--layout", choices=("heuristic", "model"), default="heuristic")
    ap.add_argument(
        "--page", action="append", help="only these hand-reference page ids (skips docling's set)"
    )
    args = ap.parse_args()

    from operonx_kb.pdf.parser import PdfParser
    from operonx_kb.testing.layout_eval import (
        load_reference,
        page_blocks,
        score_layout,
        truth_from_docling,
    )

    if args.layout == "model":
        from operonx_kb.pdf.models import HeronDetector, ModelLayout, TableFormer

        parser = PdfParser(layout=ModelLayout(detector=HeronDetector(), tables=TableFormer()))
    else:
        parser = PdfParser()
    hand: Dict[str, List] = defaultdict(list)
    for page in load_reference(args.reference, args.docling_tests):
        if page["path"] is None or (args.page and page["id"] not in args.page):
            continue
        doc = parser.parse(page["path"].read_bytes())
        blocks = page_blocks(doc.blocks, page["page"], page.get("scope"))
        out = classify(page["blocks"], blocks, score_layout(page["blocks"], blocks))
        for side in out:
            hand[side].extend(out[side])
    report("Hand reference", hand, args.examples)
    if args.docling_tests and not args.page:
        ref: Dict[str, List] = defaultdict(list)
        for path in sorted((args.docling_tests / "groundtruth").glob("*.json")):
            if path.name.endswith(".pages.meta.json"):
                continue
            source = args.docling_tests / "sources" / (path.name[: -len(".json")] + ".pdf")
            if not source.exists():
                continue
            truth = truth_from_docling(json.loads(path.read_text(encoding="utf-8")))
            blocks = parser.parse(source.read_bytes()).blocks
            out = classify(truth, blocks, score_layout(truth, blocks))
            for side in out:
                ref[side].extend(out[side])
        report("docling's outputs", ref, args.examples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
