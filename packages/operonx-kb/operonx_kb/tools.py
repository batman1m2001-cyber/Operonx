"""The knowledge base as agent tools (PLAN K7): ``kb_search`` and ``kb_read``.

In process, not over MCP: an agent built with operonx-agents gets a
:class:`~operonx_agents.Toolset` whose tools call :class:`KnowledgeBase`
directly, traced like every other run::

    from operonx_kb.tools import kb_tools

    tools = kb_tools(kb, "handbook", scope=lambda ctx: {"acl_any": ctx.deps.principals})
    agent = Agent(name="hr", model=Model("assistant"), tools=[tools, file_ticket])

**The scope is the caller's, never the model's.** The model passes a query
(and how many hits); which documents it may see is ``scope`` — a fixed
:class:`~operonx_kb.model.filter.KBFilter` (or its dict), or a function of the
run's :class:`~operonx_agents.RunContext` (the signed-in user's principals in
``ctx.deps``). ``kb_read`` checks the same scope before it returns a word, so
an id the model invents or remembers from another run reads nothing it could
not have searched.

Both tools are read-only (they run concurrently and need no approval under
the default policy). A hit carries its ``id`` (the chunk) for ``kb_read`` and
its document, pages and heading, so an answer can cite where it read.
"""

# No `from __future__ import annotations`: the tools' signatures name RunContext, imported
# lazily below, and operonx-agents reads them with get_type_hints.
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

from operonx_kb.errors import MissingExtraError
from operonx_kb.model.filter import KBFilter, document_payload, matches

__all__ = ["kb_tools", "search_passages", "read_passage"]

Scope = Union[
    None, KBFilter, Mapping[str, Any], Callable[[Any], Union[None, KBFilter, Mapping[str, Any]]]
]

#: The most characters of one hit's text a search returns (``kb_read`` has the rest).
HIT_CHARS = 1200


def _filter(scope: Scope, ctx: Any) -> Optional[dict]:
    value = scope(ctx) if callable(scope) else scope
    dumped = KBFilter.of(value).model_dump(mode="json", exclude_defaults=True)
    return dumped or None


def _hit(h: Mapping[str, Any]) -> Dict[str, Any]:
    text = h["text"]
    return {
        "id": h["chunk_id"],
        "document": h.get("title") or h["key"],
        "key": h["key"],
        "section": " > ".join(h.get("heading_path") or []),
        "pages": h.get("pages") or [],
        "text": text if len(text) <= HIT_CHARS else text[: HIT_CHARS - 1] + "…",
    }


async def search_passages(
    kb: Any, collection: str, query: str, *, k: int = 5, max_k: int = 20,
    mode: Optional[str] = None, filter: Optional[dict] = None,
) -> List[Dict[str, Any]]:  # fmt: skip
    """The best passages for ``query`` in ``collection`` under ``filter``: what
    ``kb_search`` returns, here and over MCP (:mod:`operonx_kb.mcp`)."""
    found = await kb.search(collection, query, k=max(1, min(int(k), max_k)), mode=mode,
                            filter=filter)  # fmt: skip
    return [_hit(h) for h in found["hits"]]


def read_passage(
    kb: Any, collection: str, id: str, *, filter: Optional[dict] = None, around: int = 1,
    prefix: str = "kb",
) -> Dict[str, Any]:  # noqa: A002 — the hit's own field name  # fmt: skip
    """A passage by its id with ``around`` chunks either side, if ``filter`` lets the
    caller see its document: what ``kb_read`` returns."""
    spec = kb.collection(collection).spec
    flt = KBFilter.of(filter).checked(spec)
    found = kb.catalog.active_chunks(collection, [id]).get(id)
    if found is None or not matches(flt, collection, document_payload(spec, found.document)):
        return {"error": f"no passage {id!r} in {collection} (use an id from {prefix}_search)"}
    occurrences = kb.catalog.version_chunks(found.occurrence.version_id)
    at = found.occurrence.ordinal
    near = [o for o in occurrences if abs(o.ordinal - at) <= around]
    chunks = kb.catalog.get_chunks([o.chunk_id for o in near])
    return {
        "id": id,
        "document": found.document.title or found.document.key,
        "key": found.document.key,
        "section": " > ".join(found.chunk.heading_path or []),
        "pages": sorted({p for o in near for p in (o.pages or [])}),
        "text": "\n\n".join(chunks[o.chunk_id].text for o in near if o.chunk_id in chunks),
    }


def kb_tools(
    kb: Any,
    collection: str,
    *,
    scope: Scope = None,
    mode: Optional[str] = None,
    k: int = 5,
    max_k: int = 20,
    around: int = 1,
    prefix: str = "kb",
):
    """``<prefix>_search`` and ``<prefix>_read`` over one collection, as a Toolset.

    Args:
        kb: The :class:`~operonx_kb.KnowledgeBase`.
        collection: The collection the tools reach (only this one).
        scope: Which documents the agent may see: a filter, or a function of the
            run's context returning one (``None``: the whole collection).
        mode: The retrieval mode (default: the collection's default; ``"graph"``
            for a collection of linked facts).
        k: Hits a search returns unless the model asks for more …
        max_k: … and at most this many.
        around: Chunks before and after the one ``kb_read`` returns.
        prefix: The tools' name prefix (two collections → two toolsets).

    Raises:
        MissingExtraError: operonx-agents is not installed.
    """
    try:
        from operonx_agents import RunContext, Toolset, tool
    except ImportError as exc:  # pragma: no cover - exercised without the package
        raise MissingExtraError("Agent tools (operonx_kb.tools)", "agents", exc) from exc

    kb.collection(collection)  # an unknown collection fails here, not on the first call

    async def search(ctx: RunContext, query: str, k: int = k) -> List[Dict[str, Any]]:
        return await search_passages(kb, collection, query, k=k, max_k=max_k, mode=mode,
                                     filter=_filter(scope, ctx))  # fmt: skip

    async def read(ctx: RunContext, id: str) -> Dict[str, Any]:  # noqa: A002 — the hit's own field name
        return read_passage(kb, collection, id, filter=_filter(scope, ctx), around=around,
                            prefix=prefix)  # fmt: skip

    search.__doc__ = (
        f"Search the {collection!r} knowledge base. Returns the best passages, each with an id "
        f"(pass it to {prefix}_read for more), its document, section and pages.\n\n"
        "Args:\n    query: What to look for, in plain words.\n    k: How many passages."
    )
    read.__doc__ = (
        f"Read a passage found by {prefix}_search with the text around it.\n\n"
        "Args:\n    id: The passage id from a search result."
    )
    return Toolset([
        tool(search, name=f"{prefix}_search", readonly=True),
        tool(read, name=f"{prefix}_read", readonly=True),
    ])  # fmt: skip
