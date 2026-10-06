"""From parsed blocks to a version: element tree, canonical text, spans, pages.

:func:`build_version` is a pure function, which is what makes it golden-testable
(track5 §7.3). It

1. normalises every block's text (``normalize_inline``; ``normalize_block`` for code),
2. nests sections by heading level (a heading opens a section that runs to the
   next heading of the same or a higher level — docling's parents stack,
   ``msword_backend.py`` / ``html_backend.py``),
3. groups consecutive list items into lists, a deeper item opening a nested list,
4. keeps page headers and footers as furniture (in the tree, out of the text),
5. pairs captions with the adjacent table or figure,
6. serialises the body into one Markdown-flavoured canonical text, assigning
   every element its span as it goes, and
7. checks the span invariant before returning.

Canonical serialisation (``SERIALIZER_VERSION``): blocks are separated by a
blank line, items of one list by a newline. A title is ``# text``, a heading of
level L ``#``×(L+1) (docling's Markdown mapping: ``#`` is the title), a list
item ``- text`` or ``3. text`` indented two spaces per depth, code a fenced
block, a table a GFM table. A leaf's span covers its content, never its markup;
a table's span covers the whole table and each cell's span is in
``attrs["cell_spans"]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from operonx_kb.model.document import (
    CONTAINER_KINDS,
    Element,
    Page,
    Region,
    Span,
)
from operonx_kb.model.ids import canonical_json, element_id, fingerprint, sha256_text, text_sha
from operonx_kb.parsing.base import ParsedDoc, RawBlock
from operonx_kb.text.normalize import normalize_block, normalize_inline
from operonx_kb.text.spans import check_elements

__all__ = ["VersionTree", "build_version", "structure_fingerprint", "table_markdown"]

SERIALIZER_VERSION = "1"


def structure_fingerprint() -> str:
    """Fingerprint of the structurer and canonical serializer together."""
    return fingerprint("operonx_kb.structure.build_version", SERIALIZER_VERSION)


@dataclass
class VersionTree:
    """The result of :func:`build_version`."""

    canonical: str
    elements: List[Element]
    pages: List[Page]
    title: Optional[str] = None

    @property
    def text_sha(self) -> str:
        return sha256_text(self.canonical)

    def body(self) -> List[Element]:
        return [e for e in self.elements if e.layer == "body"]

    def by_id(self) -> Dict[str, Element]:
        return {e.id: e for e in self.elements}


# ── the tree, before serialisation ──────────────────────────────────────


@dataclass
class _Node:
    kind: str
    text: str = ""
    level: Optional[int] = None
    depth: int = 0
    regions: List[Region] = field(default_factory=list)
    attrs: Dict[str, Any] = field(default_factory=dict)
    children: List["_Node"] = field(default_factory=list)
    layer: str = "body"
    span: Optional[Span] = None
    id: str = ""


def _clean(block: RawBlock) -> Optional[_Node]:
    """A normalised node for ``block``, or ``None`` when nothing is left of it."""
    attrs = dict(block.attrs)
    if block.kind == "table":
        rows = [[normalize_inline(str(c)) for c in row] for row in attrs.get("rows", [])]
        rows = [r for r in rows if any(r)]
        if not rows:
            return None
        width = max(len(r) for r in rows)
        attrs["rows"] = [r + [""] * (width - len(r)) for r in rows]
        attrs["header_rows"] = min(int(attrs.get("header_rows", 1)), len(rows))
        return _Node("table", regions=list(block.regions), attrs=attrs)
    if block.kind == "code":
        text = normalize_block(block.text)
    else:
        text = normalize_inline(block.text)
    if not text and block.kind not in ("figure",):
        return None
    if block.kind == "figure" and not text:
        text = normalize_inline(str(attrs.get("alt", "")))
    level = block.level
    if block.kind == "heading":
        level = max(1, min(int(level or 1), 6))
    return _Node(
        block.kind,
        text=text,
        level=level,
        depth=max(0, block.depth),
        regions=list(block.regions),
        attrs=attrs,
        layer="furniture" if block.kind in ("page_header", "page_footer") else "body",
    )


def _nest(blocks: List[RawBlock]) -> Tuple[_Node, Optional[str]]:
    root = _Node("document")
    sections: List[_Node] = []  # open sections, outermost first
    lists: List[_Node] = []  # open lists, outermost first
    furniture: List[_Node] = []
    title: Optional[str] = None

    def parent() -> _Node:
        return sections[-1] if sections else root

    for block in blocks:
        node = _clean(block)
        if node is None:
            continue
        if node.layer == "furniture":
            furniture.append(node)
            continue
        if node.kind != "list_item":
            lists.clear()
        if node.kind == "title":
            sections.clear()
            title = title or node.text
            root.children.append(node)
        elif node.kind == "heading":
            while sections and sections[-1].level >= node.level:
                sections.pop()
            section = _Node("section", level=node.level, children=[node])
            parent().children.append(section)
            sections.append(section)
        elif node.kind == "list_item":
            ordered = bool(node.attrs.get("ordered", False))
            while lists and lists[-1].depth > node.depth:
                lists.pop()
            if lists and lists[-1].depth == node.depth and lists[-1].attrs["ordered"] != ordered:
                lists.pop()
            if not lists or lists[-1].depth < node.depth:
                new = _Node("list", depth=node.depth, attrs={"ordered": ordered})
                (lists[-1] if lists else parent()).children.append(new)
                lists.append(new)
            lists[-1].children.append(node)
        else:
            parent().children.append(node)

    _pair_captions(root)
    root.children.extend(furniture)
    return root, title


def _pair_captions(node: _Node) -> None:
    """Link each caption to the adjacent table or figure (one caption each).

    Docling ranks the graphics before and after a caption by box distance
    (``reading_order_rb.py``, predict_to_captions). Without boxes for every
    format we use the publishing convention: a table's caption sits above it, a
    figure's below; otherwise the preceding graphic wins.
    """
    kids = node.children
    taken = set()
    for i, child in enumerate(kids):
        if child.kind == "caption":
            before = kids[i - 1] if i > 0 and kids[i - 1].kind in ("table", "figure") else None
            after = (
                kids[i + 1]
                if i + 1 < len(kids) and kids[i + 1].kind in ("table", "figure")
                else None
            )
            choice = None
            if after is not None and after.kind == "table" and id(after) not in taken:
                choice = after
            elif before is not None and id(before) not in taken:
                choice = before
            elif after is not None and id(after) not in taken:
                choice = after
            if choice is not None:
                taken.add(id(choice))
                child.attrs["_target"] = choice
                choice.attrs["_caption"] = child
        _pair_captions(child)


# ── serialisation ───────────────────────────────────────────────────────


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|")


def table_markdown(rows: List[List[str]]) -> Tuple[str, List[List[Span]]]:
    """A GFM table for ``rows`` (row 0 is the header row) and each cell's span in it.

    Cell spans cover the escaped cell text, empty cells a zero-length span.
    """
    width = max(len(r) for r in rows)
    lines: List[str] = []
    spans: List[List[Span]] = []
    pos = 0
    for r, row in enumerate(rows):
        line = "|"
        row_spans: List[Span] = []
        for cell in row + [""] * (width - len(row)):
            cell = _escape_cell(cell)
            line += " "
            start = pos + len(line)
            line += cell
            row_spans.append((start, start + len(cell)))
            line += " |"
        lines.append(line)
        spans.append(row_spans)
        pos += len(line) + 1
        if r == 0:
            sep = "|" + " --- |" * width
            lines.append(sep)
            pos += len(sep) + 1
    return "\n".join(lines), spans


class _Writer:
    def __init__(self) -> None:
        self.parts: List[str] = []
        self.pos = 0
        self.prev_leaf: Optional[_Node] = None

    def emit(self, s: str) -> None:
        self.parts.append(s)
        self.pos += len(s)

    def separator(self, node: _Node) -> None:
        if self.pos == 0:
            return
        same_list = (
            node.kind == "list_item"
            and self.prev_leaf is not None
            and self.prev_leaf.kind == "list_item"
        )
        self.emit("\n" if same_list else "\n\n")

    def leaf(self, node: _Node) -> None:
        if node.kind == "figure" and not node.text:
            node.span = (self.pos, self.pos)
            return
        self.separator(node)
        if node.kind == "table":
            md, cell_spans = table_markdown(node.attrs["rows"])
            start = self.pos
            self.emit(md)
            node.text = md
            node.span = (start, self.pos)
            node.attrs["cell_spans"] = [
                [(s + start, e + start) for s, e in row] for row in cell_spans
            ]
        else:
            prefix, suffix = _markup(node)
            self.emit(prefix)
            start = self.pos
            self.emit(node.text)
            node.span = (start, self.pos)
            self.emit(suffix)
        self.prev_leaf = node

    def walk(self, node: _Node) -> None:
        if node.layer == "furniture":
            return
        if node.kind in CONTAINER_KINDS:
            for child in node.children:
                self.walk(child)
            body = [c.span for c in node.children if c.span is not None]
            node.span = (body[0][0], body[-1][1]) if body else (self.pos, self.pos)
        else:
            self.leaf(node)


def _markup(node: _Node) -> Tuple[str, str]:
    if node.kind == "title":
        return "# ", ""
    if node.kind == "heading":
        return "#" * min(node.level + 1, 6) + " ", ""
    if node.kind == "list_item":
        marker = str(node.attrs.get("marker") or "") if node.attrs.get("ordered") else ""
        if node.attrs.get("ordered") and not marker:
            marker = "1."
        return "  " * node.depth + (marker or "-") + " ", ""
    if node.kind == "code":
        return "```" + str(node.attrs.get("lang") or "") + "\n", "\n```"
    return "", ""


# ── elements ────────────────────────────────────────────────────────────

_STRUCTURAL_ATTRS = ("ordered", "marker", "lang", "header_rows")


def _content_sha(node: _Node, text: str) -> str:
    attrs = {k: node.attrs[k] for k in _STRUCTURAL_ATTRS if k in node.attrs}
    if node.kind == "table":
        attrs["rows"] = node.attrs["rows"]
    return text_sha(canonical_json([node.kind, node.level, normalize_inline(text), attrs]))


def _elements(root: _Node, canonical: str, version_id: str) -> List[Element]:
    out: List[Element] = []

    def visit(node: _Node, path: str, parent_id: Optional[str], ordinal: int, depth: int) -> None:
        node.id = element_id(version_id, path)
        text = canonical[node.span[0] : node.span[1]] if node.span is not None else node.text
        attrs = {k: v for k, v in node.attrs.items() if not k.startswith("_")}
        out.append(
            Element(
                id=node.id,
                content_sha=_content_sha(node, text),
                version_id=version_id,
                parent_id=parent_id,
                path=path,
                ordinal=ordinal,
                depth=depth,
                kind=node.kind,
                layer=node.layer,
                level=node.level if node.kind in ("heading", "section") else None,
                text=text,
                span=node.span,
                regions=node.regions,
                attrs=attrs,
            )
        )
        for i, child in enumerate(node.children):
            visit(child, f"{path}.{i}", node.id, i, depth + 1)

    visit(root, "0", None, 0, 0)
    # Caption links are node references until every node has its id.
    by_node = {id(n): n for n in _iter(root)}
    index = {e.id: e for e in out}
    for node in by_node.values():
        if "_target" in node.attrs:
            index[node.id].attrs["target"] = node.attrs["_target"].id
        if "_caption" in node.attrs:
            index[node.id].attrs["caption"] = node.attrs["_caption"].id
    return out


def _iter(node: _Node):
    yield node
    for child in node.children:
        yield from _iter(child)


def build_version(parsed: ParsedDoc, version_id: str) -> VersionTree:
    """Build the element tree and canonical text of one version.

    Args:
        parsed: A parser's output.
        version_id: The id of the version being built; element ids derive from it.

    Returns:
        The tree, its canonical text and pages, with the span invariant checked.

    Raises:
        SpanInvariantError: A bug in the serializer (never bad input).
    """
    root, title = _nest(parsed.blocks)
    writer = _Writer()
    writer.walk(root)
    canonical = "".join(writer.parts)
    elements = _elements(root, canonical, version_id)
    check_elements(canonical, elements)
    pages = [
        Page(
            version_id=version_id,
            page_no=p.page_no,
            width=p.width,
            height=p.height,
            unit=p.unit,
            text_layer=p.text_layer,
        )
        for p in parsed.pages
    ]
    title = title or parsed.metadata.get("title") or _first_heading(elements)
    return VersionTree(canonical=canonical, elements=elements, pages=pages, title=title)


def _first_heading(elements: List[Element]) -> Optional[str]:
    for e in elements:
        if e.kind == "heading":
            return e.text
    return None
