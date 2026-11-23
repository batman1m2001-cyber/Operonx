"""Research ops: the logic of :func:`operonx_kb.graphs.answer.research_answer`
(:mod:`operonx_kb.retrieval.research`). The researcher is an ``LLMOp`` in the graph."""

from __future__ import annotations

from typing import Optional

from operonx import op

from operonx_kb.retrieval import research

__all__ = ["research_search", "research_gather", "research_next"]


@op
def research_search(question: str, next: Optional[str] = None) -> dict:  # noqa: A002 — the loop's cell
    """The query of this iteration: the question itself first, then the researcher's."""
    return {"query": next or question}


@op(bound="cpu", exclude={"trace": ["prior", "new", "hits", "messages"]}, show_keys="stats")
def research_gather(question: str, query: str, new: list, max_searches: int,
                    prior: Optional[list] = None, searches: Optional[list] = None) -> dict:  # fmt: skip
    """This search's hits added to the earlier ones, and the researcher's messages."""
    hits = research.merge_hits(prior, new)
    made = [*(searches or []), query]
    messages = research.research_request(question, hits, made, max(0, max_searches - len(made)))
    stats = {"search": len(made), "new": len(hits) - len(prior or []), "hits": len(hits)}
    return {"hits": hits, "searches": made, "messages": messages, "stats": stats}


@op(exclude={"trace": ["hits", "kept"]}, show_keys="stats")
def research_next(hits: list, searches: list, max_searches: int, search: Optional[str] = None,
                  keep: Optional[list] = None, usage: Optional[dict] = None,
                  cost_usd: Optional[float] = None, spent: Optional[dict] = None) -> dict:  # fmt: skip
    """Apply the researcher's reply: the next search or done, the kept hits, and the
    researcher's usage so far (``spent``)."""
    out = research.research_advance(search, keep, hits, searches, max_searches)
    spent = research.add_usage(spent, {**(usage or {}), "cost_usd": cost_usd or 0.0})
    return {"next": out["next"], "done": out["done"], "kept": out["kept"], "stats": out["stats"],
            "spent": spent, "trail": {"searches": list(searches), "usage": spent}}  # fmt: skip
