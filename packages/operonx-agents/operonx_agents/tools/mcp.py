"""MCP — tools that live in another process, as a :class:`Toolset`.

Ported from ``operonx.agents.mcp`` (its tests too), with two changes: the
tools land in a toolset one agent owns instead of the process-wide
registry, and a server is reached over **stdio or streamable HTTP**, the
protocol version negotiated by the SDK (the stateless 2026-07-28 revision
when the server speaks it, the initialize handshake when it does not)::

    async with await MCPToolset.connect(MCPServer("fs", command="npx", args=[...])) as fs:
        agent = Agent(name="ops", model=..., tools=[lookup_order, fs])

    crm = await MCPToolset.connect(MCPServer("crm", url="https://crm.internal/mcp"),
                                   allow=["get_customer"])

Three things about MCP make it different from a local tool, and each shows
up in the API here.

**Discovery is asynchronous.** A server's tool list exists only after a
connection, so a toolset is built by an ``await``, before the agent.

**The server is someone else's code.** Its tool descriptions go straight
into the model's context, which makes them an injection surface, so they
are truncated; names are namespaced (``<server>__<tool>``) so a server
cannot shadow a local tool; and a tool the server does not describe as
read-only is destructive, so the default policy asks a human before it
runs (absent hints mean unknown, and unknown is gated).

**A failed call must look failed.** MCP reports errors in-band, as
``isError`` on an otherwise ordinary result. The flag is checked and
raises, which dispatch turns into a tool message the model can recover
from — reading the content would hand the model an error formatted as an
answer.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import warnings
import weakref
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from operonx.core import LOGGER

from operonx_agents.errors import AgentsError
from operonx_agents.tools.tool import Tool, tool
from operonx_agents.tools.toolset import Toolset

__all__ = ["MCPClient", "MCPError", "MCPServer", "MCPToolset", "MAX_DESCRIPTION_CHARS"]

#: Tool names must be a stable handle for the model, and providers reject
#: anything exotic. Server names are user-supplied, so they are sanitised.
_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")

#: A description is context the model pays for on every turn, and it comes
#: from a third party. Long ones are truncated rather than trusted.
MAX_DESCRIPTION_CHARS = 1024


class MCPError(AgentsError):
    """A protocol-level failure: connect, handshake, or a tool call."""


@dataclass
class MCPServer:
    """How to reach one MCP server: a ``command`` to run (stdio) or a
    ``url`` (streamable HTTP).

    Args:
        name: Short label. Becomes the tool-name prefix, so it must be
            distinctive — ``fs`` gives ``fs__read_file``.
        command: Executable for a stdio server, e.g. ``"npx"``.
        args: Arguments for ``command``.
        env: Extra environment for the child process, passed as given.
        cwd: Working directory for the child process.
        url: The endpoint of a streamable-HTTP server.
        headers: Sent with every HTTP request (a bearer token, say).
        timeout: Seconds any single tool call may take.
    """

    name: str
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = field(default=None, repr=False)
    timeout: float = 60.0

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise MCPError("MCPServer needs a name — it prefixes every tool it provides")
        has_command = bool(self.command and self.command.strip())
        has_url = bool(self.url and self.url.strip())
        if has_command == has_url:
            raise MCPError(
                f"MCPServer {self.name!r} needs a command to run (stdio) or a url "
                "(streamable HTTP), and not both"
            )
        self.name = _UNSAFE.sub("_", self.name.strip())

    @property
    def where(self) -> str:
        return self.url if self.url else str(self.command)


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first attribute that exists, by any of its spellings.

    The MCP wire format is camelCase (``isError``, ``inputSchema``) and the
    Python SDK exposes snake_case (``is_error``, ``input_schema``). Reading
    only one spelling means every ``getattr`` silently returns its default:
    measured against a live server, ``isError`` was always absent, so a
    failed tool call — content and all — was handed to the model as a
    successful result.
    """
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _flatten(content: Any) -> str:
    """Turn an MCP content list into text the model can read.

    A block it cannot render is described rather than dropped, because a
    silently shortened result is indistinguishable from a complete one.
    """
    if content is None:
        return ""
    parts: List[str] = []
    for block in content if isinstance(content, list) else [content]:
        kind = getattr(block, "type", None)
        if kind == "text":
            parts.append(getattr(block, "text", "") or "")
        elif kind == "image":
            mime = _attr(block, "mime_type", "mimeType", default="image")
            parts.append(f"[{mime} image omitted — this tool returned binary content]")
        elif kind == "resource":
            resource = getattr(block, "resource", None)
            text = getattr(resource, "text", None)
            uri = getattr(resource, "uri", "?")
            parts.append(text if text else f"[resource {uri}]")
        else:
            parts.append(str(block))
    return "\n".join(p for p in parts if p)


def _describe(error: Optional[BaseException]) -> str:
    """``Type: message`` for an error, looking through exception groups:
    anyio's own text is only ``unhandled errors in a TaskGroup``."""
    if error is None:
        return "the connection ended before the handshake finished"
    nested = getattr(error, "exceptions", None)
    if isinstance(nested, (list, tuple)) and nested:
        return "; ".join(_describe(e) for e in nested)
    return f"{type(error).__name__}: {error}"


#: Owner tasks of live connections. asyncio keeps only a weak reference to
#: a task, and an owner deliberately holds no reference to its client (see
#: `_hold_connection`), so without this set nothing would keep it alive.
_OWNERS: Set["asyncio.Task[None]"] = set()


@dataclass
class _Connection:
    """What a connected client holds: its owner task, the event that tells
    the owner to shut down, and the finaliser for a client never closed."""

    owner: "asyncio.Task[None]"
    closing: asyncio.Event
    finalizer: Any


def _transport(server: MCPServer) -> tuple:
    """``(transport, http client to close or None)`` for the server."""
    if server.url:
        from mcp.client.streamable_http import streamable_http_client

        http = None
        if server.headers:
            # The SDK's own factory: its default timeouts, plus the headers.
            from mcp.shared._httpx_utils import create_mcp_http_client

            http = create_mcp_http_client(headers=dict(server.headers))
        return streamable_http_client(server.url, http_client=http), http
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=server.command, args=list(server.args), env=server.env, cwd=server.cwd
    )
    return stdio_client(params), None


async def _hold_connection(
    server: MCPServer, ready: "asyncio.Future[Any]", closing: asyncio.Event
) -> None:
    """Enter the transport and session, hold them, exit them — in one task.

    The transports and the session enter anyio cancel scopes, and a cancel
    scope must be exited by the task that entered it. Entering them in the
    caller's task tied the connection to whichever task called
    ``connect()``: closing from another (a FastAPI lifespan connects, a
    shutdown handler closes) raised ``Attempted to exit cancel scope in a
    different task`` and cancelled the connecting task. So the scopes live
    here, in a task that exists for nothing else, and ``close()`` asks it
    to leave them.

    Takes the ready future and the event, never the client: a client
    dropped without ``close()`` must stay collectable so its finaliser can
    set ``closing``.
    """
    from mcp.client import Client

    async with AsyncExitStack() as stack:
        transport, http = _transport(server)
        if http is not None:
            await stack.enter_async_context(http)
        session = await stack.enter_async_context(Client(transport))
        tools = await _list_all_tools(session)
        if not ready.done():
            ready.set_result((session, tools))
        await closing.wait()


async def _list_all_tools(session: Any) -> List[Any]:
    """Every page of ``tools/list``, not just the first: reading one page
    and dropping the cursor hid a paginating server's later tools, and
    ``allow=`` then blamed the server for a tool it does provide."""
    collected: List[Any] = []
    cursor = None
    for _ in range(1000):  # a runaway-server backstop, not a page cap
        listed = await session.list_tools(cursor=cursor)
        collected.extend(_attr(listed, "tools", default=[]) or [])
        cursor = _attr(listed, "next_cursor", "nextCursor")
        if not cursor:
            break
    return collected


def _abandoned(loop: asyncio.AbstractEventLoop, closing: asyncio.Event, name: str) -> None:
    """Finaliser for a client collected while still connected: it only
    hands the loop a signal, and the owner task shuts the server down from
    its own task (finalising the scopes from the collector's context
    cancelled whichever task had connected)."""

    def shut_down() -> None:
        warnings.warn(
            f"MCPClient for {name!r} was garbage-collected without close(); "
            f"shutting its server down",
            ResourceWarning,
            stacklevel=1,
        )
        closing.set()

    if loop.is_closed():
        return  # asyncio.run() already cancelled the owner on the way out
    try:
        loop.call_soon_threadsafe(shut_down)
    except RuntimeError:  # the loop closed between the check and the call
        pass


class MCPClient:
    """A live connection to one MCP server.

    Holds the transport and session open for its lifetime, so it must be
    closed; :meth:`MCPToolset.connect` and ``async with`` do. The
    connection is owned by a task of its own, so connecting in one task and
    closing in another is safe. A client dropped without ``close()`` has
    its server shut down when it is garbage-collected, with a
    ``ResourceWarning`` — a backstop, not a lifecycle.
    """

    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self._conn: Optional[_Connection] = None
        self._session: Any = None
        self._tools: List[Any] = []

    # ------------------------------------------------------------ lifecycle

    async def connect(self) -> "MCPClient":
        """Start (or reach) the server, handshake, and read its tool list.

        Raises:
            MCPError: the server could not be reached or did not complete
                the handshake, or this client is already connected — a
                second connection would orphan the first one's server.
        """
        if self._conn is not None:
            raise MCPError(
                f"MCP server {self.server.name!r} is already connected — "
                f"close() it before connecting again"
            )
        try:
            import mcp  # noqa: F401
        except ImportError as e:  # pragma: no cover - declared as an extra
            raise MCPError("MCP support needs the SDK: pip install 'operonx-agents[mcp]'") from e

        loop = asyncio.get_running_loop()
        ready: "asyncio.Future[Any]" = loop.create_future()
        closing = asyncio.Event()
        owner = loop.create_task(
            _hold_connection(self.server, ready, closing), name=f"mcp:{self.server.name}"
        )
        _OWNERS.add(owner)
        owner.add_done_callback(_OWNERS.discard)
        try:
            await asyncio.wait({ready, owner}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # Abandoning a half-open connection. The owner unwinds its own
            # scopes; waiting for it means no server outlives this call.
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            raise
        if not ready.done():
            # The owner ended before the handshake did: that is the failure.
            error = None if owner.cancelled() else owner.exception()
            raise MCPError(
                f"could not connect to MCP server {self.server.name!r} "
                f"({self.server.where}): {_describe(error)}"
            ) from error

        self._session, self._tools = ready.result()
        self._conn = _Connection(
            owner=owner,
            closing=closing,
            finalizer=weakref.finalize(self, _abandoned, loop, closing, self.server.name),
        )
        return self

    async def close(self) -> None:
        """Shut the connection (and a stdio server) down. Idempotent, and
        safe from any task.

        Raises:
            MCPError: the shutdown itself failed. ``async with`` logs rather
                than raises when an error is already on its way out, so the
                real one is not replaced.
        """
        conn, self._conn = self._conn, None
        self._session = None
        if conn is None:
            return
        conn.finalizer.detach()
        conn.closing.set()
        owner = conn.owner
        if not owner.done():
            # `wait`, not `await owner`: a caller that stops waiting (its
            # own cancellation) must not cancel the shutdown half-way.
            await asyncio.wait({owner})
        if owner.cancelled():
            return  # torn down by the loop shutting down, in its own task
        error = owner.exception()
        if error is not None:
            raise MCPError(
                f"closing MCP server {self.server.name!r} failed: {_describe(error)}"
            ) from error

    async def __aenter__(self) -> "MCPClient":
        return await self.connect()

    async def __aexit__(self, exc_type: Any, exc: Optional[BaseException], _tb: Any) -> None:
        try:
            await self.close()
        except MCPError:
            if exc is None:
                raise
            # An error is already on its way out. Raising here would replace
            # it, and the caller would debug the teardown instead.
            LOGGER.warning(
                "MCP server %r: teardown failed while a %s was propagating",
                self.server.name,
                type(exc).__name__,
                exc_info=True,
            )

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def protocol_version(self) -> Optional[str]:
        """The MCP revision negotiated: ``2026-07-28`` (stateless) with a
        server that speaks it, an initialize-handshake one otherwise."""
        return self._session.protocol_version if self._session is not None else None

    # ----------------------------------------------------------------- use

    @property
    def tools(self) -> List[Any]:
        """Tool descriptors as the server reported them."""
        return list(self._tools)

    async def call(self, name: str, arguments: Dict[str, Any]) -> str:
        """Invoke one tool and return its text — what a model reads.

        Raises:
            MCPError: the connection is closed, the call timed out, or the
                server reported ``isError``. Raising rather than returning
                the text is deliberate: dispatch turns an exception into a
                tool message the model can act on.
        """
        return (await self._invoke(name, arguments))[1]

    async def call_value(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Invoke one tool and return its value — what code reads.

        The text of a result is for a model: a list arrives as one text
        block per item, so a one-item list reads as a bare object and an
        empty one as nothing. The server's ``structuredContent`` is the
        value itself; a tool returning a list or a scalar has it wrapped as
        ``{"result": ...}`` (its output schema says so), which is unwrapped
        here. Without structured content the text is parsed as JSON, and
        returned as it is when it is not JSON.
        """
        result, text = await self._invoke(name, arguments)
        structured = _attr(result, "structured_content", "structuredContent")
        if structured is not None:
            if (
                self._wraps_result(name)
                and isinstance(structured, dict)
                and set(structured) == {"result"}
            ):
                return structured["result"]
            return structured
        try:
            return json.loads(text) if text else None
        except ValueError:
            return text

    def _wraps_result(self, name: str) -> bool:
        """Whether the tool's output schema is the ``{"result": ...}`` wrapper."""
        found = next((t for t in self._tools if _attr(t, "name") == name), None)
        schema = _attr(found, "output_schema", "outputSchema") if found is not None else None
        if not isinstance(schema, dict):
            return False
        return set(schema.get("properties") or {}) == {"result"} and schema.get("required") == [
            "result"
        ]

    async def _invoke(self, name: str, arguments: Dict[str, Any]) -> tuple:
        """One tool call, checked: ``(raw result, its text)``."""
        if self._session is None:
            raise MCPError(
                f"MCP server {self.server.name!r} is not connected — "
                f"call connect() before using its tools"
            )
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(name, arguments or {}),
                timeout=self.server.timeout,
            )
        except asyncio.TimeoutError as e:
            raise MCPError(f"{self.server.name}__{name} exceeded {self.server.timeout:g}s") from e
        except Exception as e:
            raise MCPError(f"{self.server.name}__{name} failed: {type(e).__name__}: {e}") from e

        text = _flatten(_attr(result, "content"))
        if not text:
            # A spec-legal server may answer entirely in `structuredContent`
            # with no text block: reading only `content` returned "".
            structured = _attr(result, "structured_content", "structuredContent")
            if structured is not None:
                text = json.dumps(structured, default=str)
        if bool(_attr(result, "is_error", "isError", default=False)):
            # In-band error. Ignoring the flag would format a failure as an
            # answer, which is the one thing a tool result must never do.
            raise MCPError(f"{self.server.name}__{name} reported an error: {text}")
        return result, text


class MCPToolset(Toolset):
    """A connected server's tools, as one agent's :class:`Toolset`.

    Args:
        client: A connected :class:`MCPClient`.
        allow: Server-side tool names to expose. Omit for all of them.
            Naming a tool the server does not provide raises, rather than
            silently handing the agent a narrower toolset than its author
            wrote.
        prefix: The namespace (default: the server's name).

    Closing the toolset closes its client. Several toolsets may share one
    client (``MCPToolset(client, allow=[...], prefix=...)``); then close
    the client.

    Raises:
        MCPError: on a tool the server lacks, a schema that is not an
            object, or two server tools that become one name once made safe
            for a provider. Nothing is built: a toolset is all or nothing.
    """

    __slots__ = ("client",)

    def __init__(
        self,
        client: MCPClient,
        *,
        allow: Optional[List[str]] = None,
        prefix: Optional[str] = None,
    ) -> None:
        if not client.connected:
            raise MCPError(
                f"MCP server {client.server.name!r} is not connected — "
                "use `await MCPToolset.connect(server)`, or connect() the client first"
            )
        self.client = client
        super().__init__(_proxies(client, allow, prefix))

    @classmethod
    async def connect(
        cls,
        server: MCPServer,
        *,
        allow: Optional[List[str]] = None,
        prefix: Optional[str] = None,
    ) -> "MCPToolset":
        """Connect and build in one step. **Close it** — it owns a
        connection, and for stdio a child process::

            async with await MCPToolset.connect(MCPServer("fs", command=...)) as fs:
                ...
        """
        client = await MCPClient(server).connect()
        try:
            return cls(client, allow=allow, prefix=prefix)
        except Exception:
            try:
                await client.close()
            except MCPError:
                # The build error is the one the caller needs; a teardown
                # failure raised here would replace it.
                LOGGER.warning(
                    "MCP server %r: teardown failed after a toolset error",
                    server.name,
                    exc_info=True,
                )
            raise

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self) -> "MCPToolset":
        return self

    async def __aexit__(self, exc_type: Any, exc: Optional[BaseException], _tb: Any) -> None:
        await self.client.__aexit__(exc_type, exc, _tb)


#: JSON Schema type names → the annotations a tool's arguments validate as.
_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _flags(descriptor: Any) -> Dict[str, bool]:
    """MCP annotations as tool metadata. The hints are optional, advisory
    and the server's: absent means *unknown*, and unknown is destructive —
    an unannotated third-party tool asks a human before it runs."""
    ann = _attr(descriptor, "annotations")
    readonly = _attr(ann, "read_only_hint", "readOnlyHint") if ann is not None else None
    destructive = _attr(ann, "destructive_hint", "destructiveHint") if ann is not None else None
    idempotent = _attr(ann, "idempotent_hint", "idempotentHint") if ann is not None else None
    if readonly is True:
        return {"readonly": True, "destructive": False, "idempotent": True}
    return {
        "readonly": False,
        "destructive": destructive is not False,
        "idempotent": idempotent is True,
    }


def _proxies(client: MCPClient, allow: Optional[List[str]], prefix: Optional[str]) -> List[Tool]:
    """One :class:`Tool` per server tool. Everything that can fail is
    checked before anything is built."""
    namespace = _UNSAFE.sub("_", (prefix or client.server.name).strip())
    available = {_attr(t, "name", default="") for t in client.tools}
    if allow is not None:
        missing = [n for n in allow if n not in available]
        if missing:
            raise MCPError(
                f"MCP server {client.server.name!r} does not provide {missing}. "
                f"It offers: {sorted(available)}"
            )
    planned: Dict[str, tuple] = {}
    for descriptor in client.tools:
        server_name = _attr(descriptor, "name", default="")
        if not server_name or (allow is not None and server_name not in allow):
            continue
        # MCP allows dots and any length; providers require
        # ^[A-Za-z0-9_-]{1,64}$ and reject the *whole request* when one
        # name violates it — one `github.create_issue` stopped every tool.
        full_name = f"{namespace}__{_UNSAFE.sub('_', server_name)}"[:64]
        if full_name in planned:
            raise MCPError(
                f"MCP server {client.server.name!r} has two tools that both become "
                f"{full_name!r} once made safe for a provider: "
                f"{planned[full_name][0]!r} and {server_name!r}. Pass allow= to pick one."
            )
        description = (_attr(descriptor, "description", default="") or "").strip()
        if not description:
            description = f"Tool {server_name!r} provided by the {namespace} MCP server."
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS] + " …"
        schema = _attr(descriptor, "input_schema", "inputSchema") or {
            "type": "object",
            "properties": {},
        }
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise MCPError(
                f"MCP server {client.server.name!r} gave tool {server_name!r} an "
                f"inputSchema that is not a JSON Schema object: {schema!r}"
            )
        planned[full_name] = (server_name, description, schema, _flags(descriptor))
    return [_proxy(client, full_name, *spec) for full_name, spec in planned.items()]


def _proxy(
    client: MCPClient,
    full_name: str,
    server_name: str,
    description: str,
    schema: Dict[str, Any],
    flags: Dict[str, bool],
) -> Tool:
    """A tool that forwards to the server. Its signature is built from the
    server's schema, so arguments are validated (types, unknown names)
    before anything crosses the wire; the model sees the server's schema."""
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    params, annotations = [], {}
    for name, spec in properties.items():
        if not name.isidentifier():
            # A JSON key that is not an identifier cannot be a parameter;
            # skipping it would drop an argument the model was told about.
            raise MCPError(
                f"MCP server {client.server.name!r}: tool {server_name!r} has an argument "
                f"{name!r} that is not a valid Python identifier"
            )
        annotations[name] = _JSON_TYPES.get((spec or {}).get("type"), Any)
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if name in required else None,
            )
        )

    async def proxy(**kwargs: Any) -> str:
        # An optional argument the model left out is not sent as null: a
        # server whose schema says "string" would reject it.
        sent = {k: v for k, v in kwargs.items() if v is not None or k in required}
        return await client.call(server_name, sent)

    proxy.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    proxy.__annotations__ = annotations
    proxy.__name__ = full_name
    proxy.__qualname__ = f"mcp:{full_name}"
    return tool(
        proxy,
        name=full_name,
        description=description,
        schema=schema,
        max_result_chars=40_000,
        **flags,
    )
