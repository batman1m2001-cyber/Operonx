"""Answer ops: the context builder and citation verification (track5 §10, PLAN R6/R7).

The model sits between them, an ``LLMOp`` in :mod:`operonx_kb.graphs.answer`
with the cite-by-span contract of :data:`ANSWER_PROMPT`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from operonx import op

from operonx_kb.errors import CatalogError
from operonx_kb.ops._resources import blobs_of, catalog_of
from operonx_kb.retrieval.citations import verify_citations
from operonx_kb.retrieval.context import build_sources, render_sources
from operonx_kb.stores.catalog.base import Catalog

__all__ = ["ANSWER_PROMPT", "build_context", "check_answer"]

#: The cite-by-span contract. Literal braces are doubled: this is an LLMOp template.
ANSWER_PROMPT = {
    "system": (
        "You answer questions using only the numbered sources you are given.\n"
        "Reply with JSON only, in this shape:\n"
        '{{"answer": "…", "citations": [{{"source": 1, "quote": "…"}}]}}\n'
        "Rules:\n"
        "- Put the source number in brackets, like [1], after each claim it supports.\n"
        "- For every source you mark, add at least one citation whose quote is copied "
        "word for word from that source: a sentence or a phrase, never shortened with an "
        "ellipsis, never reworded, never translated.\n"
        "- Answer in the language of the question.\n"
        "- If the sources do not answer the question, say so and cite nothing."
    ),
    "user": "Sources:\n\n{passages}\n\nQuestion: {question}",
}


def _canonical(cat: Catalog, blobs: str, version_id: str) -> str:
    version = cat.get_version(version_id)
    if version is None:
        raise CatalogError(f"no version {version_id!r}")
    data = blobs_of(blobs).get(version.text_sha)
    if data is None:
        raise CatalogError(
            f"canonical text of {version_id} is missing from the blob store",
            {"sha": version.text_sha},
        )
    return data.decode("utf-8")


@op(bound="cpu", exclude={"trace": ["hits", "sources", "prompt"]}, show_keys="stats")
def build_context(
    hits: list, catalog: str, blobs: str, budget_tokens: int = 1500, neighbours: int = 1
) -> dict:
    """Numbered sources for hydrated hits: neighbours added, touching passages joined,
    packed into ``budget_tokens``; ``prompt`` is how the answer prompt shows them."""
    cat = catalog_of(catalog)
    versions = list(dict.fromkeys(h["version_id"] for h in hits))
    occurrences = {v: cat.version_chunks(v) for v in versions}
    ids = [o.chunk_id for occ in occurrences.values() for o in occ]
    headings = {cid: c.heading_path for cid, c in cat.get_chunks(ids).items()}
    canonicals = {v: _canonical(cat, blobs, v) for v in versions}
    sources = build_sources(
        hits, occurrences, headings, canonicals, neighbours=neighbours, budget_tokens=budget_tokens
    )
    dumped = [s.as_dict() for s in sources]
    stats = {"hits": len(hits), "sources": len(dumped),
             "chars": sum(len(s["text"]) for s in dumped)}  # fmt: skip
    return {"sources": dumped, "prompt": render_sources(dumped), "stats": stats}


@op(bound="cpu", exclude={"trace": ["passages"]}, show_keys="answer")
def check_answer(
    passages: list,
    catalog: str,
    blobs: str,
    answer: Optional[str] = None,
    citations: Optional[list] = None,
    error: Optional[str] = None,
    usage: Optional[dict] = None,
) -> dict:
    """Verify every citation against its source and resolve the verified ones to
    span, elements, pages and boxes. Unverified citations are dropped and reported.

    Args:
        passages: The context's sources (``build_context``'s ``sources``; the input
            is not called ``sources``, a reserved operonx op keyword).

    Raises:
        ValueError: The model's reply did not parse as ``{answer, citations}``.
    """
    if error or answer is None:
        raise ValueError(
            f"the model's reply is not an answer with citations: {error or 'no answer field'}"
        )
    cat = catalog_of(catalog)
    versions = list(dict.fromkeys(s["version_id"] for s in passages))
    canonicals = {v: _canonical(cat, blobs, v) for v in versions}
    elements = {v: cat.elements(v, canonicals[v]) for v in versions}
    out: Dict[str, Any] = verify_citations(answer, citations or [], passages, canonicals, elements)
    shown: List[Dict[str, Any]] = [
        {k: s[k] for k in ("n", "key", "title", "version_id", "heading_path", "pages")}
        for s in passages
    ]
    out.update(sources=shown, usage=usage or {})
    return {"answer": out}
