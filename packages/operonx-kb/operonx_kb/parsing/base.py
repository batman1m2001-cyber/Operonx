"""The parser contract: bytes in, :class:`ParsedDoc` out.

A :class:`ParsedDoc` is a flat, reading-ordered list of :class:`RawBlock`\\ s
plus page geometry. It is parser-neutral on purpose: no parser's own types
(docling-core, XML trees) cross this line, so every format — and later every
third-party parser — feeds the same structurer (track5 §7.2, PLAN D1).

Parsers are pure, CPU-bound and synchronous: the ``parse`` op runs them in a
worker thread (``@op(bound="cpu")``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, FrozenSet, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from operonx_kb.model.document import Region
from operonx_kb.model.ids import fingerprint

__all__ = ["BlockKind", "RawBlock", "PageInfo", "ParsedDoc", "Parser"]

BlockKind = Literal[
    "title",
    "heading",
    "paragraph",
    "list_item",
    "table",
    "figure",
    "caption",
    "formula",
    "code",
    "footnote",
    "kv",
    "page_header",
    "page_footer",
]


class RawBlock(BaseModel):
    """One block of a parsed document, in reading order.

    Attributes:
        kind: What the block is.
        text: Its text, as extracted; the structurer normalises it. Ignored for
            tables, whose text is serialised from ``attrs["rows"]``.
        level: Heading level, 1 = top.
        depth: List nesting depth, 0 = top.
        regions: Where the block is on its page(s), normalised top-left bboxes.
        attrs: ``list_item``: ``ordered`` (bool), ``marker`` (``"3."``);
            ``table``: ``rows`` (list of rows of cell strings), ``header_rows``;
            ``code``: ``lang``; ``figure``: ``alt``; anchors such as ``sheet``,
            ``slide``, ``style``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: BlockKind
    text: str = ""
    level: Optional[int] = None
    depth: int = 0
    regions: List[Region] = Field(default_factory=list)
    attrs: Dict[str, Any] = Field(default_factory=dict)


class PageInfo(BaseModel):
    """Page geometry of a paginated source."""

    model_config = ConfigDict(extra="forbid")

    page_no: int
    width: float
    height: float
    unit: Literal["pt", "px"] = "pt"
    text_layer: bool = True


class ParsedDoc(BaseModel):
    """What a parser returns.

    Attributes:
        blocks: In reading order.
        pages: Empty for sources without pages.
        metadata: ``title`` and whatever else the format declares.
        parser: The parser's name, for stats and traces.
        stats: Parser-specific counts and timings.
    """

    model_config = ConfigDict(extra="forbid")

    blocks: List[RawBlock] = Field(default_factory=list)
    pages: List[PageInfo] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    parser: str = ""
    stats: Dict[str, Any] = Field(default_factory=dict)


class Parser(ABC):
    """A document format reader.

    Subclasses set ``name``, ``version`` (bump it whenever output changes, so
    re-ingest sees a new pipeline fingerprint), ``mimes`` and ``extensions``,
    and implement :meth:`parse`.
    """

    name: ClassVar[str]
    version: ClassVar[str]
    mimes: ClassVar[FrozenSet[str]] = frozenset()
    extensions: ClassVar[FrozenSet[str]] = frozenset()
    #: Read by the parse op; a remote parser would say ``"io"``.
    bound: ClassVar[str] = "cpu"

    def config(self) -> Dict[str, Any]:
        """Settings that change the output; part of the fingerprint."""
        return {}

    def fingerprint(self) -> str:
        cls = type(self)
        return fingerprint(f"{cls.__module__}.{cls.__qualname__}", self.version, self.config())

    @abstractmethod
    def parse(self, data: bytes, *, name: Optional[str] = None) -> ParsedDoc:
        """Read ``data``.

        Args:
            data: The source bytes.
            name: The file name, when known (a hint, e.g. for the title).

        Raises:
            DocumentParseError: The bytes are not a readable document of this format.
        """
