"""Markdown (CommonMark block structure plus GFM tables), line based, no dependency.

Blocks: ATX (``#``) and setext (``===``/``---``) headings, fenced code (``````/``~~~``,
info string = language), indented code, bullet (``-*+``) and ordered
(``1.``/``1)``) list items nested by indentation, block quotes (their content
parsed as Markdown), GFM pipe tables, thematic breaks (dropped), YAML front
matter (``title:`` becomes metadata), standalone images (figures) and raw HTML
blocks (parsed by :mod:`operonx_kb.parsing.html`, as docling's ``md_backend``
hands HTML to its HTML backend).

Inline markup is reduced to its text: emphasis markers, code-span backticks,
links (``[text](url)`` → ``text``), images (→ alt), autolinks, escapes. A lone
leading ``#`` heading is the title (:func:`promote_lone_h1`).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from operonx_kb.parsing._text import decode_text
from operonx_kb.parsing.base import ParsedDoc, Parser, RawBlock
from operonx_kb.parsing.html import html_to_blocks, promote_lone_h1

__all__ = ["MarkdownParser", "markdown_to_blocks", "strip_inline"]

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`\s]*)")
_HR = re.compile(r"^ {0,3}((\*[ \t]*){3,}|(-[ \t]*){3,}|(_[ \t]*){3,})$")
_ITEM = re.compile(r"^( *)([-*+]|(\d{1,9})([.)]))(?:[ \t]+(.*)|$)")
_QUOTE = re.compile(r"^ {0,3}> ?")
_TABLE_SEP = re.compile(r"^ *\|? *:?-+:? *(\| *:?-+:? *)*\|? *$")
_IMAGE_LINE = re.compile(r"^ *!\[([^\]]*)\]\(([^)]*)\) *$")
_HTML_BLOCK = re.compile(r"^ {0,3}<(/?[a-zA-Z][a-zA-Z0-9-]*|!--)")

_INLINE = [
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\[[^\]]*\]"), r"\1"),
    (re.compile(r"<(https?://[^>]+)>"), r"\1"),
    (re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1"), r"\2"),
    (re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"), r"\1"),
    (re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])"), r"\1"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"\1"),
    (re.compile(r"`+([^`]+?)`+"), r"\1"),
    (re.compile(r"<[^>\n]+>"), ""),
]


_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
# Escaped characters are parked in the Private Use Area while the inline
# patterns run, so ``\*literal\*`` is never read as emphasis.
_PARK = 0xF0000


def strip_inline(text: str) -> str:
    """The text of a Markdown inline run."""
    text = _ESCAPE.sub(lambda m: chr(_PARK + ord(m.group(1))), text)
    for pattern, repl in _INLINE:
        text = pattern.sub(repl, text)
    return re.sub("[\U000f0000-\U000f00ff]", lambda m: chr(ord(m.group()) - _PARK), text)


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells = re.split(r"(?<!\\)\|", line)
    return [strip_inline(c.strip()) for c in cells]


def _front_matter(lines: List[str]) -> Tuple[Dict[str, Any], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            meta: Dict[str, Any] = {}
            for line in lines[1:i]:
                key, sep, value = line.partition(":")
                if sep and key.strip() == "title":
                    meta["title"] = value.strip().strip("'\"")
            return meta, i + 1
    return {}, 0


def markdown_to_blocks(text: str) -> Tuple[List[RawBlock], Dict[str, Any]]:
    """Blocks and metadata of a Markdown document."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n")
    meta, start = _front_matter(lines)
    blocks = _parse_lines(lines[start:])
    return promote_lone_h1(blocks), meta


def _parse_lines(lines: List[str]) -> List[RawBlock]:  # noqa: C901 — one state machine
    blocks: List[RawBlock] = []
    para: List[str] = []
    # Open list items as (content indent, depth); continuation lines indented
    # at least the content indent belong to the innermost such item.
    items: List[Tuple[int, int]] = []

    def flush_para() -> None:
        if para:
            text = " ".join(line.strip() for line in para)
            image = _IMAGE_LINE.match(" ".join(para))
            if image and len(para) == 1:
                blocks.append(
                    RawBlock(kind="figure", text=image.group(1), attrs={"src": image.group(2)})
                )
            else:
                blocks.append(RawBlock(kind="paragraph", text=strip_inline(text)))
            para.clear()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_para()
            i += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            flush_para()
            marker = fence.group(2)
            lang = fence.group(3) or None
            body: List[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith(marker[0] * len(marker)):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence (or end of document)
            blocks.append(
                RawBlock(kind="code", text="\n".join(body), attrs={"lang": lang} if lang else {})
            )
            continue

        # A paragraph line followed by a setext underline is a heading.
        if para and _SETEXT.match(line):
            level = 1 if stripped.startswith("=") else 2
            text = " ".join(p.strip() for p in para)
            para.clear()
            blocks.append(RawBlock(kind="heading", level=level, text=strip_inline(text)))
            i += 1
            continue

        if _HR.match(line):
            flush_para()
            items.clear()
            i += 1
            continue

        atx = _ATX.match(line)
        if atx:
            flush_para()
            items.clear()
            blocks.append(
                RawBlock(
                    kind="heading", level=len(atx.group(1)), text=strip_inline(atx.group(2) or "")
                )
            )
            i += 1
            continue

        if _QUOTE.match(line):
            flush_para()
            items.clear()
            quoted: List[str] = []
            while i < n and lines[i].strip() and _QUOTE.match(lines[i]):
                quoted.append(_QUOTE.sub("", lines[i], count=1))
                i += 1
            blocks.extend(_parse_lines(quoted))
            continue

        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush_para()
            items.clear()
            header = _split_row(line)
            rows = [header]
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                row = _split_row(lines[i])
                rows.append(
                    (row + [""] * len(header))[: len(header)]
                )  # GFM: pad/truncate to header
                i += 1
            blocks.append(RawBlock(kind="table", attrs={"rows": rows, "header_rows": 1}))
            continue

        if _HTML_BLOCK.match(line) and not para:
            items.clear()
            html: List[str] = []
            while i < n and lines[i].strip():
                html.append(lines[i])
                i += 1
            html_blocks, _ = html_to_blocks("\n".join(html))
            blocks.extend(b for b in html_blocks if b.kind not in ("page_header", "page_footer"))
            continue

        item = _ITEM.match(line)
        if item and (item.group(5) is not None or not para):
            flush_para()
            indent = len(item.group(1))
            # Close items this line is not nested in; the innermost open item
            # whose content starts at or before the marker is the parent.
            while items and indent < items[-1][0]:
                items.pop()
            depth = items[-1][1] + 1 if items else 0
            content = item.group(5) or ""
            content_indent = indent + len(item.group(2)) + 1
            ordered = item.group(3) is not None
            attrs: Dict[str, Any] = {"ordered": ordered}
            if ordered:
                attrs["marker"] = f"{int(item.group(3))}."
            items.append((content_indent, depth))
            text_lines = [content]
            i += 1
            # Lazy continuation lines of this item.
            while i < n and lines[i].strip() and not _ITEM.match(lines[i]) and not _ATX.match(lines[i]) \
                    and not _FENCE.match(lines[i]) and not _HR.match(lines[i]):  # fmt: skip
                text_lines.append(lines[i].strip())
                i += 1
            blocks.append(
                RawBlock(
                    kind="list_item",
                    text=strip_inline(" ".join(text_lines)),
                    depth=depth,
                    attrs=attrs,
                )
            )
            continue

        if not para and items and len(line) - len(line.lstrip()) >= items[-1][0]:
            # A new paragraph inside a list item (after a blank line).
            para.append(line)
            i += 1
            continue

        if not para and line.startswith("    "):
            items.clear()
            code: List[str] = []
            while i < n and (lines[i].startswith("    ") or not lines[i].strip()):
                code.append(lines[i][4:])
                i += 1
            blocks.append(RawBlock(kind="code", text="\n".join(code)))
            continue

        if not para:
            items.clear()
        para.append(line)
        i += 1

    flush_para()
    return blocks


class MarkdownParser(Parser):
    """Markdown documents.

    Args:
        encoding: Source encoding; ``None`` means BOM or UTF-8.
    """

    name = "markdown"
    version = "1"
    mimes = frozenset({"text/markdown", "text/x-markdown"})
    extensions = frozenset({".md", ".markdown", ".mdown"})

    def __init__(self, encoding: Optional[str] = None):
        self.encoding = encoding

    def config(self) -> Dict[str, Any]:
        return {"encoding": self.encoding}

    def parse(self, data: bytes, *, name: Optional[str] = None) -> ParsedDoc:
        blocks, meta = markdown_to_blocks(decode_text(data, self.encoding, what="Markdown"))
        return ParsedDoc(blocks=blocks, metadata=meta, parser=self.name)
