"""Plain text: paragraphs are separated by blank lines."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from operonx_kb.parsing._text import decode_text
from operonx_kb.parsing.base import ParsedDoc, Parser, RawBlock

__all__ = ["PlainTextParser"]

_BLANK = re.compile(r"\n(?:[ \t]*\n)+")


class PlainTextParser(Parser):
    """Each run of non-blank lines is one paragraph; lines inside it are joined.

    Args:
        encoding: Source encoding; ``None`` means BOM or UTF-8 (see :func:`decode_text`).
    """

    name = "plain"
    version = "1"
    mimes = frozenset({"text/plain"})
    extensions = frozenset({".txt", ".text", ".log"})

    def __init__(self, encoding: Optional[str] = None):
        self.encoding = encoding

    def config(self) -> Dict[str, Any]:
        return {"encoding": self.encoding}

    def parse(self, data: bytes, *, name: Optional[str] = None) -> ParsedDoc:
        text = decode_text(data, self.encoding).replace("\r\n", "\n").replace("\r", "\n")
        blocks = [RawBlock(kind="paragraph", text=p) for p in _BLANK.split(text) if p.strip()]
        return ParsedDoc(blocks=blocks, parser=self.name)
