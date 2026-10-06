"""The per-collection pipeline: which parser, structurer and chunker, and their fingerprint.

``pipeline_fp`` combines the fingerprints of the parser chosen for the file,
the structurer/serializer, the chunker and the enabled enrichers (track5 §6.3,
PLAN E8). A version's id includes it, so changing any of them makes the next
ingest a new version instead of silently mixing outputs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from operonx_kb.chunking import Chunker, chunker_from_spec
from operonx_kb.model.collection import CollectionSpec
from operonx_kb.model.document import Element, Page
from operonx_kb.model.ids import combine_fingerprints
from operonx_kb.parsing.base import Parser
from operonx_kb.parsing.router import ParserRouter
from operonx_kb.structure.build import VersionTree, structure_fingerprint

__all__ = ["Pipeline", "tree_to_dict", "tree_from_dict"]


class Pipeline:
    """Parser routing and chunking for one collection spec."""

    def __init__(self, spec: CollectionSpec, router: Optional[ParserRouter] = None):
        self.spec = spec
        self.router = router or ParserRouter()
        self.chunker: Chunker = chunker_from_spec(spec.chunker)

    def parser_for(
        self, data: bytes, name: Optional[str] = None, mime: Optional[str] = None
    ) -> Parser:
        return self.router.for_file(data, name=name, mime=mime)

    def parser_named(self, name: str) -> Parser:
        for parser in self.router.parsers:
            if parser.name == name:
                return parser
        raise KeyError(f"no parser named {name!r}")

    def fingerprint(self, parser: Parser, enrichers: Optional[Mapping[str, str]] = None) -> str:
        """``pipeline_fp``. ``enrichers`` maps each enabled enrichment stage to its
        fingerprint; a collection without enrichment has the fingerprint it always had."""
        return combine_fingerprints(
            parser=parser.fingerprint(),
            structure=structure_fingerprint(),
            chunker=self.chunker.fingerprint(),
            **{f"enrich_{k}": v for k, v in sorted((enrichers or {}).items())},
        )


def tree_to_dict(tree: VersionTree) -> Dict[str, Any]:
    """A JSON-safe form of a version tree, for passing between ops."""
    return {
        "canonical": tree.canonical,
        "title": tree.title,
        "elements": [e.model_dump(mode="json") for e in tree.elements],
        "pages": [p.model_dump(mode="json") for p in tree.pages],
    }


def tree_from_dict(data: Dict[str, Any]) -> VersionTree:
    elements: List[Element] = []
    for e in data["elements"]:
        e = dict(e)
        if e.get("span") is not None:
            e["span"] = tuple(e["span"])
        elements.append(Element.model_validate(e))
    return VersionTree(
        canonical=data["canonical"],
        elements=elements,
        pages=[Page.model_validate(p) for p in data["pages"]],
        title=data.get("title"),
    )
