"""The knowledge base over MCP: ``kb_search`` and ``kb_read`` for a client outside our
code (Claude Desktop, an IDE, a program in another language).

Our own flows and agents use the KB in process (:mod:`operonx_kb.tools`); this serves
the same two tools, with the same results, over the Model Context Protocol::

    operonx-kb --resources resources.yaml mcp handbook --filter '{"acl_any": ["team:hr"]}'

**The scope is the server's, never the client's.** An MCP client is a model's tool
caller: it sends a query, not who it is. What it may see is fixed when the server
starts (``scope``: a :class:`~operonx_kb.model.filter.KBFilter` or its dict), and
``kb_read`` checks it again, so an id from elsewhere reads nothing the server's
scope hides. Run one server per audience.

Needs the ``mcp`` extra.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Union

from operonx_kb.errors import MissingExtraError
from operonx_kb.model.filter import KBFilter
from operonx_kb.tools import read_passage, search_passages

__all__ = ["mcp_server"]


def mcp_server(
    kb: Any,
    collection: str,
    *,
    scope: Union[None, KBFilter, Mapping[str, Any]] = None,
    mode: Optional[str] = None,
    k: int = 5,
    max_k: int = 20,
    around: int = 1,
    prefix: str = "kb",
    name: Optional[str] = None,
):
    """An MCP server (``mcp.server.mcpserver.MCPServer``) with ``<prefix>_search`` and
    ``<prefix>_read`` over one collection; ``.run()`` serves it on stdio.

    Args:
        kb: The :class:`~operonx_kb.KnowledgeBase`.
        collection: The collection the tools reach (only this one).
        scope: Which documents a client may see (``None``: the whole collection).
        mode: The retrieval mode (default: the collection's).
        k: Hits a search returns unless the client asks for more …
        max_k: … and at most this many.
        around: Chunks before and after the one ``kb_read`` returns.
        prefix: The tools' name prefix.
        name: The server's name (default ``operonx-kb-<collection>``).

    Raises:
        MissingExtraError: the ``mcp`` package is not installed.
    """
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - exercised without the package
        raise MissingExtraError("The KB over MCP (operonx_kb.mcp)", "mcp", exc) from exc

    kb.collection(collection)  # an unknown collection fails here, not on the first call
    flt = KBFilter.of(scope).model_dump(mode="json", exclude_defaults=True) or None
    server = MCPServer(name=name or f"operonx-kb-{collection}")
    read_only = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

    @server.tool(
        name=f"{prefix}_search",
        description=(
            f"Search the {collection!r} knowledge base. Returns the best passages, each with "
            f"an id (pass it to {prefix}_read for more), its document, section and pages."
        ),
        annotations=read_only,
    )
    async def search(query: str, k: int = k) -> List[Dict[str, Any]]:
        """query: what to look for, in plain words. k: how many passages."""
        return await search_passages(kb, collection, query, k=k, max_k=max_k, mode=mode,
                                     filter=flt)  # fmt: skip

    @server.tool(
        name=f"{prefix}_read",
        description=f"Read a passage found by {prefix}_search with the text around it.",
        annotations=read_only,
    )
    async def read(id: str) -> Dict[str, Any]:  # noqa: A002 — the hit's own field name
        """id: the passage id from a search result."""
        return read_passage(kb, collection, id, filter=flt, around=around, prefix=prefix)

    return server
