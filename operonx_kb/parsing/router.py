"""Choosing a parser for a file: by declared MIME type, else by extension, else by content.

The router is a pure function of its parser list, so its fingerprint is the set
of its parsers' fingerprints: adding or upgrading a parser is a visible change
for every document it would now read.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Dict, List, Optional, Sequence

from operonx_kb.errors import UnsupportedFormatError
from operonx_kb.model.ids import combine_fingerprints
from operonx_kb.parsing.base import Parser
from operonx_kb.parsing.docx import DocxParser
from operonx_kb.parsing.html import HtmlParser
from operonx_kb.parsing.markdown import MarkdownParser
from operonx_kb.parsing.plain import PlainTextParser
from operonx_kb.parsing.pptx import PptxParser
from operonx_kb.parsing.xlsx import XlsxParser
from operonx_kb.pdf.parser import PdfParser

__all__ = ["ParserRouter", "default_parsers", "sniff_mime"]


def default_parsers() -> List[Parser]:
    """Every built-in parser. The PDF parser needs the ``pdf`` extra only when used."""
    return [
        PlainTextParser(),
        MarkdownParser(),
        HtmlParser(),
        DocxParser(),
        PptxParser(),
        XlsxParser(),
        PdfParser(),
    ]


def sniff_mime(data: bytes, name: Optional[str] = None) -> Optional[str]:
    """A MIME type from the file name, then from the leading bytes."""
    ext = PurePath(name).suffix.lower() if name else ""
    by_ext = {
        ".txt": "text/plain", ".text": "text/plain", ".log": "text/plain",
        ".md": "text/markdown", ".markdown": "text/markdown", ".mdown": "text/markdown",
        ".html": "text/html", ".htm": "text/html", ".xhtml": "application/xhtml+xml",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    }  # fmt: skip
    if ext in by_ext:
        return by_ext[ext]
    head = data[:1024].lstrip()
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if data[:4] == b"PK\x03\x04":
        if b"word/" in data[:4096]:
            return by_ext[".docx"]
        if b"ppt/" in data[:4096]:
            return by_ext[".pptx"]
        if b"xl/" in data[:4096]:
            return by_ext[".xlsx"]
    return None


class ParserRouter:
    """Maps a file to the parser that reads it.

    Args:
        parsers: The parsers to choose from, first match wins; default
            :func:`default_parsers`.
    """

    def __init__(self, parsers: Optional[Sequence[Parser]] = None):
        self.parsers = list(parsers) if parsers is not None else default_parsers()
        self._by_mime: Dict[str, Parser] = {}
        for parser in self.parsers:
            for mime in parser.mimes:
                self._by_mime.setdefault(mime, parser)

    def fingerprint(self) -> str:
        return combine_fingerprints(**{p.name: p.fingerprint() for p in self.parsers})

    def for_file(self, data: bytes, *, name: Optional[str] = None, mime: Optional[str] = None) -> Parser:
        """The parser for this file.

        Raises:
            UnsupportedFormatError: No parser reads it; the message lists the types that are read.
        """
        mime = mime or sniff_mime(data, name)
        if mime and mime.split(";")[0].strip() in self._by_mime:
            return self._by_mime[mime.split(";")[0].strip()]
        raise UnsupportedFormatError(
            f"no parser for {name or 'this file'} (type {mime or 'unknown'}). "
            "Pass mime= if the type is known, or add a Parser for it.",
            {"supported": sorted(self._by_mime)},
        )
