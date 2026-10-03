"""HTML with the stdlib ``html.parser``.

Rules ported from docling's ``html_backend.py``, adapted:

- ``script``, ``style``, ``noscript``, ``template``, ``svg`` and hidden elements
  (``hidden``, ``aria-hidden="true"``, inline ``display:none`` /
  ``visibility:hidden``) are dropped with their content.
- ``<title>`` is metadata. Site chrome — ``nav``, and ``header``/``footer`` that
  are not inside ``main``/``article`` — becomes furniture (page header/footer):
  kept in the tree, out of the canonical text. Docling keeps ``nav`` in the body;
  for retrieval it is noise on every page.
- ``h1``–``h6`` are headings of that level; a lone ``h1`` that opens the
  document is the title (the same rule as Markdown).
- ``ul``/``ol`` (``start``) nest into list items; ``dl`` gives ``dt`` items with
  their ``dd`` one level deeper.
- ``pre`` is code (language from ``class="language-x"``); ``table`` is a table
  laid out on an occupancy grid (``rowspan``/``colspan``; a spanned slot is
  empty), ``th`` rows at the top are header rows; ``caption`` and
  ``figcaption`` are captions; ``img`` is a figure with its ``alt``.
- Inline text is buffered across inline tags and flushed at block boundaries;
  ``br`` is a line break inside a block.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser as _StdHTMLParser
from typing import Any, Dict, List, Optional, Tuple

from operonx_kb.parsing._text import decode_text
from operonx_kb.parsing.base import ParsedDoc, Parser, RawBlock

__all__ = ["HtmlParser", "html_to_blocks", "promote_lone_h1"]

_SKIP = frozenset({"script", "style", "noscript", "template", "svg", "object", "iframe"})
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "dd", "details", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre", "section", "summary",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "caption",
    }
)  # fmt: skip
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*(hidden|collapse)", re.I)
_LANG = re.compile(r"(?:language|lang)-([\w+#-]+)")


def _hidden(attrs: Dict[str, Optional[str]]) -> bool:
    return (
        "hidden" in attrs
        or (attrs.get("aria-hidden") or "").lower() == "true"
        or bool(_HIDDEN_STYLE.search(attrs.get("style") or ""))
    )


def _int(value: Optional[str], default: int = 1) -> int:
    try:
        n = int(value or default)
    except ValueError:
        return default
    return max(1, min(n, 1000))


class _Table:
    def __init__(self) -> None:
        self.rows: List[List[Tuple[str, bool, int, int]]] = []  # (text, is_th, rowspan, colspan)
        self.cell: Optional[List[str]] = None
        self.cell_meta: Tuple[bool, int, int] = (False, 1, 1)
        self.caption: Optional[List[str]] = None

    def grid(self) -> Tuple[List[List[str]], int]:
        occupied: Dict[Tuple[int, int], str] = {}
        header_flags: List[bool] = []
        for r, row in enumerate(self.rows):
            c = 0
            all_th = bool(row)
            for text, is_th, rowspan, colspan in row:
                while (r, c) in occupied:
                    c += 1
                for dr in range(rowspan):
                    for dc in range(colspan):
                        occupied[(r + dr, c + dc)] = text if dr == 0 and dc == 0 else ""
                all_th = all_th and is_th
                c += colspan
            header_flags.append(all_th)
        if not occupied:
            return [], 0
        n_rows = max(r for r, _ in occupied) + 1
        n_cols = max(c for _, c in occupied) + 1
        grid = [[occupied.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
        header_rows = 0
        while header_rows < len(header_flags) and header_flags[header_rows]:
            header_rows += 1
        return grid, header_rows


class _Builder(_StdHTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[RawBlock] = []
        self.title_parts: List[str] = []
        self.stack: List[str] = []
        self.skip_depth = 0
        self.in_title = False
        self.buffer: List[str] = []
        self.lists: List[Dict[str, Any]] = []  # {"ordered", "counter"}
        self.pending_kind: List[Tuple[str, Dict[str, Any]]] = []  # block context for the buffer
        self.tables: List[_Table] = []
        self.furniture_depth = 0
        self.furniture_kind = "page_header"
        self.pre_depth = 0
        self.pre_lang: Optional[str] = None

    # -- helpers ---------------------------------------------------------

    def _in(self, *tags: str) -> bool:
        return any(t in self.stack for t in tags)

    def _context(self) -> Tuple[str, Dict[str, Any]]:
        return self.pending_kind[-1] if self.pending_kind else ("paragraph", {})

    def flush(self) -> None:
        text = "".join(self.buffer)
        self.buffer = []
        if not text.strip():
            return
        if self.furniture_depth:
            self.blocks.append(RawBlock(kind=self.furniture_kind, text=text))
            return
        kind, extra = self._context()
        attrs = dict(extra.get("attrs", {}))
        self.blocks.append(
            RawBlock(
                kind=kind,
                text=text,
                level=extra.get("level"),
                depth=extra.get("depth", 0),
                attrs=attrs,
            )
        )
        if kind == "list_item":
            # Text after a nested list inside the same <li> continues as a paragraph.
            self.pending_kind[-1] = ("paragraph", {})

    # -- events ----------------------------------------------------------

    def handle_starttag(self, tag: str, attr_list: List[Tuple[str, Optional[str]]]) -> None:
        attrs = dict(attr_list)
        if self.skip_depth:
            if tag not in _VOID:
                self.skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
            return
        if tag in _SKIP or _hidden(attrs):
            if tag not in _VOID:
                self.skip_depth = 1
            return
        if tag == "br":
            self.buffer.append("\n")
            return
        if self.tables and tag in ("tr", "td", "th", "caption"):
            self._table_start(tag, attrs)
            self.stack.append(tag)
            return
        if self.tables and tag not in (
            "table",
            "tr",
            "td",
            "th",
            "caption",
            "thead",
            "tbody",
            "tfoot",
        ):
            if tag == "img" and attrs.get("alt"):
                self.buffer.append(attrs["alt"])
            if tag not in _VOID:
                self.stack.append(tag)
            return
        if tag in _BLOCK:
            self.flush()
        if tag not in _VOID:
            self.stack.append(tag)

        if tag in ("nav", "header", "footer") and not self._in("main", "article"):
            self.furniture_depth += 1
            self.furniture_kind = "page_footer" if tag == "footer" else "page_header"
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.pending_kind.append(("heading", {"level": int(tag[1])}))
        elif tag == "p":
            self.pending_kind.append(("paragraph", {}))
        elif tag in ("ul", "ol"):
            start = _int(attrs.get("start"), 1) if tag == "ol" else 1
            self.lists.append({"ordered": tag == "ol", "counter": start - 1})
        elif tag == "dl":
            self.lists.append({"ordered": False, "counter": 0})
        elif tag in ("li", "dt", "dd"):
            depth = max(0, len(self.lists) - 1) + (1 if tag == "dd" else 0)
            current = self.lists[-1] if self.lists else {"ordered": False, "counter": 0}
            current["counter"] += 1 if tag == "li" else 0
            ordered = current["ordered"] and tag == "li"
            attrs_out: Dict[str, Any] = {"ordered": ordered}
            if ordered:
                attrs_out["marker"] = f"{current['counter']}."
            self.pending_kind.append(("list_item", {"depth": depth, "attrs": attrs_out}))
        elif tag == "pre":
            self.pre_depth += 1
            match = _LANG.search(attrs.get("class") or "")
            self.pre_lang = match.group(1) if match else None
            self.pending_kind.append(
                ("code", {"attrs": {"lang": self.pre_lang} if self.pre_lang else {}})
            )
        elif tag == "code" and self.pre_depth and not self.pre_lang:
            match = _LANG.search(attrs.get("class") or "")
            if match:
                self.pre_lang = match.group(1)
                kind, extra = self.pending_kind[-1]
                self.pending_kind[-1] = (kind, {"attrs": {"lang": self.pre_lang}})
        elif tag in ("figcaption",):
            self.pending_kind.append(("caption", {}))
        elif tag == "img":
            self.flush()
            self.blocks.append(
                RawBlock(
                    kind="figure",
                    text=attrs.get("alt") or "",
                    attrs={"src": attrs.get("src") or ""},
                )
            )
        elif tag == "table":
            self.tables.append(_Table())
        elif tag == "blockquote":
            self.pending_kind.append(("paragraph", {}))

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "title":
            self.in_title = False
            return
        if tag not in self.stack:
            return  # stray close tag
        if self.tables:
            table = self.tables[-1]
            if tag in ("td", "th"):
                if table.cell is not None:
                    table.rows[-1].append(("".join(table.cell),) + table.cell_meta)
                    table.cell = None
            elif tag == "caption":
                pass
            elif tag == "table":
                self._close_table()
            self._pop(tag)
            return
        if tag in _BLOCK:
            self.flush()
        self._pop(tag)
        if (
            tag in ("nav", "header", "footer")
            and self.furniture_depth
            and not self._in("main", "article")
        ):
            self.furniture_depth -= 1
        elif tag in (
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "p",
            "li",
            "dt",
            "dd",
            "figcaption",
            "blockquote",
        ):
            if self.pending_kind:
                self.pending_kind.pop()
        elif tag in ("ul", "ol", "dl"):
            if self.lists:
                self.lists.pop()
        elif tag == "pre":
            self.pre_depth -= 1
            self.pre_lang = None
            if self.pending_kind:
                self.pending_kind.pop()

    def _pop(self, tag: str) -> None:
        while self.stack:
            if self.stack.pop() == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.tables:
            table = self.tables[-1]
            if table.cell is not None:
                table.cell.append(data)
            elif table.caption is not None:
                table.caption.append(data)
            return
        if not self.pre_depth:
            data = re.sub(r"\s+", " ", data)
        self.buffer.append(data)

    def _table_start(self, tag: str, attrs: Dict[str, Optional[str]]) -> None:
        table = self.tables[-1]
        if tag == "tr":
            table.rows.append([])
        elif tag in ("td", "th"):
            if not table.rows:
                table.rows.append([])
            table.cell = []
            table.cell_meta = (tag == "th", _int(attrs.get("rowspan")), _int(attrs.get("colspan")))
        else:
            table.caption = []

    def _close_table(self) -> None:
        table = self.tables.pop()
        grid, header_rows = table.grid()
        if table.caption is not None and "".join(table.caption).strip():
            self.blocks.append(RawBlock(kind="caption", text="".join(table.caption)))
        if grid:
            if self.tables:  # nested table: flatten into the outer cell
                outer = self.tables[-1]
                if outer.cell is not None:
                    outer.cell.append(" ".join(" ".join(r) for r in grid))
                return
            self.blocks.append(
                RawBlock(kind="table", attrs={"rows": grid, "header_rows": header_rows})
            )


def promote_lone_h1(blocks: List[RawBlock]) -> List[RawBlock]:
    """A level-1 heading that is the first body block and the only one is the title,
    and the other headings move up a level.

    Docling maps every ``#``/``h1`` to the title; a document with several
    ``h1``\\ s then has several titles. One leading ``h1`` is the title, several
    are top-level headings.
    """
    body = [b for b in blocks if b.kind not in ("page_header", "page_footer")]
    h1 = [b for b in body if b.kind == "heading" and b.level == 1]
    if not (len(h1) == 1 and body and body[0] is h1[0]):
        return blocks
    out = []
    for b in blocks:
        if b is h1[0]:
            out.append(b.model_copy(update={"kind": "title", "level": None}))
        elif b.kind == "heading":
            # Under the title, ``h2`` is the first heading level (docling's mapping).
            out.append(b.model_copy(update={"level": max(1, (b.level or 2) - 1)}))
        else:
            out.append(b)
    return out


def html_to_blocks(html: str) -> Tuple[List[RawBlock], Dict[str, Any]]:
    """Blocks and metadata (``title``) of an HTML document or fragment."""
    builder = _Builder()
    builder.feed(html)
    builder.close()
    builder.flush()
    while builder.tables:
        builder._close_table()
    title = re.sub(r"\s+", " ", "".join(builder.title_parts)).strip()
    return promote_lone_h1(builder.blocks), ({"title": title} if title else {})


class HtmlParser(Parser):
    """HTML pages and fragments.

    Args:
        encoding: Source encoding; ``None`` means the ``<meta charset>``, else BOM
            or UTF-8.
    """

    name = "html"
    version = "1"
    mimes = frozenset({"text/html", "application/xhtml+xml"})
    extensions = frozenset({".html", ".htm", ".xhtml"})

    def __init__(self, encoding: Optional[str] = None):
        self.encoding = encoding

    def config(self) -> Dict[str, Any]:
        return {"encoding": self.encoding}

    def parse(self, data: bytes, *, name: Optional[str] = None) -> ParsedDoc:
        encoding = self.encoding
        if encoding is None:
            match = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4096], re.I)
            encoding = match.group(1).decode("ascii") if match else None
        blocks, metadata = html_to_blocks(decode_text(data, encoding, what="HTML"))
        return ParsedDoc(blocks=blocks, metadata=metadata, parser=self.name)
