"""What each admin route answers (track5 §16.2): JSON-ready dicts, no HTTP.

Reads go to the catalog and the blob store, the store of record, through a
:class:`~operonx_kb.kb.KnowledgeBase`; a query runs the KB's own search and
answer graphs, each under a trace id it returns, so Studio can open the run.
Every box is a :class:`~operonx_kb.model.document.Region`'s normalised
top-left ``bbox`` on a 1-based page: the viewer scales it to the page image.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence

from operonx_kb.errors import KBError
from operonx_kb.eval.cases import eval_case
from operonx_kb.kb import MODES, KnowledgeBase, QueryError
from operonx_kb.model.document import Element, Region, VersionChunk
from operonx_kb.model.filter import KBFilter
from operonx_kb.ops._resources import full_key
from operonx_kb.retrieval.citations import answer_sentences
from operonx_kb.text.spans import regions_for_span

__all__ = [
    "API",
    "NotFound",
    "BadRequest",
    "Unavailable",
    "well_known",
    "collections",
    "collection",
    "health",
    "documents",
    "document",
    "version",
    "version_tree",
    "version_text",
    "page",
    "chunk",
    "query",
    "case",
]

#: The contract's name and version. A breaking change is ``operonx-kb/2``.
API = "operonx-kb/1"
#: Documents per page of :func:`documents`, and the most a caller may ask for.
PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
#: Hits per search of :func:`query`, at most.
MAX_K = 50
#: Characters of an element's or chunk's text in a page or list answer.
PREVIEW = 160


class NotFound(KBError):
    """No such collection, document, version, page or chunk."""


class BadRequest(KBError, ValueError):
    """The request is malformed: a missing field, a value of the wrong type."""


class Unavailable(KBError):
    """The admin app was not configured for this (no answer model, no reranker)."""


def _preview(text: str) -> str:
    return text if len(text) <= PREVIEW else text[: PREVIEW - 1] + "…"


def _region(r: Region) -> Dict[str, Any]:
    return {"page_no": r.page_no, "bbox": [round(v, 5) for v in r.bbox]}


# ── collections ──────────────────────────────────────────────────────────


def well_known(kb: KnowledgeBase, *, llm: Optional[str], reranker: Optional[str]) -> Dict[str, Any]:
    """Discovery: the contract, the collections, and what a query may ask for."""
    return {
        "api": API,
        "collections": [c.id for c in kb.catalog.list_collections()],
        "answer": llm is not None,
        "rerank": reranker is not None,
    }


def _modes(kb: KnowledgeBase, collection_id: str) -> List[str]:
    spec = kb.collection(collection_id).spec
    have = {"dense": spec.dense is not None, "lexical": spec.lexical is not None}
    have["hybrid"] = have["dense"] and have["lexical"]
    # Tree search is seeded by the collection's default mode and walks its tree index.
    have["tree"] = spec.tree is not None and (have["dense"] or have["lexical"])
    have["graph"] = spec.graph is not None and (have["dense"] or have["lexical"])
    return [m for m in MODES if have[m]]


def _collection_of(kb: KnowledgeBase, collection_id: str):
    found = kb.catalog.get_collection(collection_id)
    if found is None:
        raise NotFound(f"no collection {collection_id!r}; GET /collections lists them")
    return found


def collection(kb: KnowledgeBase, collection_id: str) -> Dict[str, Any]:
    """A collection: its spec, the retrieval modes it serves, and its counts."""
    found = _collection_of(kb, collection_id)
    docs = kb.catalog.list_documents(collection_id, include_deleted=True)
    live = [d for d in docs if d.deleted_at is None]
    modes = _modes(kb, collection_id)
    return {
        "id": found.id,
        "tags": found.tags,
        "spec": found.spec.model_dump(mode="json"),
        "modes": modes,
        "default_mode": kb.default_mode(collection_id) if modes else None,
        "filterable": found.spec.filterable,
        "documents": len(live),
        "active": sum(1 for d in live if d.active_version_id is not None),
        "deleted": len(docs) - len(live),
        "chunks": len(kb.catalog.active_chunk_ids(collection_id)),
    }


def collections(kb: KnowledgeBase) -> Dict[str, Any]:
    return {"collections": [collection(kb, c.id) for c in kb.catalog.list_collections()]}


def health(kb: KnowledgeBase, collection_id: str) -> Dict[str, Any]:
    """:meth:`KnowledgeBase.verify` of the collection: counts, and every problem found."""
    _collection_of(kb, collection_id)
    report = kb.verify(collection_id)
    return {
        "ok": report.ok,
        "documents": report.documents,
        "elements": report.elements,
        "chunks": report.chunks,
        "index_entries": report.index_entries,
        "lexical_entries": report.lexical_entries,
        "problems": report.problems,
    }


# ── documents and versions ───────────────────────────────────────────────

#: A document's state as the documents table shows it.
STATUSES = ("active", "pending", "deleted")


def _status(doc) -> str:
    if doc.deleted_at is not None:
        return "deleted"
    return "active" if doc.active_version_id is not None else "pending"


def _document_row(kb: KnowledgeBase, doc) -> Dict[str, Any]:
    active = kb.catalog.get_version(doc.active_version_id) if doc.active_version_id else None
    return {
        "id": doc.id,
        "key": doc.key,
        "title": doc.title,
        "mime": doc.mime,
        "tags": doc.tags,
        "status": _status(doc),
        "active_version_id": doc.active_version_id,
        "created_at": doc.created_at.isoformat(),
        "deleted_at": doc.deleted_at.isoformat() if doc.deleted_at else None,
        "stats": active.stats if active else {},
    }


def documents(
    kb: KnowledgeBase,
    collection_id: str,
    *,
    q: str = "",
    status: str = "",
    page: int = 1,
    size: int = PAGE_SIZE,
) -> Dict[str, Any]:
    """A page of a collection's documents, newest first.

    Args:
        q: Keeps documents whose key or title contains it (case-insensitive).
        status: ``active``, ``pending`` or ``deleted`` (default: all but deleted).
        page: 1-based.
    """
    _collection_of(kb, collection_id)
    if status and status not in STATUSES:
        raise BadRequest(f"status is one of {list(STATUSES)}, not {status!r}")
    if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
        raise BadRequest(f"page starts at 1 and size is 1 to {MAX_PAGE_SIZE}")
    docs = kb.catalog.list_documents(collection_id, include_deleted=status == "deleted")
    needle = q.casefold().strip()
    picked = [
        d
        for d in docs
        if (_status(d) == status if status else _status(d) != "deleted")
        and (not needle or needle in d.key.casefold() or needle in (d.title or "").casefold())
    ]
    picked.sort(key=lambda d: (d.created_at, d.key), reverse=True)
    shown = picked[(page - 1) * size : page * size]
    return {
        "documents": [_document_row(kb, d) for d in shown],
        "total": len(picked),
        "page": page,
        "size": size,
    }


def _document_of(kb: KnowledgeBase, document_id: str):
    doc = kb.catalog.get_document(document_id)
    if doc is None:
        raise NotFound(f"no document {document_id!r}")
    return doc


def document(kb: KnowledgeBase, document_id: str) -> Dict[str, Any]:
    """A document, its versions (newest first) and its ingest log (errors included)."""
    doc = _document_of(kb, document_id)
    versions = sorted(kb.catalog.list_versions(doc.id), key=lambda v: v.ordinal, reverse=True)
    log = kb.catalog.ingest_log(doc.collection_id, doc.key)
    return {
        **_document_row(kb, doc),
        "collection_id": doc.collection_id,
        "acl": doc.acl,
        "metadata": doc.metadata,
        "versions": [v.model_dump(mode="json") for v in versions],
        "log": log[-20:],
    }


def _version_of(kb: KnowledgeBase, version_id: str):
    found = kb.catalog.get_version(version_id)
    if found is None:
        raise NotFound(f"no version {version_id!r}")
    return found


def _tree(kb: KnowledgeBase, version_id: str) -> List[Element]:
    return kb.catalog.elements(version_id, kb.canonical_text(version_id))


def _chunk_regions(tree: Sequence[Element], occurrence: VersionChunk) -> List[Region]:
    """Where a chunk is drawn: the regions of its spans, in span order, each once."""
    seen, out = set(), []
    for span in occurrence.spans:
        for r in regions_for_span(tree, tuple(span)):
            key = (r.page_no, r.bbox)
            if key not in seen:
                seen.add(key)
                out.append(r)
    return out


def version(kb: KnowledgeBase, version_id: str) -> Dict[str, Any]:
    """A version: its document, pages and chunk occurrences (with their regions)."""
    ver = _version_of(kb, version_id)
    doc = _document_of(kb, ver.document_id)
    occurrences = kb.catalog.version_chunks(version_id)
    chunks = kb.catalog.get_chunks([o.chunk_id for o in occurrences])
    tree = _tree(kb, version_id)
    return {
        **ver.model_dump(mode="json"),
        "document": _document_row(kb, doc),
        "active": doc.active_version_id == ver.id,
        "pages": [
            {"page_no": p.page_no, "width": p.width, "height": p.height, "unit": p.unit}
            for p in kb.catalog.pages(version_id)
        ],
        "chunks": [
            {
                "chunk_id": o.chunk_id,
                "ordinal": o.ordinal,
                "kind": chunks[o.chunk_id].kind,
                "token_count": chunks[o.chunk_id].token_count,
                "heading_path": chunks[o.chunk_id].heading_path,
                "preview": _preview(chunks[o.chunk_id].text),
                "spans": [list(s) for s in o.spans],
                "pages": o.pages,
                "regions": [_region(r) for r in _chunk_regions(tree, o)],
            }
            for o in occurrences
        ],
    }


def version_tree(kb: KnowledgeBase, version_id: str) -> Dict[str, Any]:
    """The element tree, in document order."""
    _version_of(kb, version_id)
    return {
        "version_id": version_id,
        "elements": [
            {
                "id": e.id,
                "parent_id": e.parent_id,
                "path": e.path,
                "depth": e.depth,
                "kind": e.kind,
                "layer": e.layer,
                "level": e.level,
                "span": list(e.span) if e.span else None,
                "text": e.text,
                "regions": [_region(r) for r in e.regions],
            }
            for e in _tree(kb, version_id)
        ],
    }


def version_text(kb: KnowledgeBase, version_id: str) -> Dict[str, Any]:
    """The canonical text, which every span points into."""
    _version_of(kb, version_id)
    return {"version_id": version_id, "text": kb.canonical_text(version_id)}


def page(kb: KnowledgeBase, version_id: str, page_no: int) -> Dict[str, Any]:
    """One page: its size, the elements drawn on it, and the chunks with boxes on it."""
    _version_of(kb, version_id)
    pages = {p.page_no: p for p in kb.catalog.pages(version_id)}
    if page_no not in pages:
        raise NotFound(
            f"version {version_id} has no page {page_no}"
            + (f"; its pages are 1 to {len(pages)}" if pages else "; it has no pages")
        )
    p = pages[page_no]
    tree = _tree(kb, version_id)
    elements = [
        {"id": e.id, "kind": e.kind, "layer": e.layer, "span": list(e.span) if e.span else None,
         "preview": _preview(e.text), "boxes": [_region(r)["bbox"] for r in e.regions if r.page_no == page_no]}
        for e in tree
        if any(r.page_no == page_no for r in e.regions)
    ]  # fmt: skip
    chunks = []
    for o in kb.catalog.version_chunks(version_id):
        if page_no not in o.pages:
            continue
        boxes = [_region(r)["bbox"] for r in _chunk_regions(tree, o) if r.page_no == page_no]
        if boxes:
            chunks.append({"chunk_id": o.chunk_id, "ordinal": o.ordinal, "boxes": boxes})
    return {
        "version_id": version_id,
        "page_no": page_no,
        "pages": len(pages),
        "width": p.width,
        "height": p.height,
        "unit": p.unit,
        "elements": elements,
        "chunks": chunks,
    }


# ── chunks ───────────────────────────────────────────────────────────────


def chunk(kb: KnowledgeBase, chunk_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
    """A chunk as one version holds it (default: its document's active version): text
    and embedded text, where it is on the pages, its neighbours, and which indexes hold it."""
    found = kb.catalog.get_chunks([chunk_id]).get(chunk_id)
    if found is None:
        raise NotFound(f"no chunk {chunk_id!r}")
    doc = _document_of(kb, found.document_id)
    version_id = version_id or doc.active_version_id
    if version_id is None:
        raise NotFound(f"document {doc.key!r} has no active version; name one with ?version=")
    occurrences = kb.catalog.version_chunks(version_id)
    at = next((i for i, o in enumerate(occurrences) if o.chunk_id == chunk_id), None)
    if at is None:
        raise NotFound(f"version {version_id} does not hold chunk {chunk_id}")
    occurrence = occurrences[at]
    spec = kb.collection(doc.collection_id).spec
    indexes = []
    if spec.dense is not None:
        # what the embedder read: the template applied as EmbedChunksOp applies it
        store = full_key(spec.dense.store, "vector_store")
        held = kb.catalog.index_entries(store, spec.dense.collection or "", document_id=doc.id)
        indexes.append({"kind": "dense", "store": store, "collection": spec.dense.collection,
                        "key": held.get(chunk_id), "embedder": spec.dense.embedder,
                        "embedded_as": spec.dense.passage_template.replace("{text}", found.embed_text)})  # fmt: skip
    if spec.lexical is not None:
        store = full_key(spec.lexical.index, "kb_lexical")
        held = kb.catalog.index_entries(store, spec.lexical.collection or "", document_id=doc.id)
        indexes.append({"kind": "lexical", "store": store, "collection": spec.lexical.collection,
                        "key": held.get(chunk_id), "analyzer": spec.lexical.analyzer.model_dump()})  # fmt: skip
    at_text = found.embed_text.find(found.text)
    neighbour = {
        "previous": occurrences[at - 1].chunk_id if at > 0 else None,
        "next": occurrences[at + 1].chunk_id if at + 1 < len(occurrences) else None,
    }
    return {
        **found.model_dump(mode="json"),
        "document": _document_row(kb, doc),
        "version_id": version_id,
        "ordinal": occurrence.ordinal,
        "spans": [list(s) for s in occurrence.spans],
        "element_ids": occurrence.element_ids,
        "pages": occurrence.pages,
        "regions": [_region(r) for r in _chunk_regions(_tree(kb, version_id), occurrence)],
        # the context the embedded text adds before the chunk's own text (its heading path)
        "context_prefix": found.embed_text[:at_text] if at_text > 0 else "",
        "neighbours": neighbour,
        "indexes": indexes,
    }


# ── query ────────────────────────────────────────────────────────────────


def _hit(h: Mapping[str, Any]) -> Dict[str, Any]:
    keys = ("chunk_id", "rank", "score", "scores", "document_id", "key", "title", "version_id",
            "ordinal", "heading_path", "pages", "spans", "text")  # fmt: skip
    return {k: h.get(k) for k in keys}


def _int(body: Mapping[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = body.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BadRequest(f"{name} is a whole number from {low} to {high}, not {value!r}")
    return value


async def query(
    kb: KnowledgeBase,
    collection_id: str,
    body: Mapping[str, Any],
    *,
    llm: Optional[str],
    reranker: Optional[str],
) -> Dict[str, Any]:
    """Run the query of the playground: one search per asked mode, and an answer.

    Body:
        query: The question.
        modes: Retrieval modes to list side by side (default: the collection's default).
        rerank: Also list the default mode's hits reranked (needs a reranker).
        answer: Also answer, from the default mode's search (``mode`` overrides it), with
            the reranker when ``rerank`` is set. Needs an answer model.
        k: Hits per search (default 8).
        filter: A ``KBFilter`` dict.

    Every run gets its own trace id, returned beside its result.

    Raises:
        BadRequest: A malformed body, a mode the collection cannot serve, a bad filter.
        Unavailable: ``answer`` or ``rerank`` asked of an app configured without them.
        QueryError: A run failed; its ``context`` holds the run's ``trace_id``.
    """
    _collection_of(kb, collection_id)
    text = body.get("query")
    if not isinstance(text, str) or not text.strip():
        raise BadRequest("the body needs a non-empty 'query'")
    served = _modes(kb, collection_id)
    if not served:
        raise BadRequest(f"collection {collection_id!r} has no index to search")
    modes = body.get("modes") or [kb.default_mode(collection_id)]
    if not isinstance(modes, list) or any(m not in served for m in modes):
        raise BadRequest(
            f"modes are a list from {served} (what {collection_id!r} serves); got {modes!r}"
        )
    mode = body.get("mode") or kb.default_mode(collection_id)
    if mode not in served:
        raise BadRequest(f"mode is one of {served}, not {mode!r}")
    k = _int(body, "k", 8, 1, MAX_K)
    rerank, answer = bool(body.get("rerank")), bool(body.get("answer"))
    if rerank and reranker is None:
        raise Unavailable("this admin app has no reranker; pass reranker= to kb_admin_app()")
    if answer and llm is None:
        raise Unavailable("this admin app has no answer model; pass llm= to kb_admin_app()")
    try:
        asked = KBFilter.of(body.get("filter"))
    except ValueError as exc:
        raise BadRequest(f"filter is not a KBFilter: {exc}") from exc
    asked.checked(kb.collection(collection_id).spec)  # FilterError (400) before any run
    flt = asked.model_dump(mode="json", exclude_defaults=True) or None

    async def search(m: str, ranked: bool) -> Dict[str, Any]:
        trace_id = uuid.uuid4().hex
        try:
            got = await kb.search(collection_id, text, filter=flt, k=k, mode=m,
                                  reranker=reranker if ranked else None, trace_id=trace_id)  # fmt: skip
        except QueryError as exc:
            exc.context["trace_id"] = trace_id  # the failed run, for "open trace"
            raise
        return {"mode": m, "reranked": ranked, "hits": [_hit(h) for h in got["hits"]],
                "stats": got["stats"], "trace_id": trace_id}  # fmt: skip

    async def ask() -> Dict[str, Any]:
        trace_id = uuid.uuid4().hex
        try:
            got = await kb.ask(collection_id, text, llm, filter=flt, k=k, mode=mode,
                               reranker=reranker if rerank else None, trace_id=trace_id)  # fmt: skip
        except QueryError as exc:
            exc.context["trace_id"] = trace_id  # the failed run, for "open trace"
            raise
        sentences = [list(s) for s in answer_sentences(got["text"])]
        return {
            **got,
            "mode": mode,
            "reranked": rerank,
            "sentences": sentences,
            "trace_id": trace_id,
        }

    runs = [search(m, False) for m in modes]
    if rerank:
        runs.append(search(mode, True))
    if answer:
        runs.append(ask())
    done = await asyncio.gather(*runs)
    return {
        "query": text,
        "collection": collection_id,
        "k": k,
        "filter": flt,
        "searches": [d for d in done if "hits" in d],
        "answer": done[-1] if answer else None,
    }


def case(kb: KnowledgeBase, collection_id: str, body: Mapping[str, Any]) -> Dict[str, Any]:
    """An eval case (``datasets/*.jsonl``, :mod:`operonx_kb.eval.cases`) from a query and
    the citations a reviewer kept: Studio saves it through its dataset rows API.

    Body:
        query: The question.
        citations: ``[{"key", "quote", "pages"?}]``, e.g. an answer's verified citations.
        answer: The expected answer, when the reviewer vouches for it.
        k: The case's ``k`` (optional).
        tags: Extra tags.
    """
    _collection_of(kb, collection_id)
    cites = body.get("citations")
    if not isinstance(cites, list) or not cites:
        raise BadRequest("the body needs 'citations': [{key, quote}], the text the answer rests on")
    relevant = []
    for c in cites:
        if (
            not isinstance(c, Mapping)
            or not isinstance(c.get("key"), str)
            or not isinstance(c.get("quote"), str)
        ):
            raise BadRequest(f"a citation is {{key, quote, pages?}}, not {c!r}")
        label = {"doc_key": c["key"], "quote": c["quote"]}
        if c.get("pages"):
            label["page"] = int(c["pages"][0])
        relevant.append(label)
    k = body.get("k")
    return {
        "row": eval_case(
            kb, collection_id, str(body.get("query") or ""), relevant,
            answer=body.get("answer") or None,
            k=_int(body, "k", 0, 1, MAX_K) if k is not None else None,
            tags=[str(t) for t in body.get("tags") or []],
        )
    }  # fmt: skip
