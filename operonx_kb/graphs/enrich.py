"""Enrichment graphs (wiring only; the logic is in :mod:`operonx_kb.ops.enrich`).

One stage shape for every model call of ingest (PLAN E1)::

    lookup_answers ─► if misses ─► each_request ─► LLMOp (parallel) ─► keep_answer ─► store_answers ─┐
                      else ───────────────────────────────────────────────────────────► stage_answers ◄┘

A stage whose requests are all cached runs no model op at all, so a re-ingest
of unchanged content shows no ``LLMOp`` span in its trace.

The factories return the subgraphs the ingest graph wires in, chosen by the
collection's spec; a collection without an enricher gets a pass-through of the
same shape, so the ingest graph has one wiring:

- :func:`contextual_graph` / :func:`no_contextual`: ``(fresh, drafted, requests,
  keys, enrichers) → (todo, chunks, stats)``. The inputs are not named like the
  outputs: a graph's input and output of one name are one cell, and a stage that
  failed would hand its inputs on as its outputs.
- :func:`tree_graph` / :func:`no_tree`: ``(tree, plan, enrichers) → (nodes, stats)``.
"""

from __future__ import annotations

from typing import Optional

from operonx import END, START, graph
from operonx.core.ops import if_
from operonx.providers.ops import LLMOp

from operonx_kb.model.collection import ContextualSpec, TreeSpec
from operonx_kb.ops.enrich import (
    apply_contexts,
    each_request,
    finish_tree,
    keep_answer,
    keep_chunks,
    lookup_answers,
    no_tree_nodes,
    plan_summaries,
    plan_toc,
    stage_answers,
    store_answers,
)

__all__ = ["llm_stage", "contextual_graph", "no_contextual", "tree_graph", "no_tree"]

#: The answer limit of a table-of-contents call (a few dozen titles).
TOC_MAX_TOKENS = 2000


def llm_stage(
    kind: str,
    llm: str,
    *,
    parallel: int,
    max_tokens: Optional[int] = None,
    structured: Optional[str] = None,
    catalog: str = "kb_catalog:main",
):
    """A cached model stage: ``(requests, enrichers) → (answers, stats)``.

    Args:
        kind: The stage (``"contextual"``, ``"summary"``, ``"toc"``): its key in
            ``enrichers`` (the stage fingerprints) and in the cache.
        llm: The ``llm:`` resource, by the name ``LLMOp`` takes.
        parallel: Model calls in flight at once.
        max_tokens: The answer limit.
        structured: A JSON answer's one field (``"sections: list"``); without it
            the answer is the model's text.
    """
    value = structured.split(":", 1)[0].strip() if structured else "content"
    parsing = {"fields": [structured], "parser": "json", "max_retries": 1} if structured else {}

    @graph
    def stage(requests, enrichers):
        look = lookup_answers(requests=requests, enrichers=enrichers, kind=kind, catalog=catalog)
        each = each_request(requests=look["misses"])
        call = LLMOp.of(
            resource=llm,
            messages=each["messages"].parallel(max=parallel),
            max_tokens=max_tokens,
            **parsing,
        )
        kept = keep_answer(
            key=each["key"],
            value=call[value],
            usage=call["usage"],
            cost_usd=call["cost_usd"],
            model=call["model_used"],
            error=call.get("error"),
        )
        saved = store_answers(
            answers=kept["answer"].collect(), enrichers=enrichers, kind=kind, catalog=catalog
        )
        done = stage_answers(
            kind=kind,
            found=look["found"],
            misses=look["misses"],
            cached=look["cached"],
            fresh=saved["fresh"],
            usage=saved["usage"],
        )
        START >> look >> if_(look["count"] > 0, each).else_(done)
        each >> call >> kept >> saved >> done
        done >> END

    return stage


def contextual_graph(spec: ContextualSpec, *, catalog: str = "kb_catalog:main"):
    """Contextual enrichment of a version's new chunks (PLAN E2)."""
    contexts = llm_stage(
        "contextual", spec.llm, parallel=spec.parallel, max_tokens=spec.max_tokens, catalog=catalog
    )

    @graph
    def contextualize(fresh, drafted, requests, keys, enrichers):
        answers = contexts(requests=requests, enrichers=enrichers)
        applied = apply_contexts(
            fresh=fresh,
            drafted=drafted,
            keys=keys,
            answers=answers["answers"],
            stats=answers["stats"],
        )
        START >> answers >> applied >> END

    return contextualize


def no_contextual():
    """The chunks as chunked: the collection has no ``contextual`` spec."""

    @graph
    def contextualize(fresh, drafted, requests, keys, enrichers):
        kept = keep_chunks(fresh=fresh, drafted=drafted)
        START >> kept >> END

    return contextualize


def tree_graph(spec: TreeSpec, *, catalog: str = "kb_catalog:main"):
    """A version's tree index: nodes from headings or a synthesized table of
    contents, each with a summary (PLAN E5, E6)."""
    toc_stage = llm_stage(
        "toc", spec.llm, parallel=spec.parallel, max_tokens=TOC_MAX_TOKENS,
        structured="sections: list", catalog=catalog,
    )  # fmt: skip
    summary_stage = llm_stage(
        "summary", spec.llm, parallel=spec.parallel, max_tokens=spec.max_tokens, catalog=catalog
    )

    @graph
    def tree_index(tree, plan, enrichers):
        toc_plan = plan_toc(tree=tree, plan=plan)
        toc = toc_stage(requests=toc_plan["requests"], enrichers=enrichers)
        shape = plan_summaries(
            tree=tree,
            plan=plan,
            blocks=toc_plan["blocks"],
            ranges=toc_plan["ranges"],
            toc=toc["answers"],
            requests=toc_plan["requests"],
        )
        summaries = summary_stage(requests=shape["requests"], enrichers=enrichers)
        done = finish_tree(
            tree=tree,
            plan=plan,
            drafts=shape["drafts"],
            keys=shape["keys"],
            answers=summaries["answers"],
            toc=toc["stats"],
            summaries=summaries["stats"],
            shape=shape["stats"],
        )
        START >> toc_plan >> toc >> shape >> summaries >> done >> END

    return tree_index


def no_tree():
    """No tree index: the collection has no ``tree`` spec."""

    @graph
    def tree_index(tree, plan, enrichers):
        none = no_tree_nodes()
        START >> none >> END

    return tree_index
