"""Scoring a PDF layout against hand-written ground truth (``tests/golden/truth``).

A truth file lists what a generator draws, in reading order: ``kind``, ``text``
(``level`` for headings, ``rows`` for tables). Predicted blocks are matched to
truth blocks one to one by text (normalised; similarity ≥ 0.9, or for tables the
best cell overlap), and the scores are:

- ``text_recall``: share of truth blocks found at all;
- ``kind_accuracy``: share of truth blocks found *with the right kind*;
- ``level_accuracy``: share of truth headings found with the right level;
- ``order``: share of consecutive found truth pairs that come out in order;
- ``table_cells``: share of truth table cells equal to the predicted cell at
  the same row and column;
- ``spurious``: predicted blocks that match no truth block.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

from operonx_kb.parsing.base import RawBlock
from operonx_kb.text.normalize import normalize_inline

__all__ = ["load_truth", "truth_from_docling", "score_layout"]


def load_truth(path: Path) -> List[Dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["blocks"]


_DOCLING_KINDS = {
    "title": "title", "section_header": "heading", "list_item": "list_item", "caption": "caption",
    "footnote": "footnote", "page_header": "page_header", "page_footer": "page_footer",
    "code": "code", "formula": "formula",
}  # fmt: skip


def truth_from_docling(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reference blocks from a DoclingDocument JSON (docling's own test outputs).

    The body tree is walked in order; a table's or picture's captions come
    before it, a picture's other children (text inside a figure) are skipped,
    and furniture is appended at the end. These references are docling's
    *model output*, not human labels: agreement with them is what they measure.
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


def _norm(text: str) -> str:
    return normalize_inline(text).lower()


def _similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _cells(truth: List[List[str]], pred: List[List[str]]) -> int:
    return sum(
        1
        for r, row in enumerate(truth)
        for c, cell in enumerate(row)
        if r < len(pred) and c < len(pred[r]) and _norm(pred[r][c]) == _norm(cell)
    )


def score_layout(truth: List[Dict[str, Any]], blocks: List[RawBlock]) -> Dict[str, Any]:
    """Scores of ``blocks`` (a parser's output) against ``truth``."""
    used: set = set()
    matches: List[Optional[int]] = []
    cells_ok = cells_total = 0
    for t in truth:
        best, best_score = None, 0.0
        for i, b in enumerate(blocks):
            if i in used:
                continue
            if t["kind"] == "table":
                if b.kind != "table":
                    continue
                score = _cells(t["rows"], b.attrs.get("rows") or []) / max(
                    1, sum(len(r) for r in t["rows"])
                )
                threshold = 0.3
            else:
                if b.kind == "table":
                    continue
                score = _similar(t["text"], b.text)
                threshold = 0.9
            if score >= threshold and score > best_score:
                best, best_score = i, score
        matches.append(best)
        if best is not None:
            used.add(best)
        if t["kind"] == "table":
            total = sum(len(r) for r in t["rows"])
            cells_total += total
            if best is not None:
                cells_ok += _cells(t["rows"], blocks[best].attrs.get("rows") or [])
    found = [(t, blocks[m]) for t, m in zip(truth, matches) if m is not None]
    headings = [t for t in truth if t["kind"] == "heading"]
    level_ok = sum(
        1
        for t, b in found
        if t["kind"] == "heading" and b.kind == "heading" and b.level == t["level"]
    )
    idx = [m for m in matches if m is not None]
    pairs = list(zip(idx, idx[1:]))
    return {
        "truth_blocks": len(truth),
        "text_recall": len(found) / len(truth) if truth else 1.0,
        "kind_accuracy": sum(1 for t, b in found if t["kind"] == b.kind) / len(truth)
        if truth
        else 1.0,
        "level_accuracy": level_ok / len(headings) if headings else 1.0,
        "order": sum(1 for a, b in pairs if a < b) / len(pairs) if pairs else 1.0,
        "table_cells": cells_ok / cells_total if cells_total else 1.0,
        "spurious": len(blocks) - len(used),
        "mismatches": [
            {"truth": t["kind"], "got": b.kind, "text": (t.get("text") or "table")[:40]}
            for t, b in found
            if t["kind"] != b.kind
        ]
        + [
            {"truth": t["kind"], "got": None, "text": (t.get("text") or "table")[:40]}
            for t, m in zip(truth, matches)
            if m is None
        ],
    }
