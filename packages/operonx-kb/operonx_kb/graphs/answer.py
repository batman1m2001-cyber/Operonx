"""The answer graph (wiring only; the logic is in :mod:`operonx_kb.ops.answer`).

::

    answer:           ranked_search ─► build_context ─► LLMOp (cite-by-span JSON) ─► check_answer
    research_answer:  research_search ─► ranked_search ─► research_gather ─► LLMOp (researcher) ─► research_next ─┐
                            ▲                                                                                    │
                            └──────────────────────────── not done ◄──────────────────────────────────────────┤
                      build_context(kept) ─► LLMOp (cite-by-span JSON) ─► check_answer ◄──────── done ────────────┘

Defined once, at module level (operonx guide 05): the search mode, the reranker
and the answer model (``llm``, an ``llm:`` resource's name) are inputs.
"""

from __future__ import annotations

from operonx import END, PARENT, START, graph
from operonx.app.serve import egress, ingress
from operonx.core.ops import if_
from operonx.providers.ops import LLMOp

from operonx_kb.graphs.retrieve import ranked_search
from operonx_kb.ops.answer import ANSWER_PROMPT, build_context, check_answer
from operonx_kb.ops.research import research_gather, research_next, research_search
from operonx_kb.ops.serve import answer_result, search_request

__all__ = ["answer", "research_answer", "answer_flow"]

#: A hard stop above any search budget a caller can ask for (kb.ask caps it at MAX_SEARCHES).
RESEARCH_LOOP_CAP = 20


@graph
def answer(query, collection, filter, k, mode, reranker, rerank_depth, llm, budget_tokens,
           neighbours, catalog, blobs):  # fmt: skip
    """Answer ``query`` from a search's hits with verified citations.

    ``budget_tokens``: tokens the sources may take in the prompt; ``neighbours``:
    chunks added on each side of a hit, within its section.
    """
    found = ranked_search(query=query, collection=collection, filter=filter, k=k, mode=mode,
                          reranker=reranker, rerank_depth=rerank_depth, catalog=catalog)  # fmt: skip
    context = build_context(hits=found["hits"], catalog=catalog, blobs=blobs,
                            budget_tokens=budget_tokens, neighbours=neighbours)  # fmt: skip
    model = LLMOp.of(
        resource=llm,
        prompt=ANSWER_PROMPT,
        fields=["answer: str", "citations?: list"],
        parser="json",
        passages=context["prompt"],
        question=query,
    )
    checked = check_answer(passages=context["sources"], catalog=catalog, blobs=blobs,
                           answer=model["answer"], citations=model["citations"],
                           usage=model["usage"], cost_usd=model["cost_usd"])  # fmt: skip
    START >> found >> context >> model >> checked >> END


@graph
def research_answer(query, collection, filter, k, mode, reranker, rerank_depth, llm, budget_tokens,
                    neighbours, max_searches, catalog, blobs):  # fmt: skip
    """Answer ``query`` after up to ``max_searches`` searches: the first searches the
    question, then the researcher (``llm``) reads every passage found so far and names
    the next search, or none when the passages answer the question. The passages it
    keeps are the answer's sources (:mod:`operonx_kb.retrieval.research`).
    """
    PARENT.declare(next=None, hits=None, searches=None, spent=None)  # loop state
    q = research_search(question=query, next=PARENT["next"])
    found = ranked_search(query=q["query"], collection=collection, filter=filter, k=k, mode=mode,
                          reranker=reranker, rerank_depth=rerank_depth, catalog=catalog)  # fmt: skip
    got = research_gather(question=query, query=q["query"], new=found["hits"],
                          max_searches=max_searches, prior=PARENT["hits"],
                          searches=PARENT["searches"])  # fmt: skip
    got["hits"] >> PARENT["hits"]
    got["searches"] >> PARENT["searches"]
    plan = LLMOp.of(resource=llm, messages=got["messages"], fields=["search?: str", "keep?: list"],
                    parser="json", max_retries=1)  # fmt: skip
    move = research_next(hits=got["hits"], searches=got["searches"], max_searches=max_searches,
                         search=plan["search"], keep=plan["keep"], usage=plan["usage"],
                         cost_usd=plan["cost_usd"], spent=PARENT["spent"])  # fmt: skip
    move["next"] >> PARENT["next"]
    move["spent"] >> PARENT["spent"]
    context = build_context(hits=move["kept"], catalog=catalog, blobs=blobs,
                            budget_tokens=budget_tokens, neighbours=neighbours)  # fmt: skip
    model = LLMOp.of(
        resource=llm,
        prompt=ANSWER_PROMPT,
        fields=["answer: str", "citations?: list"],
        parser="json",
        passages=context["prompt"],
        question=query,
    )
    checked = check_answer(passages=context["sources"], catalog=catalog, blobs=blobs,
                           answer=model["answer"], citations=model["citations"],
                           usage=model["usage"], cost_usd=model["cost_usd"],
                           research=move["trail"])  # fmt: skip
    START >> q >> found >> got >> plan >> move
    move >> if_(move["done"] == True, context, max_iterations=RESEARCH_LOOP_CAP).else_(q)  # noqa: E712
    context >> model >> checked >> END


@graph
def answer_flow(mode, reranker, rerank_depth, k, llm, budget_tokens, neighbours, catalog, blobs):
    """Answers behind doors: each item ``{"query", "collection", "filter"?, "k"?}`` gets
    one answer back; the run's inputs are the same for every item."""
    src = ingress()
    req = search_request(item=src["item"], k=k)
    answered = answer(query=req["query"], collection=req["collection"], filter=req["filter"],
                      k=req["k"], mode=mode, reranker=reranker, rerank_depth=rerank_depth,
                      llm=llm, budget_tokens=budget_tokens, neighbours=neighbours,
                      catalog=catalog, blobs=blobs)  # fmt: skip
    res = answer_result(answer=answered["answer"])
    out = egress(item=res["result"])
    START >> src >> req >> answered >> res >> out >> END
