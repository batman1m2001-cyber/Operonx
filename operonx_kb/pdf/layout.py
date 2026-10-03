"""Layout analysis for PDF pages: words in, labelled blocks in reading order out.

:class:`LayoutModel` is the seam docling's layout model sits behind (the
``layout`` extra, phase K1b). :class:`HeuristicLayout` is the default, with no
ML. Its stages follow docling's StandardPdfPipeline (page parse → layout →
table structure → page assemble → reading order), with rules instead of models:

1. **Lines and segments.** Words sharing a baseline band form a line; a line is
   cut into segments where the gap between words exceeds ``segment_gap`` em —
   column gutters and table cells live in those gaps.
2. **Furniture.** A segment in the top or bottom margin band is a page header or
   footer when its digit-masked text repeats on another page, or when it is a
   page number ("3", "Page 3 of 9", "- 3 -", "Trang 3"). Docling relies on its
   layout model's labels; repetition is the rule-based equivalent.
3. **Tables.** Ruling lines that connect into a grid (≥ 2 horizontal and ≥ 2
   vertical) are a table; words go to the grid cell holding their centre. Without
   rules, ≥ 3 consecutive lines whose segments line up in the same ≥ 3 columns
   (or 2 columns of short cells) are a table.
4. **Columns and reading order.** A gutter is an x-range that few segments cross
   with text mass on both sides. Segments crossing a gutter span the page and cut
   it into zones; inside a zone the columns are read left to right, each top to
   bottom. This is the column-major order docling's rule-based reading order
   (``reading_order_rb.py``) arrives at through its up/down graph.
5. **Blocks.** Consecutive segments of one column join a block unless the
   vertical gap exceeds ``paragraph_gap`` em, the font size or weight changes, a
   list marker starts the line, or the line is indented (a first-line indent).
6. **Labels.** Monospace → code; "Figure 2:" → caption; a list marker → list
   item (depth from indentation); larger or bold short blocks → headings, level
   from numbering ("2.1" → 2) else from docling's style key (size cluster,
   weight); the largest heading at the top of page 1 → title; small text low on
   the page starting with a mark → footnote.
7. **Merges.** A paragraph that ends mid-sentence and continues in the next
   column or page is joined to its continuation (docling's predict_merges).
"""

from __future__ import annotations

import re
import statistics
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx_kb.model.ids import fingerprint
from operonx_kb.pdf.assemble import continues, is_caption, join_continued, join_lines, split_list_marker
from operonx_kb.pdf.backend import BBox, PdfPage, Rule, Word

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
    """Labels a document's pages and orders the blocks."""

    name: str = ""
    version: str = "1"

    def config(self) -> Dict[str, Any]:
        return {}

    def fingerprint(self) -> str:
        cls = type(self)
        return fingerprint(f"{cls.__module__}.{cls.__qualname__}", self.version, self.config())

    @abstractmethod
    def layout(self, pages: Sequence[PdfPage]) -> List[LayoutBlock]:
        """Blocks of every page, in reading order."""


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


def _crosses(x0: float, x1: float, gutters: List[Tuple[float, float]]) -> bool:
    """Whether ``[x0, x1]`` reaches across a gutter (a ragged line ending inside one does not)."""
    return any(x0 < g0 + 1.0 and x1 > g1 - 1.0 for g0, g1 in gutters)


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
        paragraph_gap: Vertical gap, in em, that starts a new block.
        heading_ratio: Font-size ratio to the body size that makes a heading.
        gutter_coverage: A gutter is crossed by at most this share of the
            densest x-position's segments.
    """

    name = "heuristic"
    version = "1"

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
        out: List[Segment] = []
        for index, line in enumerate(lines):
            line.sort(key=lambda w: w.x0)
            current = [line[0]]
            for prev, word in zip(line, line[1:]):
                em = max(min(prev.size, word.size), 1.0)
                if word.x0 - prev.x1 > self.segment_gap * em:
                    out.append(Segment(current, page.page_no, index))
                    current = []
                current.append(word)
            out.append(Segment(current, page.page_no, index))
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
                if a.x0 - tol <= b.x1 and b.x0 - tol <= a.x1 and a.y0 - tol <= b.y1 and b.y0 - tol <= a.y1:
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
            bbox = (min(r.x0 for r in group), min(r.y0 for r in group), max(r.x1 for r in group), max(r.y1 for r in group))
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
                matched = [next((k for k, (c0, c1) in enumerate(cols) if _overlap(s.x0, s.x1, c0, c1) > 0), None) for s in line]
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
                        k = next(k for k, (c0, c1) in enumerate(cols) if _overlap(s.x0, s.x1, c0, c1) > 0)
                        row[k] = (row[k] + " " + s.text).strip()
                    rows.append(row)
                tables.append((members, rows))
                i = j
            else:
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

    @staticmethod
    def order(items: List[Tuple[BBox, Any]], gutters: List[Tuple[float, float]]) -> List[Tuple[Any, Tuple[int, int]]]:
        """Column-major order; each item gets its (zone, column)."""
        def crosses(b: BBox) -> bool:
            return _crosses(b[0], b[2], gutters)

        def column(b: BBox) -> int:
            cx = (b[0] + b[2]) / 2
            return sum(1 for g0, g1 in gutters if cx >= g1 - 1)

        spanning = sorted((i for i in items if crosses(i[0])), key=lambda i: (i[0][1], i[0][0]))
        flowing = [i for i in items if not crosses(i[0])]
        out: List[Tuple[Any, Tuple[int, int]]] = []
        bounds = [s[0][1] for s in spanning] + [float("inf")]
        prev = float("-inf")
        for zone, bound in enumerate(bounds):
            inside = [i for i in flowing if prev <= (i[0][1] + i[0][3]) / 2 < bound]
            for col in range(len(gutters) + 1):
                members = sorted((i for i in inside if column(i[0]) == col), key=lambda i: (i[0][1], i[0][0]))
                out.extend((i[1], (zone, col)) for i in members)
            if zone < len(spanning):
                out.append((spanning[zone][1], (zone, -1)))
                prev = bound
        return out

    # -- 5-7. blocks, labels, merges -------------------------------------------

    def layout(self, pages: Sequence[PdfPage]) -> List[LayoutBlock]:
        segs = {}
        for page in pages:
            big = [b for b in page.images if (b[2] - b[0]) * (b[3] - b[1]) > 0.5 * page.width * page.height]
            words = [w for w in page.words if not any(b[0] <= (w.x0 + w.x1) / 2 <= b[2] and b[1] <= (w.y0 + w.y1) / 2 <= b[3] for b in page.images if b not in big)]
            segs[page.page_no] = self.segments(page, words)
        self.mark_furniture(pages, segs)
        sizes: Counter = Counter()
        for page_segs in segs.values():
            for s in page_segs:
                for w in s.words:
                    sizes[round(w.size * 2) / 2] += len(w.text)
        body = sizes.most_common(1)[0][0] if sizes else 10.0

        blocks: List[LayoutBlock] = []
        for page in pages:
            blocks.extend(self._page_blocks(page, segs[page.page_no], body))
        self._label(blocks, pages, body)
        return self._merge(blocks)

    def _page_blocks(self, page: PdfPage, segs: List[Segment], body: float) -> List[LayoutBlock]:
        furniture = [s for s in segs if s.furniture]
        flow = [s for s in segs if not s.furniture]
        out: List[LayoutBlock] = []
        for s in furniture:
            out.append(LayoutBlock(kind=s.furniture, page_no=page.page_no, lines=[s.text], regions=[(page.page_no, s.bbox)], size=s.size))

        items: List[Tuple[BBox, Any]] = []
        for bbox, xs, ys in self.ruled_tables(page):
            inside = [s for s in flow if bbox[0] - 1 <= (s.x0 + s.x1) / 2 <= bbox[2] + 1 and bbox[1] - 1 <= (s.y0 + s.y1) / 2 <= bbox[3] + 1]
            if not inside:
                continue
            flow = [s for s in flow if s not in inside]
            rows = self.fill_grid([w for s in inside for w in s.words], xs, ys)
            items.append((bbox, LayoutBlock(kind="table", page_no=page.page_no, rows=rows, regions=[(page.page_no, bbox)])))
        for b in page.images:
            if (b[2] - b[0]) * (b[3] - b[1]) <= 0.5 * page.width * page.height:
                items.append((b, LayoutBlock(kind="figure", page_no=page.page_no, regions=[(page.page_no, b)])))

        gutters = self.gutters(flow, body)
        # Borderless tables, inside one column or across the page.
        for column_segs in self._by_column(flow, gutters):
            for members, rows in self.aligned_tables(column_segs):
                flow = [s for s in flow if s not in members]
                bbox = (min(s.x0 for s in members), min(s.y0 for s in members), max(s.x1 for s in members), max(s.y1 for s in members))
                items.append((bbox, LayoutBlock(kind="table", page_no=page.page_no, rows=rows, regions=[(page.page_no, bbox)], attrs={"ruled": False})))
        items.extend((s.bbox, s) for s in flow)

        current: Optional[LayoutBlock] = None
        last: Optional[Segment] = None
        members: Dict[int, List[Segment]] = {}
        for item, column in self.order(items, gutters):
            if isinstance(item, LayoutBlock):
                item.column = column
                out.append(item)
                current, last = None, None
                continue
            s = item
            if current is not None and last is not None and current.column == column and self._continues_block(last, s, current):
                if s.line == last.line:
                    current.lines[-1] += " " + s.text
                else:
                    current.lines.append(s.text)
                self._grow(current, s)
                members[id(current)].append(s)
            else:
                current = LayoutBlock(kind="paragraph", page_no=page.page_no, lines=[s.text], regions=[(page.page_no, s.bbox)], x0=s.x0, column=column)
                members[id(current)] = [s]
                out.append(current)
            last = s
        for block in out:
            words = [w for seg in members.get(id(block), []) for w in seg.words]
            if words:
                chars = sum(len(w.text) for w in words) or 1
                block.size = sum(w.size * len(w.text) for w in words) / chars
                block.bold = sum(len(w.text) for w in words if w.bold) / chars
                block.mono = sum(len(w.text) for w in words if w.mono) / chars
        return out

    def _by_column(self, flow: List[Segment], gutters: List[Tuple[float, float]]) -> List[List[Segment]]:
        groups: Dict[int, List[Segment]] = defaultdict(list)
        for s in flow:
            if _crosses(s.x0, s.x1, gutters):
                groups[-1].append(s)
            else:
                cx = (s.x0 + s.x1) / 2
                groups[sum(1 for g0, g1 in gutters if cx >= g1 - 1)].append(s)
        return list(groups.values())

    def _continues_block(self, last: Segment, s: Segment, block: LayoutBlock) -> bool:
        if s.line == last.line:
            return True
        size = max(last.size, s.size, 1.0)
        gap = s.y0 - last.y1
        if gap < -0.5 * size or gap > self.paragraph_gap * size:
            return False
        if abs(s.size - last.size) > 0.1 * size:
            return False
        if (s.share("bold") >= 0.5) != (last.share("bold") >= 0.5):
            return False
        if split_list_marker(s.text) is not None:
            return False
        if split_list_marker(block.lines[0]) is not None:
            return s.x0 > block.x0 + 0.3 * size  # a list item's wrapped lines hang under its text
        if s.x0 > block.x0 + 0.8 * size and s.x0 - last.x0 > 0.8 * size:
            return False  # first-line indent of a new paragraph
        return True

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
            bold_short = b.bold >= 0.9 and b.size >= 0.95 * body and words <= 15 and not text.rstrip().endswith((".", ":", ";", ","))
            if (larger and len(b.lines) <= 3 and len(text) <= 200) or (bold_short and len(b.lines) <= 2):
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
        out: List[LayoutBlock] = []
        skip = {"page_header", "page_footer", "table", "figure", "caption", "footnote"}
        pending: Optional[LayoutBlock] = None  # a paragraph that may continue
        for b in blocks:
            if b.kind == "paragraph" and pending is not None and (b.page_no != pending.page_no or b.column != pending.column):
                if continues(pending.text, b.text):
                    pending.lines = [join_continued(pending.text, b.text)]
                    pending.regions.extend(b.regions)
                    continue
            out.append(b)
            if b.kind == "paragraph":
                pending = b
            elif b.kind not in skip:
                pending = None
        return out
