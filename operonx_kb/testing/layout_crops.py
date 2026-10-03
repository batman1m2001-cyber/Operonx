"""Page crops: the words, rules and images of a region of a real PDF page, as JSON.

Layout regression tests replay a crop through the layout without the PDF
(and without the ``pdf`` extra): the backend's output for that region is the
fixture. ``scripts/crop_page.py`` writes one.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Sequence

from operonx_kb.pdf.backend import PdfPage, Rule, Word

__all__ = ["crop_page", "load_crop"]


def _inside(box: Sequence[float], region: Sequence[float]) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def _rounded(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()}


def crop_page(page: PdfPage, region: Sequence[float], source: str) -> Dict[str, Any]:
    """The part of ``page`` whose centres lie in ``region`` (points, top-left)."""
    return {
        "source": source,
        "page_no": page.page_no,
        "width": page.width,
        "height": page.height,
        "region": list(region),
        "words": [_rounded(asdict(w)) for w in page.words if _inside(w.bbox, region)],
        "rules": [
            _rounded(asdict(r)) for r in page.rules if _inside((r.x0, r.y0, r.x1, r.y1), region)
        ],
        "images": [[round(v, 2) for v in b] for b in page.images if _inside(b, region)],
    }


def load_crop(path: Path) -> PdfPage:
    """A crop written by :func:`crop_page`, as a page."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return PdfPage(
        page_no=data["page_no"],
        width=data["width"],
        height=data["height"],
        words=[Word(**w) for w in data["words"]],
        rules=[Rule(**r) for r in data["rules"]],
        images=[tuple(b) for b in data["images"]],
    )
