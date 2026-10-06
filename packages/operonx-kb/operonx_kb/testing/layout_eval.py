"""Scoring a PDF layout against a reference.

Three references are read here, each with its own bias:

- **Golden truth** (``tests/golden/truth``): what the generators of the golden
  PDFs draw, in reading order. The heuristic layout was developed on them.
- **Hand reference** (``tests/layout_reference/reference.json``,
  :func:`load_reference`): real pages from papers, reports, a manual, a
  newspaper, a form, slides and a Vietnamese article, each block decided by a
  person looking at the rendered page (the file states its conventions). Not a
  model's output.
- **docling's outputs** for its own test PDFs (:func:`truth_from_docling`):
  docling's *model* output, so they measure agreement with docling.

A reference block has ``kind``, ``text`` (``level`` for headings, ``rows`` for
tables, ``bbox`` and ``page`` for figures). Predicted blocks are matched to
reference blocks one to one: text blocks by text (normalised; similarity ≥ 0.9,
a tie goes to the block of the same kind), tables by the best cell overlap,
figures by geometry (a predicted figure on the same page lying at least half
inside the reference figure). The scores are:

- ``text_recall``: share of the reference's text and table blocks found at all;
- ``kind_accuracy``: share found *with the right kind*;
- ``level_accuracy``: share of reference headings found with the right level;
- ``order``: share of consecutive found text/table pairs that come out in order;
- ``table_cells``: share of reference table cells equal to the predicted cell at
  the same row and column;
- ``figures``: share of reference figures found;
- ``spurious``: predicted blocks that match no reference block.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx_kb.parsing.base import RawBlock
from operonx_kb.text.normalize import normalize_inline

__all__ = [
    "load_truth",
    "load_reference",
    "truth_from_docling",
    "page_blocks",
    "score_layout",
]

BBox = Tuple[float, float, float, float]


def load_truth(path: Path) -> List[Dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["blocks"]


def load_reference(path: Path, docling_tests: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The hand reference's pages, each with ``path`` set to its PDF.

    Pages whose ``file`` starts with ``docling:`` live in a docling checkout
    (``docling_tests`` is its ``tests/data/pdf`` directory); they get
    ``path=None`` when it is not given. The others sit next to ``path``.
    """
    path = Path(path)
    ref = json.loads(path.read_text(encoding="utf-8"))
    pages = []
    for page in ref["pages"]:
        page = dict(page)
        name = page["file"]
        if name.startswith("docling:"):
            rel = name[len("docling:") :].removeprefix("tests/data/pdf/")
            page["path"] = Path(docling_tests) / rel if docling_tests else None
        else:
            page["path"] = path.parent / name
        for block in page["blocks"]:
            if block["kind"] == "figure":
                block.setdefault("page", page["page"])
        pages.append(page)
    return pages


_DOCLING_KINDS = {
    "title": "title", "section_header": "heading", "list_item": "list_item", "caption": "caption",
    "footnote": "footnote", "page_header": "page_header", "page_footer": "page_footer",
    "code": "code", "formula": "formula",
}  # fmt: skip


def _docling_box(item: Dict[str, Any], doc: Dict[str, Any]) -> Optional[Tuple[int, BBox]]:
    prov = item.get("prov") or []
    if not prov:
        return None
    p = prov[0]
    size = doc["pages"][str(p["page_no"])]["size"]
    w, h = size["width"], size["height"]
    b = p["bbox"]
    if b.get("coord_origin", "BOTTOMLEFT") == "BOTTOMLEFT":
        return p["page_no"], (b["l"] / w, (h - b["t"]) / h, b["r"] / w, (h - b["b"]) / h)
    return p["page_no"], (b["l"] / w, b["t"] / h, b["r"] / w, b["b"] / h)


def truth_from_docling(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reference blocks from a DoclingDocument JSON (docling's own test outputs).

    The body tree is walked in order; a table's or picture's captions come
    before it, a picture is a figure with its page and box, its other children
    (text inside a figure) are skipped, and furniture is appended at the end.
    These references are docling's *model output*, not human labels: agreement
    with them is what they measure.
    """
    by_ref: Dict[str, Dict[str, Any]] = {}
    for key in ("texts", "tables", "pictures", "groups"):
        for item in doc.get(key, []):
            by_ref[item["self_ref"]] = item
    out: List[Dict[str, Any]] = []

    def emit(ref: str) -> None:
        item = by_ref.get(ref)
        if item is None:
            return
        for cap in item.get("captions", []):
            emit_text(by_ref.get(cap["$ref"]), "caption")
        if ref.startswith("#/tables/"):
            grid = item["data"].get("grid") or []
            rows = [[c.get("text", "") for c in row] for row in grid]
            if rows:
                out.append({"kind": "table", "rows": rows})
            return
        if ref.startswith("#/pictures/"):
            box = _docling_box(item, doc)
            if box is not None:
                out.append({"kind": "figure", "page": box[0], "bbox": list(box[1])})
            return
        if ref.startswith("#/texts/"):
            if item.get("label") != "caption":
                emit_text(item, None)
        for child in item.get("children", []):
            emit(child["$ref"])

    def emit_text(item: Optional[Dict[str, Any]], kind: Optional[str]) -> None:
        if item is None or not item.get("text", "").strip():
            return
        row: Dict[str, Any] = {
            "kind": kind or _DOCLING_KINDS.get(item.get("label"), "paragraph"),
            "text": item["text"],
        }
        if row["kind"] == "heading":
            row["level"] = item.get("level", 1)
        out.append(row)

    for child in doc.get("body", {}).get("children", []):
        emit(child["$ref"])
    for child in doc.get("furniture", {}).get("children", []):
        emit(child["$ref"])
    return out


def page_blocks(
    blocks: Sequence[RawBlock], page_no: int, scope: Optional[Sequence[float]] = None
) -> List[RawBlock]:
    """The blocks a single-page reference is scored on: those that start on
    ``page_no`` (a block continued from an earlier page belongs there), with
    their first region's centre inside ``scope`` when one is given."""
    out = []
    for b in blocks:
        if not b.regions or b.regions[0].page_no != page_no:
            continue
        if scope is not None:
            x0, y0, x1, y1 = b.regions[0].bbox
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if not (scope[0] <= cx <= scope[2] and scope[1] <= cy <= scope[3]):
                continue
        out.append(b)
    return out


def _norm(text: str) -> str:
    return normalize_inline(text).lower()


def _similar(a: str, b: str, threshold: float = 0.0) -> float:
    """Similarity ratio of the normalised texts; 0.0 when it cannot reach ``threshold``.

    ``real_quick_ratio`` and ``quick_ratio`` are upper bounds of ``ratio``, so
    the cut changes no score at or above the threshold, only skips work.
    """
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
        return 0.0
    return matcher.ratio()


def _cells(truth: List[List[str]], pred: List[List[str]]) -> int:
    return sum(
        1
        for r, row in enumerate(truth)
        for c, cell in enumerate(row)
        if r < len(pred) and c < len(pred[r]) and _norm(pred[r][c]) == _norm(cell)
    )


def _inside_share(pred: Sequence[float], truth: Sequence[float]) -> float:
    """Share of ``pred``'s area that lies inside ``truth``."""
    w = max(0.0, min(pred[2], truth[2]) - max(pred[0], truth[0]))
    h = max(0.0, min(pred[3], truth[3]) - max(pred[1], truth[1]))
    area = max((pred[2] - pred[0]) * (pred[3] - pred[1]), 1e-9)
    return w * h / area


def _match_score(t: Dict[str, Any], b: RawBlock) -> Tuple[float, float]:
    """``(score, threshold)`` of predicted ``b`` for reference ``t``; 0 when incomparable."""
    if t["kind"] == "figure":
        if b.kind != "figure":
            return 0.0, 0.5
        share = max(
            (_inside_share(r.bbox, t["bbox"]) for r in b.regions if r.page_no == t.get("page")),
            default=0.0,
        )
        return share, 0.5
    if t["kind"] == "table":
        if b.kind != "table":
            return 0.0, 0.3
        total = max(1, sum(len(r) for r in t["rows"]))
        return _cells(t["rows"], b.attrs.get("rows") or []) / total, 0.3
    if b.kind in ("table", "figure"):
        return 0.0, 0.9
    return _similar(t["text"], b.text, 0.9), 0.9


def score_layout(truth: List[Dict[str, Any]], blocks: List[RawBlock]) -> Dict[str, Any]:
    """Scores of ``blocks`` (a parser's output) against ``truth``."""
    used: set = set()
    matches: List[Optional[int]] = []
    cells_ok = cells_total = 0
    for t in truth:
        best, best_key = None, (0.0, False)
        for i, b in enumerate(blocks):
            if i in used:
                continue
            score, threshold = _match_score(t, b)
            key = (score, b.kind == t["kind"])
            if score >= threshold and key > best_key:
                best, best_key = i, key
        matches.append(best)
        if best is not None:
            used.add(best)
        if t["kind"] == "table":
            cells_total += sum(len(r) for r in t["rows"])
            if best is not None:
                cells_ok += _cells(t["rows"], blocks[best].attrs.get("rows") or [])
    texts = [(t, m) for t, m in zip(truth, matches) if t["kind"] != "figure"]
    figures = [m for t, m in zip(truth, matches) if t["kind"] == "figure"]
    found = [(t, blocks[m]) for t, m in texts if m is not None]
    headings = [t for t, _ in texts if t["kind"] == "heading"]
    level_ok = sum(
        1
        for t, b in found
        if t["kind"] == "heading" and b.kind == "heading" and b.level == t.get("level")
    )
    idx = [m for _, m in texts if m is not None]
    pairs = list(zip(idx, idx[1:]))
    n = len(texts)
    kind_ok = sum(1 for t, b in found if t["kind"] == b.kind)
    figures_found = sum(1 for m in figures if m is not None)
    return {
        "truth_blocks": n,
        "found": len(found),
        "kind_found": kind_ok,
        "text_recall": len(found) / n if n else 1.0,
        "kind_accuracy": kind_ok / n if n else 1.0,
        "level_accuracy": level_ok / len(headings) if headings else 1.0,
        "order": sum(1 for a, b in pairs if a < b) / len(pairs) if pairs else 1.0,
        "table_cells": cells_ok / cells_total if cells_total else 1.0,
        "cells_ok": cells_ok,
        "cells_total": cells_total,
        "figures_total": len(figures),
        "figures_found": figures_found,
        "figures": figures_found / len(figures) if figures else 1.0,
        "spurious": len(blocks) - len(used),
        "matches": matches,
        "mismatches": [
            {"truth": t["kind"], "got": b.kind, "text": (t.get("text") or "table")[:40]}
            for t, b in found
            if t["kind"] != b.kind
        ]
        + [
            {"truth": t["kind"], "got": None, "text": (t.get("text") or "table")[:40]}
            for t, m in texts
            if m is None
        ],
    }
