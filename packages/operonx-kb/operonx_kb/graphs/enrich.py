"""Enrichment graphs (wiring only; the logic is in :mod:`operonx_kb.ops.enrich`).

One stage shape for every model call of ingest (PLAN E1)::

    lookup_answers ─► if misses ─► each_request ─► LLMOp (parallel) ─► keep_answer ─► store_answers ─┐
                      else ───────────────────────────────────────────────────────────► stage_answers ◄┘

A stage whose requests are all cached runs no model op at all, so a re-ingest
of unchanged content shows no ``LLMOp`` span in its trace. A stage the
collection does not enable has no requests, so it is the same: no model op runs.

Every graph is defined at module level (operonx guide 05); the model is an input
(``llm``, the ``llm:`` resource's name), read from the collection by
:func:`~operonx_kb.ops.settings.index_settings`:

- :func:`text_stage` / :func:`toc_stage`: ``(requests, enrichers, …) → (answers, stats)``.
- :func:`contextualize`: ``(fresh, drafted, requests, keys, enrichers, llm, max_tokens) →
  (todo, chunks, stats)``. The inputs are not named like the outputs: a graph's input
  and output of one name are one cell, and a stage that failed would hand its inputs
  on as its outputs.
- :func:`tree_index`: ``(tree, plan, enrichers, llm, max_tokens) → (nodes, stats)``.

Model calls run :data:`PARALLEL` at a time per stage; to limit calls to a model, give
its ``llm:`` resource a ``rate_limit:`` in ``resources.yaml``.
"""

from __future__ import annotations

from operonx import END, START, graph
from operonx.core.ops import if_
from operonx.providers.ops import LLMOp

from operonx_kb.ops.enrich import (
    apply_contexts,
    each_request,
    finish_tree,
    keep_answer,
    lookup_answers,
    plan_summaries,
    plan_toc,
    stage_answers,
    store_answers,
)

__all__ = ["text_stage", "toc_stage", "contextualize", "tree_index", "PARALLEL", "TOC_MAX_TOKENS"]

#: Model calls in flight at once, per stage.
PARALLEL = 8
#: The answer limit of a table-of-contents call (a few dozen titles).
TOC_MAX_TOKENS = 2000


@graph
def text_stage(requests, enrichers, kind, llm, max_tokens, catalog):
    """A cached model stage whose answer is the model's text (``contextual``, ``summary``)."""
    look = lookup_answers(requests=requests, enrichers=enrichers, kind=kind, catalog=catalog)
    each = each_request(requests=look["misses"])
    call = LLMOp.of(resource=llm, messages=each["messages"].parallel(max=PARALLEL),
                    max_tokens=max_tokens)  # fmt: skip
    kept = keep_answer(key=each["key"], value=call["content"], usage=call["usage"],
                       cost_usd=call["cost_usd"], model=call["model_used"],
                       error=call.get("error"))  # fmt: skip
    saved = store_answers(answers=kept["answer"].collect(), enrichers=enrichers, kind=kind,
                          catalog=catalog)  # fmt: skip
    done = stage_answers(kind=kind, found=look["found"], misses=look["misses"],
                         cached=look["cached"], fresh=saved["fresh"], usage=saved["usage"])  # fmt: skip
    START >> look >> if_(look["count"] > 0, each).else_(done)
    each >> call >> kept >> saved >> done
    done >> END


@graph
def toc_stage(requests, enrichers, llm, catalog):
    """The table-of-contents stage: a JSON answer, its ``sections`` field."""
    look = lookup_answers(requests=requests, enrichers=enrichers, kind="toc", catalog=catalog)
    each = each_request(requests=look["misses"])
    call = LLMOp.of(resource=llm, messages=each["messages"].parallel(max=PARALLEL),
                    max_tokens=TOC_MAX_TOKENS, fields=["sections: list"], parser="json",
                    max_retries=1)  # fmt: skip
    kept = keep_answer(key=each["key"], value=call["sections"], usage=call["usage"],
                       cost_usd=call["cost_usd"], model=call["model_used"],
                       error=call.get("error"))  # fmt: skip
    saved = store_answers(answers=kept["answer"].collect(), enrichers=enrichers, kind="toc",
                          catalog=catalog)  # fmt: skip
    done = stage_answers(kind="toc", found=look["found"], misses=look["misses"],
                         cached=look["cached"], fresh=saved["fresh"], usage=saved["usage"])  # fmt: skip
    START >> look >> if_(look["count"] > 0, each).else_(done)
    each >> call >> kept >> saved >> done
    done >> END


@graph
def contextualize(fresh, drafted, requests, keys, enrichers, llm, max_tokens, catalog):
    """Contextual enrichment of a version's new chunks (PLAN E2); the chunks as chunked
    when the collection has none (no requests, no keys)."""
    answers = text_stage(requests=requests, enrichers=enrichers, kind="contextual", llm=llm,
                         max_tokens=max_tokens, catalog=catalog)  # fmt: skip
    applied = apply_contexts(fresh=fresh, drafted=drafted, keys=keys,
                             answers=answers["answers"], stats=answers["stats"])  # fmt: skip
    START >> answers >> applied >> END


@graph
def tree_index(tree, plan, enrichers, llm, max_tokens, catalog):
    """A version's tree index: nodes from headings or a synthesized table of contents,
    each with a summary (PLAN E5, E6); no nodes when the collection has no tree index."""
    toc_plan = plan_toc(tree=tree, plan=plan)
    toc = toc_stage(requests=toc_plan["requests"], enrichers=enrichers, llm=llm, catalog=catalog)
    shape = plan_summaries(tree=tree, plan=plan, blocks=toc_plan["blocks"],
                           ranges=toc_plan["ranges"], toc=toc["answers"],
                           requests=toc_plan["requests"])  # fmt: skip
    summaries = text_stage(requests=shape["requests"], enrichers=enrichers, kind="summary",
                           llm=llm, max_tokens=max_tokens, catalog=catalog)  # fmt: skip
    done = finish_tree(tree=tree, plan=plan, drafts=shape["drafts"], keys=shape["keys"],
                       answers=summaries["answers"], toc=toc["stats"],
                       summaries=summaries["stats"], shape=shape["stats"])  # fmt: skip
    START >> toc_plan >> toc >> shape >> summaries >> done >> END
