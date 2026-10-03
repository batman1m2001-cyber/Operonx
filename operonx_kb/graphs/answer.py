"""The answer graph (wiring only; the logic is in :mod:`operonx_kb.ops.answer`).

::

    search ─► build_context ─► LLMOp (cite-by-span JSON) ─► check_answer

``search`` is any search graph (:mod:`operonx_kb.graphs.retrieve`): the answer
graph adds the context, the model and citation verification, and nothing about
how hits are found.
"""

from __future__ import annotations

from operonx import END, START, graph
from operonx.app.serve import egress, ingress
from operonx.providers.ops import LLMOp

from operonx_kb.ops.answer import ANSWER_PROMPT, build_context, check_answer
from operonx_kb.ops.serve import answer_result, search_request

__all__ = ["answer_graph", "build_answer_flow"]


def answer_graph(
    search,
    llm: str,
    *,
    budget_tokens: int = 1500,
    neighbours: int = 1,
    catalog: str = "kb_catalog:main",
    blobs: str = "kb_blob:main",
):
    """Answer ``(query, collection, filter, k)`` from a search's hits with verified citations.

    Args:
        search: A search graph (``search_graph(...)`` or ``reranked(...)``).
        llm: The ``llm:`` resource name of the answer model.
        budget_tokens: Tokens the sources may take in the prompt.
        neighbours: Chunks added on each side of a hit, within its section.
    """

    @graph
    def answer(query, collection, filter, k):
        found = search(query=query, collection=collection, filter=filter, k=k)
        context = build_context(
            hits=found["hits"],
            catalog=catalog,
            blobs=blobs,
            budget_tokens=budget_tokens,
            neighbours=neighbours,
        )
        model = LLMOp.of(
            resource=llm,
            prompt=ANSWER_PROMPT,
            fields=["answer: str", "citations?: list"],
            parser="json",
            passages=context["prompt"],
            question=query,
        )
        checked = check_answer(
            passages=context["sources"],
            catalog=catalog,
            blobs=blobs,
            answer=model["answer"],
            citations=model["citations"],
            error=model["error"],
            usage=model["usage"],
        )
        START >> found >> context >> model >> checked >> END

    return answer


def build_answer_flow(answer, *, k: int = 8):
    """An answer graph behind doors: each item ``{"query", "collection", "filter"?, "k"?}``
    gets one answer back."""

    @graph
    def answer_flow():
        src = ingress()
        req = search_request(item=src["item"], k=k)
        answered = answer(query=req["query"], collection=req["collection"], filter=req["filter"],
                          k=req["k"])  # fmt: skip
        res = answer_result(answer=answered["answer"])
        out = egress(item=res["result"])
        START >> src >> req >> answered >> res >> out >> END

    return answer_flow
