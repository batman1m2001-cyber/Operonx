"""PDF parsing: backend → layout → reading order → assembled blocks.

The stages are docling's StandardPdfPipeline in our own types: a
:class:`~operonx_kb.pdf.backend.PdfBackend` reads words, rules and images; a
:class:`~operonx_kb.pdf.layout.LayoutModel` labels blocks and orders them;
this module turns them into parser-neutral :class:`RawBlock`\\ s with page
regions normalised to ``[0, 1]``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from operonx_kb.model.document import Region
from operonx_kb.parsing.base import PageInfo, ParsedDoc, Parser, RawBlock
from operonx_kb.pdf.backend import DoclingParseBackend, PdfBackend
from operonx_kb.pdf.layout import HeuristicLayout, LayoutModel

__all__ = ["PdfParser"]


class PdfParser(Parser):
    """PDFs with a text layer.

    Args:
        backend: Reads words and geometry; default :class:`DoclingParseBackend`
            (the ``pdf`` extra).
        layout: Labels and orders blocks; default :class:`HeuristicLayout`.
        password: For encrypted PDFs.
    """

    name = "pdf"
    version = "1"
    mimes = frozenset({"application/pdf"})
    extensions = frozenset({".pdf"})

    def __init__(
        self,
        backend: Optional[PdfBackend] = None,
        layout: Optional[LayoutModel] = None,
        password: Optional[str] = None,
    ):
        self.backend = backend or DoclingParseBackend()
        self.layout_model = layout or HeuristicLayout()
        self.password = password

    def config(self) -> Dict[str, Any]:
        return {"backend": self.backend.fingerprint(), "layout": self.layout_model.fingerprint()}

    def parse(self, data: bytes, *, name: Optional[str] = None) -> ParsedDoc:
        started = time.perf_counter()
        pages = self.backend.pages(data, password=self.password)
        parsed_at = time.perf_counter()
        blocks = self.layout_model.layout(pages)
        dims = {p.page_no: (p.width, p.height) for p in pages}
        out: List[RawBlock] = []
        for b in blocks:
            regions = []
            for page_no, (x0, y0, x1, y1) in b.regions:
                w, h = dims[page_no]
                clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
                regions.append(Region(page_no=page_no, bbox=(clamp(x0 / w), clamp(y0 / h), clamp(x1 / w), clamp(y1 / h))))
            attrs = {k: v for k, v in b.attrs.items() if not k.startswith("_")}
            if b.kind == "table":
                attrs.update({"rows": b.rows or [], "header_rows": 1})
            out.append(RawBlock(kind=b.kind, text="" if b.kind == "table" else b.text, level=b.level, depth=b.depth, regions=regions, attrs=attrs))
        return ParsedDoc(
            blocks=out,
            pages=[PageInfo(page_no=p.page_no, width=p.width, height=p.height, text_layer=bool(p.words)) for p in pages],
            parser=self.name,
            stats={
                "pages": len(pages),
                "backend_s": round(parsed_at - started, 4),
                "layout_s": round(time.perf_counter() - parsed_at, 4),
            },
        )
