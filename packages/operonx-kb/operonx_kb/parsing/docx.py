"""DOCX with the stdlib (``zipfile`` + ``defusedxml``).

The rules are ported from docling's ``msword_backend.py``:

- **Headings**: a paragraph whose style (or any style it is ``basedOn``) has
  "heading" in its id or name. The level is the style's ``outlineLvl`` + 1 when
  set, else the digits in the style name ("Heading 2" → 2). A paragraph with an
  ``outlineLvl`` but a localised style name is a heading too. Style id or name
  "Title" is the title; "Caption" is a caption.
- **Code**: a style in the code family (``Code``, ``HTML Preformatted``,
  ``Source Code``, ``Verbatim`` …) anywhere in the ``basedOn`` chain.
- **Lists**: ``w:numPr`` on the paragraph or inherited from its style;
  ``numId`` 0 means none; headings and code never become items. Ordered when
  the level's ``numFmt`` is a counting format; the marker is the level's
  ``lvlText`` with ``%N`` filled from per-list counters (deeper levels reset).
- **Tables**: ``gridSpan`` and ``gridBefore`` keep columns aligned (spanned slots
  are empty); a ``vMerge`` continuation is an empty cell; row 0 is the header;
  a 1×1 table is unwrapped into its paragraphs.
- Headers and footers are furniture; footnotes come last; ``docProps/core.xml``
  gives the title. Deleted text (``w:del``) and field instructions are skipped;
  text boxes become paragraphs after their anchor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from xml.etree.ElementTree import Element

from operonx_kb.errors import DocumentParseError
from operonx_kb.parsing._ooxml import NS, Package, format_counter, q
from operonx_kb.parsing.base import ParsedDoc, Parser, RawBlock

__all__ = ["DocxParser"]

_W = NS["w"]
_CODE_STYLES = frozenset(
    {"source code", "code", "code block", "code listing", "html preformatted",
     "preformatted text", "preformatted", "verbatim"}
)  # fmt: skip
_ORDERED_FORMATS = frozenset(
    {"decimal", "decimalZero", "lowerRoman", "upperRoman", "lowerLetter", "upperLetter",
     "ordinal", "cardinalText", "ordinalText", "decimalEnclosedParen", "decimalEnclosedCircle",
     "chineseCounting", "ideographDigital", "japaneseCounting", "koreanCounting"}
)  # fmt: skip
_HEADING_NUM = re.compile(r"(\d+)\s*$|^(\d+)")


def _wattr(el: Element, name: str) -> Optional[str]:
    return el.get(f"{{{_W}}}{name}")


@dataclass
class _Style:
    name: str = ""
    based_on: Optional[str] = None
    outline: Optional[int] = None
    num: Optional[Tuple[str, int]] = None


@dataclass
class _Level:
    fmt: str = "decimal"
    text: str = ""
    start: int = 1


@dataclass
class _Numbering:
    levels: Dict[str, Dict[int, _Level]] = field(default_factory=dict)  # numId -> ilvl -> level
    counters: Dict[str, Dict[int, int]] = field(default_factory=dict)

    def level(self, num_id: str, ilvl: int) -> _Level:
        return self.levels.get(num_id, {}).get(ilvl, _Level(text=f"%{ilvl + 1}."))

    def next_marker(self, num_id: str, ilvl: int) -> Tuple[bool, str]:
        lvl = self.level(num_id, ilvl)
        counters = self.counters.setdefault(num_id, {})
        counters[ilvl] = counters.get(ilvl, lvl.start - 1) + 1
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]
        ordered = lvl.fmt in _ORDERED_FORMATS
        if not ordered:
            return False, ""

        def fill(match: re.Match) -> str:
            k = int(match.group(1)) - 1
            other = self.level(num_id, k)
            return format_counter(counters.get(k, other.start), other.fmt)

        marker = re.sub(r"%(\d)", fill, lvl.text or f"%{ilvl + 1}.").strip()
        return True, marker or f"{counters[ilvl]}."


def _styles(pkg: Package) -> Dict[str, _Style]:
    root = pkg.xml("word/styles.xml")
    out: Dict[str, _Style] = {}
    if root is None:
        return out
    for st in root.findall(q("w:style")):
        sid = _wattr(st, "styleId") or ""
        style = _Style()
        name = st.find(q("w:name"))
        style.name = (_wattr(name, "val") or "") if name is not None else ""
        based = st.find(q("w:basedOn"))
        style.based_on = _wattr(based, "val") if based is not None else None
        ppr = st.find(q("w:pPr"))
        if ppr is not None:
            outline = ppr.find(q("w:outlineLvl"))
            if outline is not None:
                style.outline = int(_wattr(outline, "val") or 9)
            style.num = _num_pr(ppr)
        out[sid] = style
    return out


def _num_pr(ppr: Element) -> Optional[Tuple[str, int]]:
    num = ppr.find(q("w:numPr"))
    if num is None:
        return None
    nid = num.find(q("w:numId"))
    lvl = num.find(q("w:ilvl"))
    if nid is None:
        return None
    return (_wattr(nid, "val") or "0", int(_wattr(lvl, "val") or 0) if lvl is not None else 0)


def _numbering(pkg: Package) -> _Numbering:
    root = pkg.xml("word/numbering.xml")
    numbering = _Numbering()
    if root is None:
        return numbering
    abstract: Dict[str, Dict[int, _Level]] = {}
    for an in root.findall(q("w:abstractNum")):
        levels: Dict[int, _Level] = {}
        for lvl in an.findall(q("w:lvl")):
            ilvl = int(_wattr(lvl, "ilvl") or 0)
            fmt = lvl.find(q("w:numFmt"))
            text = lvl.find(q("w:lvlText"))
            start = lvl.find(q("w:start"))
            levels[ilvl] = _Level(
                fmt=(_wattr(fmt, "val") or "decimal") if fmt is not None else "decimal",
                text=(_wattr(text, "val") or "") if text is not None else "",
                start=int(_wattr(start, "val") or 1) if start is not None else 1,
            )
        abstract[_wattr(an, "abstractNumId") or ""] = levels
    for num in root.findall(q("w:num")):
        ref = num.find(q("w:abstractNumId"))
        levels = dict(abstract.get(_wattr(ref, "val") or "", {})) if ref is not None else {}
        for override in num.findall(q("w:lvlOverride")):
            ilvl = int(_wattr(override, "ilvl") or 0)
            start = override.find(q("w:startOverride"))
            if start is not None and ilvl in levels:
                base = levels[ilvl]
                levels[ilvl] = _Level(base.fmt, base.text, int(_wattr(start, "val") or 1))
        numbering.levels[_wattr(num, "numId") or ""] = levels
    return numbering


_SKIP_TEXT = frozenset({q("w:del"), q("w:instrText"), q("w:drawing"), q("w:pict"), q("mc:AlternateContent"),
                        q("w:delText"), q("w:footnoteReference"), q("w:endnoteReference")})  # fmt: skip


def _run_text(el: Element, out: List[str]) -> None:
    for child in el:
        tag = child.tag
        if tag in _SKIP_TEXT:
            continue
        if tag == q("w:t"):
            out.append(child.text or "")
        elif tag in (q("w:tab"), q("w:ptab")):
            out.append(" ")
        elif tag in (q("w:br"), q("w:cr")):
            out.append("\n")
        elif tag == q("w:noBreakHyphen"):
            out.append("-")
        elif tag == q("w:pPr") or tag == q("w:rPr"):
            continue
        else:
            _run_text(child, out)


def _paragraph_text(p: Element) -> str:
    out: List[str] = []
    _run_text(p, out)
    return "".join(out)


class _Walker:
    def __init__(self, pkg: Package):
        self.styles = _styles(pkg)
        self.numbering = _numbering(pkg)
        self.blocks: List[RawBlock] = []

    def chain(self, style_id: Optional[str]) -> List[Tuple[str, _Style]]:
        out: List[Tuple[str, _Style]] = []
        seen = set()
        while style_id and style_id not in seen and style_id in self.styles:
            seen.add(style_id)
            style = self.styles[style_id]
            out.append((style_id, style))
            style_id = style.based_on
        return out

    def classify(self, p: Element) -> Tuple[str, Optional[int], Optional[Tuple[str, int]]]:
        ppr = p.find(q("w:pPr"))
        style_id = None
        outline = None
        num = None
        if ppr is not None:
            ps = ppr.find(q("w:pStyle"))
            style_id = _wattr(ps, "val") if ps is not None else None
            ol = ppr.find(q("w:outlineLvl"))
            outline = int(_wattr(ol, "val") or 9) if ol is not None else None
            num = _num_pr(ppr)
        chain = self.chain(style_id)
        names = [(sid.lower(), st.name.lower()) for sid, st in chain] or (
            [(style_id.lower(), "")] if style_id else []
        )
        if any(sid == "title" or name == "title" for sid, name in names):
            return "title", None, None
        if any(name == "caption" or sid == "caption" for sid, name in names):
            return "caption", None, None
        if any(name in _CODE_STYLES or sid in _CODE_STYLES for sid, name in names):
            return "code", None, None
        if any("heading" in sid or "heading" in name for sid, name in names):
            level = next(
                (
                    st.outline + 1
                    for _, st in chain
                    if st.outline is not None and 0 <= st.outline < 9
                ),
                None,
            )
            if level is None:
                for sid, name in names:
                    m = _HEADING_NUM.search(name) or _HEADING_NUM.search(sid)
                    if m:
                        level = int(m.group(1) or m.group(2))
                        break
            return "heading", max(1, min(level or 1, 9)), None
        if outline is None:
            outline = next((st.outline for _, st in chain if st.outline is not None), None)
        if outline is not None and 0 <= outline < 9:
            return "heading", outline + 1, None
        if num is None:
            num = next((st.num for _, st in chain if st.num is not None), None)
        if num is not None and num[0] != "0":
            return "list_item", None, num
        return "paragraph", None, None

    def paragraph(self, p: Element) -> None:
        kind, level, num = self.classify(p)
        text = _paragraph_text(p)
        attrs = {}
        depth = 0
        if kind == "list_item" and num is not None:
            ordered, marker = self.numbering.next_marker(num[0], num[1])
            attrs = {"ordered": ordered}
            if ordered:
                attrs["marker"] = marker
            depth = num[1]
        if text.strip():
            self.blocks.append(
                RawBlock(kind=kind, text=text, level=level, depth=depth, attrs=attrs)
            )
        for drawing in p.iter(q("w:drawing")):
            if next(drawing.iter(q("pic:pic")), None) is None:
                continue  # a shape or text box, not a picture
            pr = next(drawing.iter(q("wp:docPr")), None)
            alt = (pr.get("descr") or pr.get("title") or "") if pr is not None else ""
            self.blocks.append(RawBlock(kind="figure", text=alt))
        # Text boxes: the DrawingML choice, or the VML fallback when there is no choice.
        boxes = [tb for choice in p.iter(q("mc:Choice")) for tb in choice.iter(q("w:txbxContent"))]
        if not boxes:
            boxes = [tb for pict in p.iter(q("w:pict")) for tb in pict.iter(q("w:txbxContent"))]
        for box in boxes:
            for inner in box.iter(q("w:p")):
                t = _paragraph_text(inner)
                if t.strip():
                    self.blocks.append(RawBlock(kind="paragraph", text=t))

    def table(self, tbl: Element) -> None:
        rows: List[List[str]] = []
        for tr in tbl.findall(q("w:tr")):
            row: List[str] = []
            trpr = tr.find(q("w:trPr"))
            if trpr is not None:
                before = trpr.find(q("w:gridBefore"))
                if before is not None:
                    row.extend([""] * int(_wattr(before, "val") or 0))
            for tc in tr.findall(q("w:tc")):
                tcpr = tc.find(q("w:tcPr"))
                span = 1
                continuation = False
                if tcpr is not None:
                    gs = tcpr.find(q("w:gridSpan"))
                    span = int(_wattr(gs, "val") or 1) if gs is not None else 1
                    vm = tcpr.find(q("w:vMerge"))
                    continuation = vm is not None and _wattr(vm, "val") != "restart"
                text = (
                    ""
                    if continuation
                    else " ".join(
                        t for t in (_paragraph_text(p) for p in tc.iter(q("w:p"))) if t.strip()
                    )
                )
                row.append(text)
                row.extend([""] * (span - 1))
            rows.append(row)
        if len(rows) == 1 and len(rows[0]) == 1:
            for tc in tbl.iter(q("w:tc")):
                for p in tc.findall(q("w:p")):
                    self.paragraph(p)
            return
        if rows:
            self.blocks.append(RawBlock(kind="table", attrs={"rows": rows, "header_rows": 1}))

    def body(self, el: Element) -> None:
        for child in el:
            if child.tag == q("w:p"):
                self.paragraph(child)
            elif child.tag == q("w:tbl"):
                self.table(child)
            elif child.tag == q("w:sdt"):
                content = child.find(q("w:sdtContent"))
                if content is not None:
                    self.body(content)
            elif child.tag in (q("w:customXml"), q("w:ins")):
                self.body(child)


def _part_text(root: Optional[Element]) -> str:
    if root is None:
        return ""
    return " ".join(t for t in (_paragraph_text(p) for p in root.iter(q("w:p"))) if t.strip())


class DocxParser(Parser):
    """Word documents (``.docx``)."""

    name = "docx"
    version = "1"
    mimes = frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
    extensions = frozenset({".docx"})

    def parse(self, data: bytes, *, name: Optional[str] = None) -> ParsedDoc:
        pkg = Package(data, "DOCX")
        document = pkg.xml("word/document.xml")
        if document is None:
            raise DocumentParseError("DOCX has no word/document.xml; is it a Word document?")
        walker = _Walker(pkg)
        body = document.find(q("w:body"))
        if body is not None:
            walker.body(body)
        rels = pkg.rels("word/document.xml")
        furniture: List[RawBlock] = []
        for rid, target in sorted(rels.items()):
            base = target.rsplit("/", 1)[-1]
            if base.startswith("header") or base.startswith("footer"):
                text = _part_text(pkg.xml(target))
                if text.strip():
                    kind = "page_header" if base.startswith("header") else "page_footer"
                    furniture.append(RawBlock(kind=kind, text=text))
        notes = pkg.xml("word/footnotes.xml")
        footnotes: List[RawBlock] = []
        if notes is not None:
            for fn in notes.findall(q("w:footnote")):
                if int(_wattr(fn, "id") or 0) <= 0:
                    continue  # separators
                text = _part_text(fn)
                if text.strip():
                    footnotes.append(RawBlock(kind="footnote", text=text))
        metadata = {}
        core = pkg.xml("docProps/core.xml")
        if core is not None:
            title = core.find("{http://purl.org/dc/elements/1.1/}title")
            if title is not None and (title.text or "").strip():
                metadata["title"] = title.text.strip()
        return ParsedDoc(
            blocks=furniture + walker.blocks + footnotes, metadata=metadata, parser=self.name
        )
