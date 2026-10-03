"""PDF backends: bytes → pages of positioned words, ruling lines and images.

:class:`PdfBackend` is the edge of the PDF pipeline. Everything after it — layout,
reading order, assembly — sees only the types defined here, in top-left page
coordinates (points). :class:`DoclingParseBackend` is the implementation, over
``docling-parse`` (MIT, C++; the ``pdf`` extra). It is the only module that
imports ``docling_parse`` or ``docling_core`` (PLAN D1, D2).

Words, not text lines, are the primitive: docling-parse's line cells merge a
table row's cells into one line ("Item Q1 Q2"), and the gaps between words are
what separate columns and table cells.
"""

from __future__ import annotations

import io
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict, List, Optional, Tuple

from operonx_kb.errors import DocumentParseError, MissingExtraError
from operonx_kb.model.ids import fingerprint

__all__ = [
    "Word",
    "Rule",
    "PdfPage",
    "PageRenderer",
    "PdfBackend",
    "DoclingParseBackend",
    "font_style",
]

BBox = Tuple[float, float, float, float]  # x0, y0, x1, y1; origin top-left, points


@dataclass
class Word:
    """One word with its box and font.

    Attributes:
        size: Font size estimate: the glyph box height, in points.
        bold, italic, mono: From the font name (:func:`font_style`); ``False``
            when the PDF does not name the font (base-14 fonts in docling-parse).
        angle: Direction of the baseline in degrees, counter-clockwise on the
            page: 0 for ordinary text, 90 for text read bottom to top (an arXiv
            stamp, a form's side label), 270 for top to bottom.
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font: str = ""
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    mono: bool = False
    angle: float = 0.0

    @property
    def bbox(self) -> BBox:
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def horizontal(self) -> bool:
        """Whether the word runs left to right (within 10 degrees)."""
        return abs((self.angle + 180.0) % 360.0 - 180.0) <= 10.0


@dataclass
class Rule:
    """An axis-aligned ruling line (table borders, separators)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def horizontal(self) -> bool:
        return abs(self.y1 - self.y0) <= abs(self.x1 - self.x0)


@dataclass
class PdfPage:
    """One page, in top-left coordinates."""

    page_no: int
    width: float
    height: float
    words: List[Word] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)
    images: List[BBox] = field(default_factory=list)


# Font-name tokens, after docling's utils/font_style.py: strip the subset
# prefix ("ABCDEF+"), split on separators and camelCase, look for weight and
# slant words or their abbreviations.
_BOLD = re.compile(
    r"(bold|black|heavy|semibold|demibold|extrabold|ultrabold|^demi$|^medi$|^bd$|^b$|^sb$)", re.I
)
_ITALIC = re.compile(r"(italic|oblique|kursiv|^it$|^i$|^bi$)", re.I)
_MONO = re.compile(r"(mono|courier|consolas|menlo|monaco|code|typewriter|fixed)", re.I)
# Families that encode the style in a code glued to the name, where the token
# rules above cannot see it:
# - TeX's Computer Modern and its EC/TC/SF (cm-super) encodings: CMBX10 and
#   SFBX1000 are bold, CMTT10 and SFTT0900 typewriter, CMTI10 and SFSL1000 slanted;
# - Linux Libertine/Biolinum: LinLibertineTB is bold, ...TI italic, ...TZ semibold.
# URW's Nimbus fonts call their bold "Medi" (NimbusRomNo9L-Medi), docling's
# conventions have "Demi" for semibold; both are the "^medi$|^demi$" tokens.
_TEX = re.compile(r"^(?:CM|EC|TC|SF)([A-Z]+)\d+$")
_LIBERTINE = re.compile(r"^Lin(?:Libertine|Biolinum)[TO]?([BZ]?)(I?)$")


def font_style(font_name: str) -> Tuple[bool, bool, bool]:
    """``(bold, italic, mono)`` from a PDF font name such as ``"/ABCDEF+Arial-BoldItalicMT"``."""
    name = font_name.lstrip("/")
    name = name.split("+", 1)[1] if re.match(r"^[A-Z]{6}\+", name) else name
    tokens = [t for t in re.split(r"[-_,+ ]|(?<=[a-z])(?=[A-Z])", name) if t]
    tokens = [t[:-2] if t.endswith("MT") and len(t) > 2 else t for t in tokens]
    bold = any(_BOLD.search(t) for t in tokens)
    italic = any(_ITALIC.search(t) for t in tokens)
    mono = bool(_MONO.search(name))
    tex = _TEX.match(name)
    if tex:
        code = tex.group(1)
        bold = bold or "BX" in code or code.startswith("B")
        italic = italic or code.endswith(("TI", "SL", "IT", "I"))
        mono = mono or "TT" in code
    libertine = _LIBERTINE.match(name)
    if libertine:
        bold = bold or bool(libertine.group(1))
        italic = italic or bool(libertine.group(2))
    return bold, italic, mono


class PageRenderer(ABC):
    """Page images of one open document, for layout models that look at pixels.

    Use as a context manager; the document is closed on exit.
    """

    @abstractmethod
    def render(self, page_no: int, scale: float = 1.0) -> Any:
        """Page ``page_no`` (1-based) as an RGB ``PIL.Image``; ``scale`` 1.0 is 72 dpi,
        so one pixel is one point."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "PageRenderer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class PdfiumRenderer(PageRenderer):
    """Renders with ``pypdfium2`` (Apache-2.0/BSD; the ``layout`` extra), as docling does."""

    def __init__(self, data: bytes, password: Optional[str] = None):
        try:
            import pypdfium2
        except ImportError as exc:
            raise MissingExtraError("Rendering PDF pages for the ML layout", "layout", exc) from exc
        self._doc = pypdfium2.PdfDocument(data, password=password)

    def render(self, page_no: int, scale: float = 1.0) -> Any:
        return self._doc[page_no - 1].render(scale=scale).to_pil().convert("RGB")

    def close(self) -> None:
        self._doc.close()


class PdfBackend(ABC):
    """Reads a PDF into :class:`PdfPage`\\ s."""

    name: str = ""
    version: str = "1"

    def config(self) -> Dict[str, Any]:
        return {}

    def fingerprint(self) -> str:
        cls = type(self)
        return fingerprint(f"{cls.__module__}.{cls.__qualname__}", self.version, self.config())

    def renderer(self, data: bytes, password: Optional[str] = None) -> PageRenderer:
        """Page images of the document (pypdfium2 by default)."""
        return PdfiumRenderer(data, password)

    @abstractmethod
    def pages(self, data: bytes, password: Optional[str] = None) -> List[PdfPage]:
        """Every page of the document.

        Raises:
            DocumentParseError: Not a PDF, encrypted without the password, or corrupt.
        """


class DoclingParseBackend(PdfBackend):
    """``docling-parse`` (the ``pdf`` extra) behind :class:`PdfBackend`."""

    name = "docling-parse"
    version = "1"

    def config(self) -> Dict[str, Any]:
        # The installed docling-parse version is part of the fingerprint: an
        # upgrade is a visible re-parse, never silent drift (track5 §6.3).
        try:
            return {"docling-parse": version("docling-parse")}
        except PackageNotFoundError:
            return {"docling-parse": None}

    def pages(self, data: bytes, password: Optional[str] = None) -> List[PdfPage]:
        try:
            from docling_parse.pdf_parser import ContentConfig, ContentLevel, DoclingPdfParser
        except ImportError as exc:
            raise MissingExtraError("PDF parsing", "pdf", exc) from exc
        if not data.startswith(b"%PDF") and b"%PDF" not in data[:1024]:
            raise DocumentParseError("not a PDF: the file does not start with %PDF")
        content = ContentConfig(
            char_cells_content_level=ContentLevel.SKIP,
            word_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
            line_cells_content_level=ContentLevel.SKIP,
            shapes_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
            bitmaps_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
            include_bitmap_bytes=False,
        )
        doc = None
        try:
            doc = DoclingPdfParser(loglevel="fatal").load(
                path_or_stream=io.BytesIO(data),
                lazy=True,
                password=password,
                content_config=content,
            )
            out = [self._page(no, page) for no, page in doc.iterate_pages()]
        except DocumentParseError:
            raise
        except Exception as exc:  # docling-parse raises RuntimeError for corrupt/encrypted files
            raise DocumentParseError(
                "docling-parse could not read the PDF", {"error": str(exc)}
            ) from exc
        finally:
            if doc is not None:
                doc.unload()
        return out

    @staticmethod
    def _page(page_no: int, page: Any) -> PdfPage:
        dim = page.dimension
        height = float(dim.height)
        width = float(dim.width)

        def flip(rect) -> BBox:
            box = rect.to_bounding_box()  # BOTTOMLEFT
            return (float(box.l), height - float(box.t), float(box.r), height - float(box.b))

        words: List[Word] = []
        for cell in page.word_cells:
            text = cell.text.strip()
            if not text:
                continue
            rect = cell.rect
            x0, y0, x1, y1 = flip(rect)
            bold, italic, mono = font_style(cell.font_name or "")
            # The cell is a quad r0..r3 (bottom-left origin) whose r0 -> r1 edge
            # is the baseline; its direction is the text direction and its
            # r0 -> r3 edge is the glyph height, whatever the rotation.
            angle = math.degrees(math.atan2(rect.r_y1 - rect.r_y0, rect.r_x1 - rect.r_x0)) % 360.0
            size = math.hypot(rect.r_x3 - rect.r_x0, rect.r_y3 - rect.r_y0) or (y1 - y0)
            words.append(
                Word(
                    text, x0, y0, x1, y1, cell.font_name or "", size, bold, italic, mono,
                    round(angle, 1),
                )
            )  # fmt: skip
        rules: List[Rule] = []
        for shape in page.shapes:
            pts = [(float(p.x), height - float(p.y)) for p in shape.points]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                if (abs(ay - by) < 1.0 or abs(ax - bx) < 1.0) and max(
                    abs(ax - bx), abs(ay - by)
                ) >= 4.0:
                    rules.append(Rule(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)))
        images = [flip(b.rect) for b in page.bitmap_resources]
        return PdfPage(
            page_no=page_no, width=width, height=height, words=words, rules=rules, images=images
        )
