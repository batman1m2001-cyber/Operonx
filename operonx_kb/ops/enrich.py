"""Enrichment ops: the logic of :mod:`operonx_kb.graphs.enrich` (PLAN §9).

Every model call of ingest goes through one stage shape: :func:`lookup_answers`
splits the stage's requests into cached answers and misses,
:func:`each_request` yields the misses to an ``LLMOp``, :func:`keep_answer`
pairs each answer with its request, :func:`store_answers` caches them and
:func:`stage_answers` hands every answer on. A request is ``{"key",
"messages"}`` (:mod:`operonx_kb.enrich.base`); its key and the stage's
fingerprint are the cache key.

A failed or unparsable answer fails the stage (PLAN E4): the answers that came
back are cached, the version is not committed, and the next ingest asks only
for what is missing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from operonx import op
from operonx.core.loggings import LOGGER

from operonx_kb.enrich import contextual as ctx
from operonx_kb.enrich import tree as trees
from operonx_kb.enrich.base import enricher_fingerprint
from operonx_kb.errors import EnrichmentError
from operonx_kb.model.collection import CollectionSpec
from operonx_kb.model.ids import combine_fingerprints, node_id, sha256_text
from operonx_kb.model.tree import TreeNode
from operonx_kb.ops._resources import catalog_of, llm_fingerprint
from operonx_kb.pipeline import tree_from_dict
from operonx_kb.stores.catalog.base import CachedAnswer
from operonx_kb.text.tokenize import RegexTokenizer

__all__ = [
    "stage_fingerprints",
    "pipeline_enrichers",
    "lookup_answers",
    "each_request",
    "keep_answer",
    "store_answers",
    "stage_answers",
    "apply_contexts",
    "keep_chunks",
    "plan_toc",
    "plan_summaries",
    "finish_tree",
    "no_tree_nodes",
]

_TOKENIZER = RegexTokenizer()


def stage_fingerprints(spec: CollectionSpec) -> Dict[str, str]:
    """The cache fingerprint of each enabled stage: its prompt version, model, and
    the settings that shape an answer but are not in the request's messages."""
    out: Dict[str, str] = {}
    if spec.contextual is not None:
        c = spec.contextual
        out["contextual"] = enricher_fingerprint(
            "contextual", ctx.PROMPT_VERSION, llm_fingerprint(c.llm), {"max_tokens": c.max_tokens}
        )
    if spec.tree is not None:
        t = spec.tree
        llm = llm_fingerprint(t.llm)
        out["summary"] = enricher_fingerprint(
            "summary", trees.SUMMARY_VERSION, llm, {"max_tokens": t.max_tokens}
        )
        out["toc"] = enricher_fingerprint("toc", trees.TOC_VERSION, llm)
    return out


def pipeline_enrichers(spec: CollectionSpec, stages: Dict[str, str]) -> Dict[str, str]:
    """What enrichment adds to ``pipeline_fp`` (PLAN E8): every setting that changes
    a version's chunks or tree. Query-time settings (the navigator, beam, docs) are
    not part of it."""
    out: Dict[str, str] = {}
    if spec.contextual is not None:
        out["contextual"] = combine_fingerprints(
            stage=stages["contextual"], window=str(spec.contextual.window_tokens)
        )
    if spec.tree is not None:
        t = spec.tree
        out["tree"] = combine_fingerprints(
            summary=stages["summary"], toc=stages["toc"], input=str(t.summary_input_tokens),
            toc_min=str(t.toc_min_tokens), toc_window=str(t.toc_window_tokens),
        )  # fmt: skip
    return out


# -- the stage ---------------------------------------------------------------------


@op(bound="cpu", exclude={"trace": ["requests", "misses"]}, show_keys="count")
def lookup_answers(requests: list, enrichers: dict, kind: str, catalog: str) -> dict:
    """Split ``requests`` into answers the cache holds and misses (one per key)."""
    fp = enrichers[kind]
    unique = list({r["key"]: r for r in requests}.values())
    cached = catalog_of(catalog).get_enrichments(fp, [r["key"] for r in unique])
    misses = [r for r in unique if r["key"] not in cached]
    found = {k: a.value for k, a in cached.items()}
    return {"misses": misses, "found": found, "count": len(misses), "cached": len(cached)}


@op(exclude={"trace": ["messages"]}, show_keys="key")
def each_request(requests: list):
    """Yield each request to the model."""
    for r in requests:
        yield {"key": r["key"], "messages": r["messages"]}


@op(show_keys="key")
def keep_answer(
    key: str,
    value: Any = None,
    usage: Optional[dict] = None,
    cost_usd: Optional[float] = None,
    model: Optional[str] = None,
    error: Optional[str] = None,
) -> dict:
    """One answer with its request's key, tokens and cost.

    Raises:
        EnrichmentError: The model's answer did not parse, or is empty.
    """
    if error is not None:
        raise EnrichmentError(f"the model's answer to request {key[:12]} did not parse: {error}")
    if value is None or (isinstance(value, str) and not value.strip()):
        raise EnrichmentError(f"the model answered request {key[:12]} with nothing")
    usage = usage or {}
    return {
        "answer": {
            "key": key, "value": value, "model": model, "cost_usd": cost_usd,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "cached_tokens": int(usage.get("cached_tokens") or 0),
        }
    }  # fmt: skip


def _usage(answers: List[dict]) -> Dict[str, Any]:
    costs = [a["cost_usd"] for a in answers]
    return {
        "calls": len(answers),
        "prompt_tokens": sum(a["prompt_tokens"] for a in answers),
        "completion_tokens": sum(a["completion_tokens"] for a in answers),
        "cached_tokens": sum(a["cached_tokens"] for a in answers),
        "cost_usd": sum(costs) if answers and None not in costs else None,
    }


@op(bound="cpu", exclude={"trace": ["answers"]}, show_keys="usage")
def store_answers(answers: list, enrichers: dict, kind: str, catalog: str) -> dict:
    """Cache the stage's fresh answers with what they cost."""
    rows = {
        a["key"]: CachedAnswer(
            value=a["value"],
            model=a["model"],
            prompt_tokens=a["prompt_tokens"],
            completion_tokens=a["completion_tokens"],
            cached_tokens=a["cached_tokens"],
            cost_usd=a["cost_usd"],
        )  # fmt: skip
        for a in answers
    }
    catalog_of(catalog).put_enrichments(enrichers[kind], kind, rows)
    return {"fresh": {k: r.value for k, r in rows.items()}, "usage": _usage(answers)}


@op(exclude={"trace": ["found", "fresh", "misses", "answers"]}, show_keys="stats")
def stage_answers(
    kind: str,
    found: dict,
    misses: list,
    cached: int = 0,
    fresh: Optional[dict] = None,
    usage: Optional[dict] = None,
) -> dict:
    """Every answer of the stage, cached or fresh.

    Raises:
        EnrichmentError: A miss got no answer (its model call failed; the failure
            is in the run's ``$errors``). The answers that came back are cached.
    """
    fresh = fresh or {}
    lost = [r["key"] for r in misses if r["key"] not in fresh]
    if lost:
        raise EnrichmentError(
            f"{len(lost)} of {len(misses)} {kind} requests got no answer, so the version is not "
            "committed; the answers that came back are cached and the next ingest asks only "
            "for the rest (see this run's $errors for the model's failures)",
            {"kind": kind, "missing": [k[:12] for k in lost[:5]]},
        )
    usage = usage or {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cached_tokens": 0, "cost_usd": None}  # fmt: skip
    return {"answers": {**found, **fresh}, "stats": {"kind": kind, "cached": cached, **usage}}


# -- contextual --------------------------------------------------------------------------


@op(bound="cpu", exclude={"trace": ["fresh", "drafted", "keys", "answers"]}, show_keys="stats")
def apply_contexts(fresh: list, drafted: list, keys: dict, answers: dict, stats: dict) -> dict:
    """Prepend each new chunk's context to its embed text (PLAN E2).

    Args:
        fresh: The version's new chunks (``chunk_version``'s ``todo``).
        drafted: All its chunks, as chunked.
        keys: A new chunk's id → its context request's key.

    Reused chunks are not touched: their catalog rows already hold their
    contextualized embed text.
    """
    done: Dict[str, Dict[str, Any]] = {}
    for c in fresh:
        text = ctx.with_context(answers[keys[c["id"]]], c["embed_text"])
        done[c["id"]] = {**c, "embed_text": text, "embed_text_sha": sha256_text(text)}
    return {
        "todo": [done[c["id"]] for c in fresh],
        "chunks": [done.get(c["id"], c) for c in drafted],
        "stats": {**stats, "contextualized": len(done)},
    }


@op(exclude={"trace": ["fresh", "drafted"]}, show_keys="stats")
def keep_chunks(fresh: list, drafted: list) -> dict:
    """The chunks as chunked, for a collection without contextual enrichment."""
    return {"todo": fresh, "chunks": drafted, "stats": {}}


# -- the tree ------------------------------------------------------------------------------


def _title(plan: dict) -> str:
    return plan.get("title") or plan.get("name") or plan["key"]


@op(bound="cpu", exclude={"trace": ["tree", "requests", "blocks"]}, show_keys="stats")
def plan_toc(tree: dict, plan: dict) -> dict:
    """Table-of-contents requests for a version without headings (PLAN E5); none otherwise."""
    spec = CollectionSpec.model_validate(plan["spec"]).tree
    vt = tree_from_dict(tree)
    if not trees.needs_toc(vt, spec.toc_min_tokens, _TOKENIZER):
        return {"requests": [], "ranges": [], "blocks": [], "stats": {"toc": False}}
    blocks = trees.toc_blocks(vt)
    requests, ranges = trees.toc_requests(
        vt, _title(plan), blocks, spec.toc_window_tokens, _TOKENIZER
    )
    return {"requests": requests, "ranges": [list(r) for r in ranges],
            "blocks": [list(b) for b in blocks], "stats": {"toc": True, "windows": len(ranges),
                                                          "blocks": len(blocks)}}  # fmt: skip


def _draft_dict(d: trees.NodeDraft) -> Dict[str, Any]:
    return {"path": d.path, "depth": d.depth, "title": d.title, "span": list(d.span), "source": d.source}  # fmt: skip


def _draft(d: Dict[str, Any]) -> trees.NodeDraft:
    return trees.NodeDraft(path=d["path"], depth=d["depth"], title=d["title"], span=tuple(d["span"]), source=d["source"])  # fmt: skip


@op(
    bound="cpu",
    exclude={"trace": ["tree", "blocks", "toc", "drafts", "requests", "keys"]},
    show_keys="stats",
)
def plan_summaries(
    tree: dict, plan: dict, blocks: list, ranges: list, toc: dict, requests: list
) -> dict:
    """The version's nodes (headings, or the synthesized table of contents) and a
    summary request for each (PLAN E5, E6)."""
    spec = CollectionSpec.model_validate(plan["spec"]).tree
    vt = tree_from_dict(tree)
    dropped = 0
    if ranges:
        answers = [toc[r["key"]] for r in requests]
        drafts, dropped = trees.toc_nodes(
            vt, _title(plan), [tuple(b) for b in blocks], [tuple(r) for r in ranges], answers
        )
        if dropped:
            LOGGER.warning(
                "[kb] %s: %d table-of-contents entries named no block of their window and were dropped",
                plan["key"], dropped,
            )  # fmt: skip
    else:
        drafts = trees.heading_nodes(vt, _title(plan))
    reqs = [
        trees.summary_request(vt, d, drafts, spec.summary_input_tokens, _TOKENIZER) for d in drafts
    ]
    return {
        "drafts": [_draft_dict(d) for d in drafts],
        "keys": {d.path: r["key"] for d, r in zip(drafts, reqs)},
        "requests": reqs,
        "stats": {
            "nodes": len(drafts),
            "source": "toc" if ranges else ("heading" if len(drafts) > 1 else "document"),
            "toc_dropped": dropped,
        },
    }


@op(
    bound="cpu",
    exclude={"trace": ["tree", "drafts", "keys", "answers", "nodes"]},
    show_keys="stats",
)
def finish_tree(
    tree: dict, plan: dict, drafts: list, keys: dict, answers: dict, toc: dict, summaries: dict,
    shape: dict,
) -> dict:  # fmt: skip
    """The version's tree nodes, with ids, pages and summaries."""
    vt = tree_from_dict(tree)
    leaves = [e for e in vt.elements if e.span is not None and e.regions]
    nodes: List[Dict[str, Any]] = []
    ids: Dict[str, str] = {}
    for d in (_draft(x) for x in drafts):
        ids[d.path] = node_id(plan["version_id"], d.path)
        pages = sorted({r.page_no for e in leaves if e.span[0] < d.span[1] and d.span[0] < e.span[1]
                        for r in e.regions})  # fmt: skip
        node = TreeNode(
            id=ids[d.path], version_id=plan["version_id"], path=d.path,
            parent_id=ids[d.parent_path] if d.parent_path else None,
            ordinal=int(d.path.rsplit(".", 1)[-1]) if "." in d.path else 0, depth=d.depth,
            title=d.title, span=d.span, pages=pages, source=d.source,
            summary=str(answers[keys[d.path]]).strip(), summary_sha=keys[d.path],
        )  # fmt: skip
        nodes.append(node.model_dump(mode="json"))
    return {"nodes": nodes, "stats": {**shape, "toc_usage": toc, "summary_usage": summaries}}


@op(show_keys="stats")
def no_tree_nodes() -> dict:
    """No tree index: the collection has no ``tree`` spec."""
    return {"nodes": [], "stats": {}}
