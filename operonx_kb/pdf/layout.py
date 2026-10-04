"""Layout analysis for PDF pages: words in, labelled blocks in reading order out.

:class:`LayoutModel` is the seam docling's layout model sits behind (the
``layout`` extra, phase K1b). :class:`HeuristicLayout` is the default, with no
ML. Its stages follow docling's StandardPdfPipeline (page parse → layout →
table structure → page assemble → reading order), with rules instead of models:

1. **Lines and segments.** Words sharing a baseline band form a line; a line is
   cut into segments where the gap between words exceeds ``segment_gap`` em —
   column gutters and table cells live in those gaps. Rotated words (an arXiv
   stamp, a form's side label) are turned upright and form blocks of their own.
2. **Furniture.** A segment in the top or bottom margin band is a page header or
   footer when its digit-masked text repeats on another page, or when it is a
   page number ("3", "Page 3 of 9", "- 3 -", "Trang 3"). Docling relies on its
   layout model's labels; repetition is the rule-based equivalent.
3. **Tables.** Ruling lines that connect into a grid (≥ 2 horizontal and ≥ 2
   vertical) are a table; words go to the grid cell holding their centre. Without
   rules, ≥ 3 consecutive lines whose segments line up in the same ≥ 3 columns
   (or 2 columns of short cells) are a table, unless a column wraps like prose.
   A grid with one used row or column only frames its text; a grid over
   bitmaps or miniature text is a figure.
4. **Blocks.** Segments are grouped by geometry: a segment continues the
   block right above it when each is the other's only neighbour across the
   line gap and the style, list-marker and first-line-indent rules agree; the
   gap allowed is the page's usual leading for that size plus ``paragraph_gap``
   em. Lines are cut only at gaps that leave a channel in the neighbouring
   lines (a justified line's wide spaces do not), and a font whose word boxes
   follow the ink gets one size per face.
5. **Reading order.** docling's rule-based reading order
   (``reading_order_rb.py``, see :mod:`operonx_kb.pdf.reading_order`) over the
   blocks, tables and figures of a page: columns come out one after the other.
   Gutters (an x-range few segments cross) only scope borderless tables.
6. **Labels.** Monospace → code; "Figure 2:" → caption; a list marker → list
   item (depth from indentation); larger or bold short blocks → headings, level
   from numbering ("2.1" → 2) else from docling's style key (size cluster,
   weight); the largest heading at the top of page 1 → title; small text low on
   the page starting with a mark → footnote.
7. **Merges.** A paragraph that ends mid-sentence at the bottom of a column
   and continues at the top of the next column or page is joined to its
   continuation (docling's predict_merges).
"""

from __future__ import annotations

import re
import statistics
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx_kb.model.ids import fingerprint
from operonx_kb.pdf.assemble import (
    continues,
    is_caption,
    join_continued,
    join_lines,
    split_list_marker,
)
from operonx_kb.pdf.backend import BBox, PageRenderer, PdfPage, Rule, Word
from operonx_kb.pdf.reading_order import reading_order

__all__ = ["Segment", "LayoutBlock", "LayoutModel", "HeuristicLayout"]


@dataclass
class Segment:
    """A run of words on one text line, without a large gap inside."""

    words: List[Word]
    page_no: int
    line: int  # index of the line on its page, top to bottom
    furniture: Optional[str] = None  # "page_header" | "page_footer"

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def bbox(self) -> BBox:
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def size(self) -> float:
        return statistics.median(w.size for w in self.words)

    def share(self, attr: str) -> float:
        chars = sum(len(w.text) for w in self.words) or 1
        return sum(len(w.text) for w in self.words if getattr(w, attr)) / chars


@dataclass
class LayoutBlock:
    """A labelled block, in reading order.

    Attributes:
        lines: The block's text lines (for code: kept as lines).
        regions: ``(page_no, bbox)`` in points, top-left; a merged block has several.
        rows: Table cells, for tables.
    """

    kind: str
    page_no: int
    lines: List[str] = field(default_factory=list)
    regions: List[Tuple[int, BBox]] = field(default_factory=list)
    size: float = 0.0
    bold: float = 0.0
    mono: float = 0.0
    level: Optional[int] = None
    depth: int = 0
    rows: Optional[List[List[str]]] = None
    attrs: Dict[str, Any] = field(default_factory=dict)
    x0: float = 0.0
    column: Tuple[int, int] = (0, 0)  # (zone, column) on its page

    @property
    def text(self) -> str:
        if self.kind == "code":
            return "\n".join(self.lines)
        return join_lines(self.lines)


class LayoutModel(ABC):
    """Labels a document's pages and orders the blocks.

    Attributes:
        needs_images: The model looks at page images; the parser then opens a
            :class:`~operonx_kb.pdf.backend.PageRenderer` for it.
    """

    name: str = ""
    version: str = "1"
    needs_images: bool = False

    def config(self) -> Dict[str, Any]:
        return {}

    def fingerprint(self) -> str:
        cls = type(self)
        return fingerprint(f"{cls.__module__}.{cls.__qualname__}", self.version, self.config())

    @abstractmethod
    def layout(
        self, pages: Sequence[PdfPage], renderer: Optional[PageRenderer] = None
    ) -> List[LayoutBlock]:
        """Blocks of every page, in reading order. ``renderer`` is given when
        :attr:`needs_images` is set."""


# ── helpers ─────────────────────────────────────────────────────────────

_PAGE_NUMBER = re.compile(
    r"^(page|trang|p\.?|pg\.?)?\s*[-–—(]?\s*\d{1,4}\s*[-–—)]?(\s*(of|/|trên)\s*\d{1,4})?$", re.I
)
_HEADING_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
_FOOTNOTE_MARK = re.compile(r"^(\d{1,2}|[*†‡§¶])\s*\S")


def _mask(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.lower())).strip()


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def set_style(block: "LayoutBlock", segments: List[Segment]) -> None:
    """Font size and the bold and monospace shares of a block, from its segments."""
    words = [w for seg in segments for w in seg.words]
    if not words:
        return
    chars = sum(len(w.text) for w in words) or 1
    block.size = sum(w.size * len(w.text) for w in words) / chars
    block.bold = sum(len(w.text) for w in words if w.bold) / chars
    block.mono = sum(len(w.text) for w in words if w.mono) / chars


_X_HEIGHT_ONLY = re.compile(r"^[acemnorsuvwxz]{2,}$")
_ASCENDERS_ONLY = re.compile(r"^(?=.*[bdfhklt])[a-z]{2,}$")
_DESCENDERS = re.compile(r"[gjpqy]")


def font_sizes(pages: Sequence[PdfPage]) -> Dict[int, float]:
    """A font size for every word that does not depend on its letters: ``id(word) -> size``.

    docling-parse measures a word by its font's ascent and descent when the
    font declares them, so all words of one face and size are equally tall.
    Some fonts (the newspaper's) give ink boxes instead: "unseren" is 4.5 pt,
    "Minuten" 6.5, "gelesen" 8.3, in one 7 pt face, enough to split lines and
    to make a line with a "g" look like a heading. A font is ink-measured when
    its x-height-only words ("an", "unseren") are clearly shorter than its
    ascender words ("die", "hat"). For those fonts each word takes the most
    common height around its own (between 0.6 and 1.4 times it, the span of
    x-height to descender), picked from the most frequent height down; other
    fonts keep their heights.
    """
    by_font: Dict[str, List[Word]] = defaultdict(list)
    for page in pages:
        for w in page.words:
            by_font[w.font].append(w)
    out: Dict[int, float] = {}
    for words in by_font.values():
        low = [w.size for w in words if _X_HEIGHT_ONLY.match(w.text)]
        high = [
            w.size
            for w in words
            if _ASCENDERS_ONLY.match(w.text) and not _DESCENDERS.search(w.text)
        ]
        ink = (
            len(low) >= 5
            and len(high) >= 5
            and statistics.median(low) < 0.85 * statistics.median(high)
        )
        if not ink:
            out.update((id(w), w.size) for w in words)
            continue
        left = list(words)
        while left:
            counts = Counter(round(w.size, 1) for w in left)
            mode = counts.most_common(1)[0][0]
            taken = [w for w in left if 0.6 * mode <= w.size <= 1.4 * mode]
            out.update((id(w), mode) for w in taken)
            left = [w for w in left if not 0.6 * mode <= w.size <= 1.4 * mode]
    return out


def _size_key(size: float) -> float:
    return round(size * 2) / 2


def _crosses(x0: float, x1: float, gutters: List[Tuple[float, float]]) -> bool:
    """Whether ``[x0, x1]`` reaches across a gutter (a ragged line ending inside one does not)."""
    return any(x0 < g0 + 1.0 and x1 > g1 - 1.0 for g0, g1 in gutters)


def _contains(outer: BBox, inner: BBox) -> bool:
    """Whether ``inner``'s centre lies in ``outer``."""
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _wrapped_prose(rows: List[List[str]]) -> bool:
    """Whether a column of ``rows`` reads as text wrapped from line to line.

    Narrow newspaper columns and side-by-side labels line up like a borderless
    table. A table's cells stand alone; wrapped text breaks mid-sentence: a
    line of three or more words without closing punctuation followed by one
    starting in lower case, or a line ending in a hyphenated word. A column
    where half the line pairs break like that is prose.
    """
    for c in range(max(len(r) for r in rows)):
        cells = [r[c] for r in rows if c < len(r) and r[c]]
        pairs = list(zip(cells, cells[1:]))
        wraps = sum(
            1
            for a, b in pairs
            if re.search(r"\w-$", a)
            or (
                len(a.split()) >= 3
                and not a.endswith((".", ":", ";", "!", "?"))
                and b[:1].islower()
            )
        )
        if pairs and wraps >= 0.5 * len(pairs):
            return True
    return False


def _cluster(values: List[float], tol: float) -> List[float]:
    out: List[float] = []
    for v in sorted(values):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


class HeuristicLayout(LayoutModel):
    """Rule-based layout (see the module docstring for the rules).

    Args:
        margin_band: Height of the header/footer bands, as a fraction of the page.
        segment_gap: Word gap, in em, that splits a line into segments.
        paragraph_gap: Vertical gap, in em, beyond a page's usual gap between
            the lines of one paragraph, that starts a new block (the whole
            gap, when the page has too few lines of that size to tell).
        heading_ratio: Font-size ratio to the body size that makes a heading.
        gutter_coverage: A gutter is crossed by at most this share of the
            densest x-position's segments.
    """

    name = "heuristic"
    version = "2"

    def __init__(
        self,
        margin_band: float = 0.08,
        segment_gap: float = 1.0,
        paragraph_gap: float = 0.5,
        heading_ratio: float = 1.15,
        gutter_coverage: float = 0.35,
    ):
        self.margin_band = margin_band
        self.segment_gap = segment_gap
        self.paragraph_gap = paragraph_gap
        self.heading_ratio = heading_ratio
        self.gutter_coverage = gutter_coverage

    def config(self) -> Dict[str, Any]:
        return {
            "margin_band": self.margin_band,
            "segment_gap": self.segment_gap,
            "paragraph_gap": self.paragraph_gap,
            "heading_ratio": self.heading_ratio,
            "gutter_coverage": self.gutter_coverage,
        }

    # -- 1. lines and segments ----------------------------------------------

    def segments(self, page: PdfPage, words: List[Word]) -> List[Segment]:
        lines: List[List[Word]] = []
        for word in sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0)):
            h = max(word.y1 - word.y0, 0.1)
            for line in reversed(lines[-4:]):
                ly0 = min(w.y0 for w in line)
                ly1 = max(w.y1 for w in line)
                if _overlap(word.y0, word.y1, ly0, ly1) >= 0.5 * min(h, max(ly1 - ly0, 0.1)):
                    line.append(word)
                    break
            else:
                lines.append([word])
        lines.sort(key=lambda ln: min(w.y0 for w in ln))
        for line in lines:
            line.sort(key=lambda w: w.x0)
        out: List[Segment] = []
        for index, line in enumerate(lines):
            current = [line[0]]
            for prev, word in zip(line, line[1:]):
                em = max(min(prev.size, word.size), 1.0)
                if word.x0 - prev.x1 > self.segment_gap * em and self._channel(
                    lines, index, prev.x1, word.x0, em
                ):
                    out.append(Segment(current, page.page_no, index))
                    current = []
                current.append(word)
            out.append(Segment(current, page.page_no, index))
        return out

    @staticmethod
    def _channel(lines: List[List[Word]], index: int, a: float, b: float, em: float) -> bool:
        """Whether the gap ``(a, b)`` on line ``index`` is a column or cell boundary.

        A justified line stretches its spaces past the cut width ("strategies
        may significantly enhance ..."); a boundary between columns or table
        cells leaves the neighbouring lines blank beside it too. The gap cuts
        the line when a neighbouring line (at most a line height away) has no text
        in the 1.5 em before the gap's end (where the next column or cell
        starts) or after its start (where a cell ends), or a 1.5 em gap of its
        own inside this one (a centred grid), or when the line has no near
        neighbour.
        """
        line = lines[index]
        y0, y1 = min(w.y0 for w in line), max(w.y1 for w in line)
        height = max(y1 - y0, 1.0)
        reach = min(1.5 * em, b - a)
        zones = ((b - reach, b), (a, a + reach))
        near = False
        for j in (index - 1, index + 1):
            if not 0 <= j < len(lines):
                continue
            other = lines[j]
            oy0, oy1 = min(w.y0 for w in other), max(w.y1 for w in other)
            if max(oy0 - y1, y0 - oy1) > height:
                continue
            near = True
            for z0, z1 in zones:
                if not any(_overlap(w.x0, w.x1, z0, z1) > 0.1 for w in other):
                    return True
            # A centred grid (author blocks) shifts its channel from line to line:
            # the neighbour then has a gap of its own inside this one.
            inside = sorted((max(w.x0, a), min(w.x1, b)) for w in other if w.x1 > a and w.x0 < b)
            edge = a
            for x0, x1 in inside:
                if x0 - edge >= reach and edge > a:
                    return True
                edge = max(edge, x1)
        return not near

    # -- 1b. rotated text ------------------------------------------------------

    def rotated_blocks(
        self, page: PdfPage, words: List[Word], segs: List[Segment]
    ) -> List[LayoutBlock]:
        """Blocks of text that does not run left to right.

        Rotated words never join horizontal lines: their tall boxes would chain
        every line they cross into one (an arXiv stamp beside an abstract, a
        form's side label). Each direction is turned upright — 90° text (read
        bottom to top) has its first line on the left, 270° text on the right —
        and cut into lines and blocks like horizontal text. A block outside the
        horizontal text's x-range sits in the margin: a page header, as docling
        labels the arXiv stamp. Others are paragraphs.
        """
        if not words:
            return []
        w_, h_ = page.width, page.height
        frames = {
            90: lambda w: (h_ - w.y1, w.x0, h_ - w.y0, w.x1),
            180: lambda w: (w_ - w.x1, h_ - w.y1, w_ - w.x0, h_ - w.y0),
            270: lambda w: (w.y0, w_ - w.x1, w.y1, w_ - w.x0),
        }
        left = min((s.x0 for s in segs), default=w_)
        right = max((s.x1 for s in segs), default=0.0)
        out: List[LayoutBlock] = []
        for angle, turn in frames.items():
            group = [w for w in words if abs((w.angle - angle + 180.0) % 360.0 - 180.0) < 45.0]
            if not group:
                continue
            upright: Dict[int, Word] = {}
            turned = []
            for w in group:
                x0, y0, x1, y1 = turn(w)
                t = Word(w.text, x0, y0, x1, y1, w.font, w.size, w.bold, w.italic, w.mono)
                upright[id(t)] = w
                turned.append(t)
            lines = self.segments(page, turned)
            blocks: List[List[Segment]] = []
            for s in lines:
                prev = blocks[-1][-1] if blocks else None
                if (
                    prev is not None
                    and s.y0 - prev.y1 <= self.paragraph_gap * max(s.size, prev.size)
                    and _overlap(s.x0, s.x1, prev.x0, prev.x1) > 0
                ):
                    blocks[-1].append(s)
                else:
                    blocks.append([s])
            for members in blocks:
                orig = [upright[id(w)] for s in members for w in s.words]
                bbox = (
                    min(w.x0 for w in orig),
                    min(w.y0 for w in orig),
                    max(w.x1 for w in orig),
                    max(w.y1 for w in orig),
                )
                margin = bbox[2] <= left or bbox[0] >= right
                block = LayoutBlock(
                    kind="page_header" if margin else "paragraph",
                    page_no=page.page_no,
                    lines=[s.text for s in members],
                    regions=[(page.page_no, bbox)],
                    x0=bbox[0],
                )
                set_style(block, members)
                out.append(block)
        return out

    # -- 2. furniture ------------------------------------------------------------

    def mark_furniture(self, pages: Sequence[PdfPage], segs: Dict[int, List[Segment]]) -> None:
        seen: Dict[Tuple[str, str], set] = defaultdict(set)
        candidates: List[Tuple[Segment, str]] = []
        for page in pages:
            for s in segs[page.page_no]:
                if s.y1 <= self.margin_band * page.height:
                    band = "page_header"
                elif s.y0 >= (1 - self.margin_band) * page.height:
                    band = "page_footer"
                else:
                    continue
                candidates.append((s, band))
                seen[(band, _mask(s.text))].add(page.page_no)
        for s, band in candidates:
            if len(seen[(band, _mask(s.text))]) >= 2 or _PAGE_NUMBER.match(s.text.strip()):
                s.furniture = band

    # -- 3. tables -----------------------------------------------------------------

    def ruled_tables(self, page: PdfPage) -> List[Tuple[BBox, List[float], List[float]]]:
        """Grids of ruling lines: (bbox, column x boundaries, row y boundaries)."""
        rules = page.rules
        parent = list(range(len(rules)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        tol = 2.0
        for i, a in enumerate(rules):
            for j in range(i + 1, len(rules)):
                b = rules[j]
                if (
                    a.x0 - tol <= b.x1
                    and b.x0 - tol <= a.x1
                    and a.y0 - tol <= b.y1
                    and b.y0 - tol <= a.y1
                ):
                    parent[find(i)] = find(j)
        groups: Dict[int, List[Rule]] = defaultdict(list)
        for i, r in enumerate(rules):
            groups[find(i)].append(r)
        grids = []
        for group in groups.values():
            hs = [r for r in group if r.horizontal]
            vs = [r for r in group if not r.horizontal]
            if len(hs) < 2 or len(vs) < 2:
                continue
            xs = _cluster([r.x0 for r in vs], tol)
            ys = _cluster([r.y0 for r in hs], tol)
            if len(xs) < 2 or len(ys) < 2:
                continue
            bbox = (
                min(r.x0 for r in group),
                min(r.y0 for r in group),
                max(r.x1 for r in group),
                max(r.y1 for r in group),
            )
            grids.append((bbox, xs, ys))
        return grids

    @staticmethod
    def fill_grid(words: List[Word], xs: List[float], ys: List[float]) -> List[List[str]]:
        cells: Dict[Tuple[int, int], List[Word]] = defaultdict(list)
        for w in words:
            cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
            col = max(0, min(len(xs) - 2, sum(1 for x in xs if x <= cx) - 1))
            row = max(0, min(len(ys) - 2, sum(1 for y in ys if y <= cy) - 1))
            cells[(row, col)].append(w)
        rows = []
        for r in range(len(ys) - 1):
            row = []
            for c in range(len(xs) - 1):
                ws = sorted(cells.get((r, c), []), key=lambda w: (round(w.y0), w.x0))
                row.append(" ".join(w.text for w in ws))
            if any(row):
                rows.append(row)
        return rows

    @staticmethod
    def _grid_kind(
        rows: List[List[str]],
        bbox: BBox,
        images: Sequence[BBox],
        words: List[Word],
        text_size: float,
    ) -> Optional[str]:
        """What a ruled grid holds: ``"table"``, ``"figure"``, or ``None`` for a frame.

        A table has text in at least two rows and two columns; a box around a
        label, a listing or a slide (one used column, or one row) only frames
        its text. A grid drawn over pictures is a figure's panels: bitmaps cover
        half of it, or its text is miniature (page thumbnails: under 0.6 of the
        size of the page's other text).
        """
        used_cols = {c for row in rows for c, cell in enumerate(row) if cell}
        area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 1e-6)
        covered = sum(
            _overlap(b[0], b[2], bbox[0], bbox[2]) * _overlap(b[1], b[3], bbox[1], bbox[3])
            for b in images
        )
        if covered >= 0.5 * area or statistics.median(w.size for w in words) < 0.6 * text_size:
            return "figure"
        if len(rows) < 2 or len(used_cols) < 2:
            return None
        return "table"

    def aligned_tables(self, segs: List[Segment]) -> List[Tuple[List[Segment], List[List[str]]]]:
        """Borderless tables: runs of lines whose segments share column intervals."""
        by_line: Dict[int, List[Segment]] = defaultdict(list)
        for s in segs:
            by_line[s.line].append(s)
        line_ids = sorted(by_line)
        tables = []
        i = 0
        while i < len(line_ids):
            run = [line_ids[i]]
            cols = [(s.x0, s.x1) for s in sorted(by_line[line_ids[i]], key=lambda s: s.x0)]
            if len(cols) < 2:
                i += 1
                continue
            j = i + 1
            while j < len(line_ids):
                line = sorted(by_line[line_ids[j]], key=lambda s: s.x0)
                prev = by_line[run[-1]]
                gap = min(s.y0 for s in line) - max(s.y1 for s in prev)
                size = statistics.median(s.size for s in prev)
                if gap > 1.5 * size or len(line) < 2:
                    break
                matched = [
                    next(
                        (k for k, (c0, c1) in enumerate(cols) if _overlap(s.x0, s.x1, c0, c1) > 0),
                        None,
                    )
                    for s in line
                ]
                if None in matched or len(set(matched)) != len(matched):
                    break
                for s, k in zip(line, matched):
                    cols[k] = (min(cols[k][0], s.x0), max(cols[k][1], s.x1))
                run.append(line_ids[j])
                j += 1
            members = [s for ln in run for s in by_line[ln]]
            mean_len = statistics.mean(len(s.text) for s in members)
            # Prose in columns also lines up; table cells are short.
            if len(run) >= 3 and mean_len <= (30 if len(cols) >= 3 else 25):
                rows = []
                for ln in run:
                    row = [""] * len(cols)
                    for s in by_line[ln]:
                        k = next(
                            k for k, (c0, c1) in enumerate(cols) if _overlap(s.x0, s.x1, c0, c1) > 0
                        )
                        row[k] = (row[k] + " " + s.text).strip()
                    rows.append(row)
                if not _wrapped_prose(rows):
                    tables.append((members, rows))
                    i = j
                    continue
            i += 1
        return tables

    # -- 4. columns and reading order ---------------------------------------------

    def gutters(self, segs: List[Segment], body: float) -> List[Tuple[float, float]]:
        if len(segs) < 6:
            return []
        lo = int(min(s.x0 for s in segs))
        hi = int(max(s.x1 for s in segs)) + 1
        cover = [0] * (hi - lo + 1)
        for s in segs:
            for x in range(int(s.x0) - lo, int(s.x1) - lo + 1):
                cover[x] += 1
        peak = max(cover)
        height = max(s.y1 for s in segs) - min(s.y0 for s in segs)
        limit = self.gutter_coverage * peak
        out: List[Tuple[float, float]] = []
        x = 0
        min_width = max(8.0, 0.8 * body)
        while x < len(cover):
            if cover[x] <= limit:
                start = x
                while x < len(cover) and cover[x] <= limit:
                    x += 1
                g0, g1 = lo + start, lo + x
                if g1 - g0 >= min_width:
                    left = [s for s in segs if s.x1 <= g0 + 1]
                    right = [s for s in segs if s.x0 >= g1 - 1]
                    # Columns are tall: text on both sides of a gutter must run
                    # down a good part of the page, or this is a gap inside a
                    # table or a form, not a column break.
                    if all(
                        len(side) >= 0.2 * len(segs) and self._extent(side) >= 0.3 * height
                        for side in (left, right)
                    ):
                        out.append((g0, g1))
            else:
                x += 1
        return out

    @staticmethod
    def _extent(segs: List[Segment]) -> float:
        return max(s.y1 for s in segs) - min(s.y0 for s in segs)

    # -- 5-7. blocks, labels, merges -------------------------------------------

    def layout(
        self, pages: Sequence[PdfPage], renderer: Optional[PageRenderer] = None
    ) -> List[LayoutBlock]:
        segs: Dict[int, List[Segment]] = {}
        rotated: Dict[int, List[Word]] = {}
        sized = font_sizes(pages)
        for page in pages:
            big = [
                b
                for b in page.images
                if (b[2] - b[0]) * (b[3] - b[1]) > 0.5 * page.width * page.height
            ]
            words = [
                replace(w, size=sized[id(w)])
                for w in page.words
                if not any(
                    b[0] <= (w.x0 + w.x1) / 2 <= b[2] and b[1] <= (w.y0 + w.y1) / 2 <= b[3]
                    for b in page.images
                    if b not in big
                )
            ]
            segs[page.page_no] = self.segments(page, [w for w in words if w.horizontal])
            rotated[page.page_no] = [w for w in words if not w.horizontal]
        self.mark_furniture(pages, segs)
        sizes: Counter = Counter()
        for page_segs in segs.values():
            for s in page_segs:
                for w in s.words:
                    sizes[round(w.size * 2) / 2] += len(w.text)
        body = sizes.most_common(1)[0][0] if sizes else 10.0

        blocks: List[LayoutBlock] = []
        for page in pages:
            blocks.extend(self._page_blocks(page, segs[page.page_no], rotated[page.page_no], body))
        self._label(blocks, pages, body)
        return self._merge(blocks)

    def _page_blocks(
        self, page: PdfPage, segs: List[Segment], rotated: List[Word], body: float
    ) -> List[LayoutBlock]:
        furniture = [s for s in segs if s.furniture]
        flow = [s for s in segs if not s.furniture]
        out: List[LayoutBlock] = []
        side_blocks = self.rotated_blocks(page, rotated, segs)
        out.extend(b for b in side_blocks if b.kind == "page_header")
        for s in furniture:
            out.append(
                LayoutBlock(
                    kind=s.furniture,
                    page_no=page.page_no,
                    lines=[s.text],
                    regions=[(page.page_no, s.bbox)],
                    size=s.size,
                )
            )

        items: List[Tuple[BBox, Any]] = []
        figures: List[BBox] = []
        for bbox, xs, ys in self.ruled_tables(page):
            inside = [
                s
                for s in flow
                if bbox[0] - 1 <= (s.x0 + s.x1) / 2 <= bbox[2] + 1
                and bbox[1] - 1 <= (s.y0 + s.y1) / 2 <= bbox[3] + 1
            ]
            if not inside:
                continue
            words = [w for s in inside for w in s.words]
            rows = self.fill_grid(words, xs, ys)
            around = [s.size for s in flow if s not in inside]
            text_size = statistics.median(around) if around else body
            kind = self._grid_kind(rows, bbox, page.images, words, text_size)
            if kind is None:
                continue  # a frame around text: its words stay in the flow
            flow = [s for s in flow if s not in inside]
            if kind == "figure":
                figures.append(bbox)
                items.append(
                    (
                        bbox,
                        LayoutBlock(
                            kind="figure", page_no=page.page_no, regions=[(page.page_no, bbox)]
                        ),
                    )
                )
                continue
            items.append(
                (
                    bbox,
                    LayoutBlock(
                        kind="table",
                        page_no=page.page_no,
                        rows=rows,
                        regions=[(page.page_no, bbox)],
                    ),
                )
            )
        for b in page.images:
            if any(_contains(f, b) for f in figures):
                continue  # a panel of a figure already found
            if (b[2] - b[0]) * (b[3] - b[1]) <= 0.5 * page.width * page.height:
                items.append(
                    (
                        b,
                        LayoutBlock(
                            kind="figure", page_no=page.page_no, regions=[(page.page_no, b)]
                        ),
                    )
                )

        gutters = self.gutters(flow, body)
        # Borderless tables, inside one column or across the page.
        for column_segs in self._by_column(flow, gutters):
            for members, rows in self.aligned_tables(column_segs):
                flow = [s for s in flow if s not in members]
                bbox = (
                    min(s.x0 for s in members),
                    min(s.y0 for s in members),
                    max(s.x1 for s in members),
                    max(s.y1 for s in members),
                )
                items.append(
                    (
                        bbox,
                        LayoutBlock(
                            kind="table",
                            page_no=page.page_no,
                            rows=rows,
                            regions=[(page.page_no, bbox)],
                            attrs={"ruled": False},
                        ),
                    )
                )
        items.extend((b.regions[0][1], b) for b in self.text_blocks(flow))
        items.extend((b.regions[0][1], b) for b in side_blocks if b.kind != "page_header")
        items.sort(key=lambda it: (round(it[0][1], 1), it[0][0]))
        order = reading_order(
            [box for box, _ in items],
            page.width,
            page.height,
            graphic=[b.kind in ("table", "figure") for _, b in items],
            row_height=3 * body,
        )
        for i in order:
            box, block = items[i]
            block.column = (0, self._column(box, gutters))
            out.append(block)
        return out

    @staticmethod
    def _column(box: BBox, gutters: List[Tuple[float, float]]) -> int:
        cx = (box[0] + box[2]) / 2
        return sum(1 for g0, g1 in gutters if cx >= g1 - 1)

    def text_blocks(self, segs: List[Segment]) -> List[LayoutBlock]:
        """Group segments into blocks by geometry, not by column.

        Segments are visited top to bottom. A segment continues the block right
        above it when that block is the only one within a paragraph gap whose
        extent it overlaps, the segment is the only one on its line under that
        block (or the block's last line spans all of them: a line cut by a wide
        gap), and :meth:`_continues_block` agrees (size, weight, list marker,
        indent). Otherwise it starts a block. Segments of one line join only in
        that wide-line case: the gap that cut them is a column or cell boundary
        (author grids, form labels, columns without a detectable gutter).
        """
        ordered = sorted(segs, key=lambda s: (s.y0, s.x0))
        leading = self._leading(ordered)
        by_line: Dict[int, List[Segment]] = defaultdict(list)
        for s in ordered:
            by_line[s.line].append(s)
        members: Dict[int, List[Segment]] = {}
        extent: Dict[int, Tuple[float, float]] = {}
        out: List[LayoutBlock] = []
        open_blocks: List[LayoutBlock] = []
        for s in ordered:
            size = max(s.size, 1.0)
            limit = 1.5 * size  # the final word is _continues_block's
            cands = []
            for b in open_blocks:
                last = members[id(b)][-1]
                x0, x1 = extent[id(b)]
                if last.line == s.line:
                    prev = [m for m in members[id(b)] if m.line != s.line]
                    if 0 <= s.x0 - last.x1 <= 3 * size and prev and s.x1 <= prev[-1].x1 + size:
                        cands.append(b)  # the next part of a cut line under a wide one
                    continue
                if -0.5 * size <= s.y0 - last.y1 <= limit and _overlap(x0, x1, s.x0, s.x1) > 0:
                    cands.append(b)
            target: Optional[LayoutBlock] = None
            if len(cands) > 1 and self._joins_cut_line(cands, members, s):
                # A one-line label cut from its text by a wide gap ("Table 1:   Both
                # ...") over a full line: the parts are one line of one block.
                cands.sort(key=lambda b: members[id(b)][0].x0)
                head = cands[0]
                for b in cands[1:]:
                    head.lines[-1] += " " + b.lines[-1]
                    for m in members[id(b)]:
                        self._grow(head, m)
                    members[id(head)].extend(members[id(b)])
                    x0, x1 = extent[id(head)]
                    bx0, bx1 = extent[id(b)]
                    extent[id(head)] = (min(x0, bx0), max(x1, bx1))
                    out.remove(b)
                    open_blocks.remove(b)
                cands = [head]
            if len(cands) == 1:
                b = cands[0]
                last = members[id(b)][-1]
                x0, x1 = extent[id(b)]
                if last.line == s.line:
                    prev = [m for m in members[id(b)] if m.line != s.line]
                    if prev and self._spans(prev[-1], by_line[s.line], (x0, x1)):
                        target = b
                else:
                    under = [t for t in by_line[s.line] if _overlap(x0, x1, t.x0, t.x1) > 0]
                    if (len(under) == 1 or self._spans(last, under, (x0, x1))) and (
                        self._continues_block(last, s, b, leading)
                    ):
                        target = b
            if target is None:
                target = LayoutBlock(
                    kind="paragraph",
                    page_no=s.page_no,
                    lines=[s.text],
                    regions=[(s.page_no, s.bbox)],
                    x0=s.x0,
                )
                members[id(target)] = [s]
                extent[id(target)] = (s.x0, s.x1)
                out.append(target)
                open_blocks.append(target)
            else:
                last = members[id(target)][-1]
                if s.line == last.line:
                    target.lines[-1] += " " + s.text
                else:
                    target.lines.append(s.text)
                self._grow(target, s)
                members[id(target)].append(s)
                x0, x1 = extent[id(target)]
                extent[id(target)] = (min(x0, s.x0), max(x1, s.x1))
            # Blocks whose last line is far above can take nothing more.
            open_blocks = [b for b in open_blocks if s.y0 - members[id(b)][-1].y1 <= 3 * size]
        for block in out:
            set_style(block, members[id(block)])
        return out

    @staticmethod
    def _joins_cut_line(
        cands: List[LayoutBlock], members: Dict[int, List[Segment]], s: Segment
    ) -> bool:
        """Whether ``cands`` are one-line blocks of one line that ``s`` runs under."""
        lines = [members[id(b)] for b in cands]
        if any(len({m.line for m in ms}) != 1 for ms in lines):
            return False
        if len({ms[0].line for ms in lines}) != 1 or s.line == lines[0][0].line:
            return False
        em = max(s.size, 1.0)
        x0 = min(m.x0 for ms in lines for m in ms)
        x1 = max(m.x1 for ms in lines for m in ms)
        return abs(s.x0 - x0) <= em and s.x1 >= x1 - em

    @staticmethod
    def _spans(wide: Segment, parts: List[Segment], extent: Tuple[float, float]) -> bool:
        """Whether the cut line ``parts`` lies under the block line ``wide``."""
        em = max(wide.size, 1.0)
        return all(
            p.x0 >= extent[0] - em and p.x1 <= max(extent[1], wide.x1) + em for p in parts
        ) and (len(parts) > 1)

    def _by_column(
        self, flow: List[Segment], gutters: List[Tuple[float, float]]
    ) -> List[List[Segment]]:
        groups: Dict[int, List[Segment]] = defaultdict(list)
        for s in flow:
            if _crosses(s.x0, s.x1, gutters):
                groups[-1].append(s)
            else:
                cx = (s.x0 + s.x1) / 2
                groups[sum(1 for g0, g1 in gutters if cx >= g1 - 1)].append(s)
        return list(groups.values())

    def _leading(self, ordered: List[Segment]) -> Dict[float, float]:
        """The usual gap between the lines of a paragraph, per font size.

        Gaps are measured between a segment and the next one below that it
        overlaps, at the same size; the most common gap (to 0.25 pt) of a size
        with at least five such pairs is its leading. Line spacing differs from
        face to face (and box heights from font to font), so a fixed share of
        the size cannot tell a line break from a paragraph break everywhere.
        """
        gaps: Dict[float, List[float]] = defaultdict(list)
        for k, u in enumerate(ordered):
            size = max(u.size, 1.0)
            for s in ordered[k + 1 :]:
                if s.y0 - u.y1 > 1.5 * size:
                    break
                if s.line == u.line or _overlap(u.x0, u.x1, s.x0, s.x1) <= 0:
                    continue
                if abs(s.size - u.size) <= 0.1 * size and s.y0 - u.y1 >= -0.5 * size:
                    gaps[_size_key(size)].append(round((s.y0 - u.y1) * 4) / 4)
                break
        return {
            key: Counter(values).most_common(1)[0][0]
            for key, values in gaps.items()
            if len(values) >= 5
        }

    def _continues_block(
        self,
        last: Segment,
        s: Segment,
        block: LayoutBlock,
        leading: Optional[Dict[float, float]] = None,
    ) -> bool:
        if s.line == last.line:
            return True
        size = max(last.size, s.size, 1.0)
        gap = s.y0 - last.y1
        usual = (leading or {}).get(_size_key(size))
        limit = (
            max(usual, 0.0) + self.paragraph_gap * size
            if usual is not None
            else self.paragraph_gap * size
        )
        if gap < -0.5 * size or gap > limit:
            return False
        if abs(s.size - last.size) > 0.1 * size:
            return False
        if s.share("mono") >= 0.9 and last.share("mono") >= 0.9:
            return True  # code: indentation and markers are content, not structure
        for style in ("bold", "italic"):
            a, b = last.share(style), s.share(style)
            if (a >= 0.9 and b < 0.5) or (a < 0.5 and b >= 0.9):
                # A whole bold or italic line next to a plain one: a heading or a
                # caption meets body text. A line that is only partly bold (a
                # run-in heading, "Graph Neural networks: Graph ...") never breaks.
                return False
        if split_list_marker(s.text) is not None and not self._full(last, block, s):
            # A marker-like start ("826. For …") right after a line that runs to
            # the block's edge is wrapped text, not a new list item.
            return False
        if split_list_marker(block.lines[0]) is not None:
            return s.x0 > block.x0 + 0.3 * size  # a list item's wrapped lines hang under its text
        left = min(block.x0, block.regions[-1][1][0])  # the first line may be indented
        if s.x0 > left + 0.8 * size and s.x0 - last.x0 > 0.8 * size:
            return False  # first-line indent of a new paragraph
        return True

    @staticmethod
    def _full(last: Segment, block: LayoutBlock, s: Segment) -> bool:
        """Whether ``last`` (the block's latest line) was wrapped before ``s``.

        It was when it starts at the block's left edge and the first word of
        ``s`` would not have fitted after it within the block (or within ``s``):
        the typesetter had to break there. Ragged-right text breaks well short
        of the edge, so a fixed margin is not enough.
        """
        right = max(max(r[1][2] for r in block.regions), s.x1)
        first = s.words[0]
        space = 0.3 * max(last.size, 1.0)
        return last.x0 <= block.x0 + 1.0 and last.x1 + space + (first.x1 - first.x0) > right

    @staticmethod
    def _grow(block: LayoutBlock, s: Segment) -> None:
        page, (x0, y0, x1, y1) = block.regions[-1]
        block.regions[-1] = (page, (min(x0, s.x0), min(y0, s.y0), max(x1, s.x1), max(y1, s.y1)))

    def _label(self, blocks: List[LayoutBlock], pages: Sequence[PdfPage], body: float) -> None:
        heights = {p.page_no: p.height for p in pages}
        headings: List[LayoutBlock] = []
        list_x: Dict[Tuple[int, Tuple[int, int]], List[float]] = defaultdict(list)
        for b in blocks:
            if b.kind != "paragraph":
                continue
            text = b.text
            words = len(text.split())
            page_h = heights.get(b.page_no, 1.0)
            y0 = b.regions[0][1][1]
            if b.mono >= 0.9 and (len(b.lines) > 1 or len(text) > 20):
                b.kind = "code"
                continue
            if is_caption(text):
                b.kind = "caption"
                continue
            larger = b.size >= self.heading_ratio * body
            bold_short = (
                b.bold >= 0.9
                and b.size >= 0.95 * body
                and words <= 15
                and not text.rstrip().endswith((".", ":", ";", ","))
            )
            if (larger and len(b.lines) <= 3 and len(text) <= 200) or (
                bold_short and len(b.lines) <= 2
            ):
                b.kind = "heading"
                headings.append(b)
                continue
            marker = split_list_marker(text)
            if marker is not None:
                b.kind = "list_item"
                b.attrs["ordered"] = marker[1]
                if marker[1]:
                    b.attrs["marker"] = marker[0]
                b.lines = [marker[2]]  # the joined text without its marker
                list_x[(b.page_no, b.column)].append(b.regions[0][1][0])
                continue
            if b.size <= 0.85 * body and y0 >= 0.7 * page_h and _FOOTNOTE_MARK.match(text):
                b.kind = "footnote"
        # List depth: rank of the item's indentation among the items of its column.
        for b in blocks:
            if b.kind == "list_item":
                levels = _cluster(list_x[(b.page_no, b.column)], 3.0)
                b.depth = max(0, sum(1 for x in levels if x < b.regions[0][1][0] - 3.0))
        self._levels(headings, body)

    def _levels(self, headings: List[LayoutBlock], body: float) -> None:
        if not headings:
            return
        # Style key, after docling's heading_hierarchy: size clusters (largest
        # first, a new cluster below a 5% drop), then weight.
        sizes = sorted({round(h.size, 1) for h in headings}, reverse=True)
        clusters: Dict[float, int] = {}
        rank = 0
        for i, size in enumerate(sizes):
            if i and sizes[i - 1] - size > 0.05 * sizes[i - 1]:
                rank += 1
            clusters[size] = rank
        keys = sorted({(clusters[round(h.size, 1)], -int(h.bold >= 0.5)) for h in headings})
        style_level = {k: i + 1 for i, k in enumerate(keys)}
        first = headings[0]
        top = max(h.size for h in headings)
        title_candidate = (
            first.page_no == min(h.page_no for h in headings)
            and first.size == top
            and first.size >= 1.3 * body
            and sum(1 for h in headings if abs(h.size - top) < 0.05 * top) == 1
            and not _HEADING_NUMBER.match(first.text)
        )
        for h in headings:
            numbered = _HEADING_NUMBER.match(h.text)
            if numbered:
                h.level = numbered.group(1).count(".") + 1
            else:
                h.level = style_level[(clusters[round(h.size, 1)], -int(h.bold >= 0.5))]
        if title_candidate:
            first.kind = "title"
            first.level = None
            # Levels below the title start at 1.
            rest = [h for h in headings if h is not first and not _HEADING_NUMBER.match(h.text)]
            if rest:
                shift = min(h.level for h in rest) - 1
                for h in rest:
                    h.level = max(1, h.level - shift)

    def _merge(self, blocks: List[LayoutBlock]) -> List[LayoutBlock]:
        """Join a paragraph to its continuation in the next column or on the next page.

        docling's predict_merges: the next paragraph in reading order (past
        furniture, tables, figures, captions and footnotes) is on a later page
        or strictly to the right, the first ends open (lower case, comma,
        hyphen) and the second starts with a letter. docling also lets two
        blocks side by side on one row merge (author blocks of a title page);
        here the first must end its column and the second must open one: no
        block of about their width below the first or above the second.
        """
        skip = {"page_header", "page_footer", "table", "figure", "caption", "footnote"}
        flow = [b for b in blocks if b.kind not in skip]
        by_page: Dict[int, List[BBox]] = defaultdict(list)
        for b in flow:
            for page, box in b.regions:
                by_page[page].append(box)

        def alone(page: int, box: BBox, side: str) -> bool:
            width = box[2] - box[0]
            for other in by_page[page]:
                if other is box or _overlap(other[0], other[2], box[0], box[2]) <= 0:
                    continue
                if other[2] - other[0] > 1.5 * width:
                    continue  # a block spanning columns (a title, an abstract) bounds a zone

                if side == "below" and other[1] >= box[3] - 1.0:
                    return False
                if side == "above" and other[3] <= box[1] + 1.0:
                    return False
            return True

        out: List[LayoutBlock] = []
        pending: Optional[LayoutBlock] = None  # a paragraph that may continue
        for b in blocks:
            if b.kind == "paragraph" and pending is not None:
                (p_page, p_box), (b_page, b_box) = pending.regions[-1], b.regions[0]
                if (
                    (b_page != p_page or p_box[2] < b_box[0])
                    and alone(p_page, p_box, "below")
                    and alone(b_page, b_box, "above")
                    and continues(pending.text, b.text)
                ):
                    pending.lines = [join_continued(pending.text, b.text)]
                    pending.regions.extend(b.regions)
                    continue
            out.append(b)
            if b.kind == "paragraph":
                pending = b
            elif b.kind not in skip:
                pending = None
        return out
