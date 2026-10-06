"""The answer graph (wiring only; the logic is in :mod:`operonx_kb.ops.answer`).

::

    ranked_search ─► build_context ─► LLMOp (cite-by-span JSON) ─► check_answer

Defined once, at module level (operonx guide 05): the search mode, the reranker
and the answer model (``llm``, an ``llm:`` resource's name) are inputs.
"""

from __future__ import annotations

from operonx import END, START, graph
from operonx.app.serve import egress, ingress
from operonx.providers.ops import LLMOp

from operonx_kb.graphs.retrieve import ranked_search
from operonx_kb.ops.answer import ANSWER_PROMPT, build_context, check_answer
from operonx_kb.ops.serve import answer_result, search_request

__all__ = ["answer", "answer_flow"]


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
                           usage=model["usage"])  # fmt: skip
    START >> found >> context >> model >> checked >> END


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
