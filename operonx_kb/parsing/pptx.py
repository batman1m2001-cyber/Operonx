"""PPTX with the stdlib (``zipfile`` + ``defusedxml``).

Each slide is a page; shape geometry gives regions. Rules ported from docling's
``mspowerpoint_backend.py``:

- **Shape order**: by top, then left within a row, where shapes whose tops lie
  within 0.05 in (45 720 EMU) of the row's first shape share the row. Group
  shapes are flattened through their child coordinate space. A placeholder
  without its own position takes it from the slide layout, then the master.
- **Titles**: ``title``/``ctrTitle`` placeholders are level-1 headings (one per
  slide; the deck's title is the first). Date, footer and slide-number
  placeholders are furniture.
- **Lists**: ``a:buNone`` is not a list; ``a:buChar``/``a:buAutoNum`` are; with
  neither, body and object placeholders are bulleted (the master's
  ``bodyStyle`` default) and other shapes are lists only at ``lvl`` > 0. The
  depth is ``lvl``; ``buAutoNum`` counters honour ``startAt``.
- **Tables**: ``gridSpan``/``hMerge``/``vMerge`` continuations are empty cells;
  row 0 is the header. **Pictures** are figures with their description.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from xml.etree.ElementTree import Element

from operonx_kb.errors import DocumentParseError
from operonx_kb.model.document import Region
from operonx_kb.parsing._ooxml import NS, Package, format_counter, q
from operonx_kb.parsing.base import PageInfo, ParsedDoc, Parser, RawBlock

__all__ = ["PptxParser"]

_R = NS["r"]
_ROW_TOLERANCE = 45720  # EMU, 0.05 inch
_EMU_PER_PT = 12700
_FURNITURE_PH = frozenset({"dt", "ftr", "sldNum", "hdr"})
_TITLE_PH = frozenset({"title", "ctrTitle"})
_BODY_PH = frozenset({"body", "obj", None})

# A shape's (x, y, w, h) in EMU.
Box = Tuple[float, float, float, float]


def _xfrm(el: Element) -> Optional[Box]:
    xfrm = el.find(f"{q('p:spPr')}/{q('a:xfrm')}")
    if xfrm is None:
        xfrm = el.find(q("p:xfrm"))  # graphicFrame
    if xfrm is None:
        xfrm = el.find(f"{q('p:grpSpPr')}/{q('a:xfrm')}")
    if xfrm is None:
        return None
    off, ext = xfrm.find(q("a:off")), xfrm.find(q("a:ext"))
    if off is None or ext is None:
        return None
    return (float(off.get("x", 0)), float(off.get("y", 0)), float(ext.get("cx", 0)), float(ext.get("cy", 0)))


def _placeholder(el: Element) -> Tuple[bool, Optional[str], Optional[str]]:
    """(is placeholder, type, idx)."""
    for nv in (q("p:nvSpPr"), q("p:nvGraphicFramePr"), q("p:nvPicPr")):
        node = el.find(f"{nv}/{q('p:nvPr')}/{q('p:ph')}")
        if node is not None:
            return True, node.get("type"), node.get("idx")
    return False, None, None


class _Layouts:
    """Placeholder positions from a slide's layout and master."""

    def __init__(self, pkg: Package):
        self.pkg = pkg
        self.cache: Dict[str, Dict[Tuple[Optional[str], Optional[str]], Box]] = {}

    def _positions(self, part: str) -> Dict[Tuple[Optional[str], Optional[str]], Box]:
        if part not in self.cache:
            out: Dict[Tuple[Optional[str], Optional[str]], Box] = {}
            root = self.pkg.xml(part)
            if root is not None:
                for sp in root.iter(q("p:sp")):
                    is_ph, typ, idx = _placeholder(sp)
                    box = _xfrm(sp)
                    if is_ph and box is not None:
                        out[(typ, idx)] = box
                        out.setdefault((typ, None), box)
            self.cache[part] = out
        return self.cache[part]

    def lookup(self, slide: str, typ: Optional[str], idx: Optional[str]) -> Optional[Box]:
        layout = next((t for t in self.pkg.rels(slide).values() if "slideLayout" in t), None)
        parts = [layout] if layout else []
        if layout:
            master = next((t for t in self.pkg.rels(layout).values() if "slideMaster" in t), None)
            if master:
                parts.append(master)
        for part in parts:
            pos = self._positions(part)
            for key in ((typ, idx), (typ, None), ("body" if typ is None else typ, None)):
                if key in pos:
                    return pos[key]
        return None


def _paragraph_text(p: Element) -> str:
    out: List[str] = []
    for child in p:
        if child.tag in (q("a:r"), q("a:fld")):
            t = child.find(q("a:t"))
            out.append((t.text or "") if t is not None else "")
        elif child.tag == q("a:br"):
            out.append("\n")
    return "".join(out)


def _auto_num(kind: str, n: int) -> str:
    if kind.startswith("alphaLc"):
        body = chr(ord("a") + (n - 1) % 26)
    elif kind.startswith("alphaUc"):
        body = chr(ord("A") + (n - 1) % 26)
    elif kind.startswith("romanLc") or kind.startswith("romanUc"):
        body = format_counter(n, "lowerRoman" if kind.startswith("romanLc") else "upperRoman")
    else:
        body = str(n)
    if kind.endswith("ParenBoth"):
        return f"({body})"
    if kind.endswith("ParenR"):
        return f"{body})"
    return f"{body}."


class _Slide:
    def __init__(self, pkg: Package, layouts: _Layouts, part: str, page_no: int, size: Tuple[float, float]):
        self.pkg = pkg
        self.layouts = layouts
        self.part = part
        self.page_no = page_no
        self.size = size
        self.blocks: List[RawBlock] = []

    def region(self, box: Optional[Box]) -> List[Region]:
        if box is None:
            return []
        w, h = self.size
        x, y, cx, cy = box
        clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
        return [Region(page_no=self.page_no, bbox=(clamp(x / w), clamp(y / h), clamp((x + cx) / w), clamp((y + cy) / h)))]

    def shapes(self, tree: Element, transform=None) -> List[Tuple[Optional[Box], int, Element]]:
        """Leaf shapes with absolute boxes, in document order."""
        out: List[Tuple[Optional[Box], int, Element]] = []
        for child in tree:
            if child.tag == q("p:grpSp"):
                gx = child.find(f"{q('p:grpSpPr')}/{q('a:xfrm')}")
                inner = transform
                if gx is not None:
                    off, ext = gx.find(q("a:off")), gx.find(q("a:ext"))
                    choff, chext = gx.find(q("a:chOff")), gx.find(q("a:chExt"))
                    if None not in (off, ext, choff, chext):
                        inner = (
                            float(off.get("x", 0)), float(off.get("y", 0)),
                            float(ext.get("cx", 1)) / max(float(chext.get("cx", 1)), 1.0),
                            float(ext.get("cy", 1)) / max(float(chext.get("cy", 1)), 1.0),
                            float(choff.get("x", 0)), float(choff.get("y", 0)),
                        )  # fmt: skip
                        if transform is not None:
                            inner = (*self._apply(transform, (inner[0], inner[1], 0, 0))[:2], *inner[2:])
                out.extend(self.shapes(child, inner))
            elif child.tag in (q("p:sp"), q("p:graphicFrame"), q("p:pic")):
                box = _xfrm(child)
                if box is None:
                    is_ph, typ, idx = _placeholder(child)
                    if is_ph:
                        box = self.layouts.lookup(self.part, typ, idx)
                elif transform is not None:
                    box = self._apply(transform, box)
                out.append((box, len(out), child))
        return out

    @staticmethod
    def _apply(t, box: Box) -> Box:
        ox, oy, sx, sy, cx0, cy0 = t
        x, y, w, h = box
        return (ox + (x - cx0) * sx, oy + (y - cy0) * sy, w * sx, h * sy)

    def ordered(self, shapes):
        placed = sorted((s for s in shapes if s[0] is not None), key=lambda s: (s[0][1], s[1]))
        rows: List[List] = []
        for s in placed:
            if rows and abs(s[0][1] - rows[-1][0][0][1]) <= _ROW_TOLERANCE:
                rows[-1].append(s)
            else:
                rows.append([s])
        out = [s for row in rows for s in sorted(row, key=lambda s: (s[0][0], s[1]))]
        return out + [s for s in shapes if s[0] is None]

    def emit(self) -> None:
        root = self.pkg.xml(self.part)
        tree = root.find(f"{q('p:cSld')}/{q('p:spTree')}") if root is not None else None
        if tree is None:
            return
        for box, _, el in self.ordered(self.shapes(tree)):
            regions = self.region(box)
            if el.tag == q("p:pic"):
                pr = el.find(f"{q('p:nvPicPr')}/{q('p:cNvPr')}")
                alt = (pr.get("descr") or "") if pr is not None else ""
                self.blocks.append(RawBlock(kind="figure", text=alt, regions=regions, attrs={"slide": self.page_no}))
            elif el.tag == q("p:graphicFrame"):
                tbl = el.find(f".//{q('a:tbl')}")
                if tbl is not None:
                    self.table(tbl, regions)
            else:
                self.text_shape(el, regions)

    def text_shape(self, sp: Element, regions: List[Region]) -> None:
        body = sp.find(q("p:txBody"))
        if body is None:
            return
        is_ph, typ, _ = _placeholder(sp)
        if is_ph and typ in _FURNITURE_PH:
            text = " ".join(_paragraph_text(p) for p in body.findall(q("a:p")))
            if text.strip():
                self.blocks.append(RawBlock(kind="page_footer", text=text, regions=regions))
            return
        if is_ph and typ in _TITLE_PH:
            text = " ".join(_paragraph_text(p) for p in body.findall(q("a:p")))
            if text.strip():
                self.blocks.append(RawBlock(kind="heading", level=1, text=text, regions=regions, attrs={"slide": self.page_no}))
            return
        bulleted_default = is_ph and typ in _BODY_PH
        counters: Dict[int, int] = {}
        for p in body.findall(q("a:p")):
            text = _paragraph_text(p)
            if not text.strip():
                continue
            ppr = p.find(q("a:pPr"))
            lvl = int(ppr.get("lvl", 0)) if ppr is not None else 0
            bullet = None
            if ppr is not None:
                if ppr.find(q("a:buNone")) is not None:
                    bullet = "none"
                elif ppr.find(q("a:buAutoNum")) is not None:
                    bullet = "num"
                elif ppr.find(q("a:buChar")) is not None or ppr.find(q("a:buBlip")) is not None:
                    bullet = "char"
            is_list = bullet in ("num", "char") or (bullet is None and (bulleted_default or lvl > 0))
            if not is_list:
                counters.clear()
                self.blocks.append(RawBlock(kind="paragraph", text=text, regions=regions, attrs={"slide": self.page_no}))
                continue
            for deeper in [k for k in counters if k > lvl]:
                del counters[deeper]
            attrs = {"ordered": bullet == "num", "slide": self.page_no}
            if bullet == "num":
                auto = ppr.find(q("a:buAutoNum"))
                start = int(auto.get("startAt", 1))
                counters[lvl] = counters.get(lvl, start - 1) + 1
                attrs["marker"] = _auto_num(auto.get("type", "arabicPeriod"), counters[lvl])
            self.blocks.append(RawBlock(kind="list_item", text=text, depth=lvl, regions=regions, attrs=attrs))

    def table(self, tbl: Element, regions: List[Region]) -> None:
        rows: List[List[str]] = []
        for tr in tbl.findall(q("a:tr")):
            row: List[str] = []
            for tc in tr.findall(q("a:tc")):
                merged = tc.get("hMerge") == "1" or tc.get("vMerge") == "1"
                body = tc.find(q("a:txBody"))
                text = ""
                if body is not None and not merged:
                    text = " ".join(t for t in (_paragraph_text(p) for p in body.findall(q("a:p"))) if t.strip())
                row.append(text)
            rows.append(row)
        if rows:
            self.blocks.append(
                RawBlock(kind="table", regions=regions, attrs={"rows": rows, "header_rows": 1, "slide": self.page_no})
            )


class PptxParser(Parser):
    """PowerPoint decks (``.pptx``); one page per slide."""

    name = "pptx"
    version = "1"
    mimes = frozenset({"application/vnd.openxmlformats-officedocument.presentationml.presentation"})
    extensions = frozenset({".pptx"})

    def parse(self, data: bytes, *, name: Optional[str] = None) -> ParsedDoc:
        pkg = Package(data, "PPTX")
        pres = pkg.xml("ppt/presentation.xml")
        if pres is None:
            raise DocumentParseError("PPTX has no ppt/presentation.xml; is it a PowerPoint deck?")
        size_el = pres.find(q("p:sldSz"))
        size = (
            (float(size_el.get("cx", 9144000)), float(size_el.get("cy", 6858000)))
            if size_el is not None
            else (9144000.0, 6858000.0)
        )
        rels = pkg.rels("ppt/presentation.xml")
        layouts = _Layouts(pkg)
        blocks: List[RawBlock] = []
        pages: List[PageInfo] = []
        slide_ids = pres.find(q("p:sldIdLst"))
        for n, sld in enumerate(slide_ids if slide_ids is not None else [], start=1):
            part = rels.get(sld.get(f"{{{_R}}}id") or "")
            if not part or not pkg.has(part):
                continue
            slide = _Slide(pkg, layouts, part, n, size)
            slide.emit()
            blocks.extend(slide.blocks)
            pages.append(PageInfo(page_no=n, width=size[0] / _EMU_PER_PT, height=size[1] / _EMU_PER_PT))
        metadata = {}
        first_heading = next((b.text for b in blocks if b.kind == "heading"), None)
        if first_heading:
            metadata["title"] = first_heading
        return ParsedDoc(blocks=blocks, pages=pages, metadata=metadata, parser=self.name)
