"""MCP — using tools that live in another process.

The Model Context Protocol is how an agent reaches tools it did not ship
with: a filesystem server, a database server, whatever someone else wrote.
This module connects to such a server and exposes its tools as ordinary
`@tool` ops, so the ReAct loop, the permission gate and the redactor treat
them exactly like local ones.

Three things about MCP make it different from a local tool, and each shows
up in the API here.

**Discovery is asynchronous, registration is not.** `@tool` registers at
import time; an MCP server's tool list only exists after a connection and
a handshake. So registration is an explicit `await`, and it has to happen
*before* the agent graph is built — `get_tool_definitions()` reads the
registry at build time.

**The server is someone else's code.** Its tool descriptions go straight
into the model's context, which makes them an injection surface, and its
arguments schema is whatever it says it is. Names are namespaced so a
server cannot shadow a local tool, and a tool the server does not describe
as read-only is gated by default.

**A failed call must look failed.** MCP reports errors in-band, as
`isError` on an otherwise ordinary result. Reading the content and
ignoring the flag would hand the model an error message formatted as an
answer — so the flag is checked and raises, which the dispatch layer turns
into a tool message the model can recover from.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import warnings
import weakref
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from operonx.agents.tool import TOOL_REGISTRY, tool, unregister_tool
from operonx.core.loggings import LOGGER

__all__ = [
    "MCPServer",
    "MCPClient",
    "MCPError",
    "register_mcp_tools",
    "unregister_mcp_tools",
    "connect_mcp",
]

#: Tool names must be a stable handle for the model, and providers reject
#: anything exotic. Server names are user-supplied, so they are sanitised.
_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")

#: A description is context the model pays for on every turn, and it comes
#: from a third party. Long ones are truncated rather than trusted.
MAX_DESCRIPTION_CHARS = 1024


class MCPError(Exception):
    """A protocol-level failure: connect, handshake, or a tool call."""


@dataclass
class MCPServer:
    """How to reach one MCP server.

    Args:
        name: Short label. Becomes the tool-name prefix, so it must be
            distinctive — ``fs`` gives ``fs__read_file``.
        command: Executable for a stdio server, e.g. ``"npx"``.
        args: Arguments for ``command``.
        env: Extra environment for the child process. Passed as given —
            a server that needs a token gets it here, and it is the
            caller's business which one.
        cwd: Working directory for the child process.
        timeout: Seconds any single tool call may take.
    """

    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    timeout: float = 60.0

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise MCPError("MCPServer needs a name — it prefixes every tool it provides")
        if not self.command or not self.command.strip():
            raise MCPError(f"MCPServer {self.name!r} needs a command to run")
        self.name = _UNSAFE.sub("_", self.name.strip())


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first attribute that exists, by any of its spellings.

    The MCP wire format is camelCase (``isError``, ``inputSchema``) and the
    Python SDK exposes snake_case (``is_error``, ``input_schema``). Reading
    only one spelling means every ``getattr`` silently returns its default:
    measured against a live server, ``isError`` was always absent, so a
    failed tool call — content and all — was handed to the model as a
    successful result.

    Trying both is not indecision. The protocol genuinely has two names for
    these fields, and which one a given SDK version surfaces is not
    something to assume.
    """
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _flatten(content: Any) -> str:
    """Turn an MCP content list into text the model can read.

    MCP returns a list of typed blocks. A model reading a tool result
    wants the text; a block it cannot render is described rather than
    dropped, because a silently shortened result is indistinguishable
    from a complete one.
    """
    if content is None:
        return ""
    parts: List[str] = []
    for block in content if isinstance(content, list) else [content]:
        kind = getattr(block, "type", None)
        if kind == "text":
            parts.append(getattr(block, "text", "") or "")
        elif kind == "image":
            mime = getattr(block, "mimeType", "image")
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
    """``Type: message`` for an error, looking through exception groups.

    Anything raised inside the transport surfaces wrapped in anyio's task
    group, whose own text is only ``unhandled errors in a TaskGroup (1
    sub-exception)`` — true, and useless to whoever reads it.
    """
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


async def _hold_connection(
    params: Any, ready: "asyncio.Future[Any]", closing: asyncio.Event
) -> None:
    """Enter the transport and session, hold them, exit them — in one task.

    ``stdio_client`` and ``ClientSession`` both enter anyio cancel scopes,
    and a cancel scope must be exited by the task that entered it. Entering
    them in the caller's task tied the connection to whichever task
    happened to call ``connect()``. Closing from another task — a FastAPI
    lifespan connects, a shutdown handler closes — raised ``Attempted to
    exit cancel scope in a different task`` and, worse, the task group's
    own ``cancel()`` on the way out cancelled the task that had connected,
    while it was still running. So the scopes live here, in a task that
    exists for nothing else, and ``close()`` asks it to leave them.

    Takes the ready future and the event, never the client: a client
    dropped without ``close()`` must stay collectable so its finaliser can
    set ``closing``.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = await MCPClient._list_all_tools(session)
        if not ready.done():
            ready.set_result((session, tools))
        await closing.wait()


def _abandoned(loop: asyncio.AbstractEventLoop, closing: asyncio.Event, name: str) -> None:
    """Finaliser for a client collected while still connected.

    Runs wherever the garbage collector does, so it only hands the loop a
    signal: the owner task then shuts the server down from its own task.
    Before, the garbage collector's finalisation exited the scopes itself,
    from foreign context, and cancelled whichever task had connected.
    """

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
    closed. Use :func:`connect_mcp` unless you need to manage that
    yourself.

    The connection is owned by a task of its own, not by the task that
    calls ``connect()``, so connecting in one task and closing in another
    is safe. A client dropped without ``close()`` has its server shut down
    when it is garbage-collected, with a ``ResourceWarning`` — a backstop,
    not a lifecycle: until collection the server keeps running.
    """

    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self._conn: Optional[_Connection] = None
        self._session: Any = None
        self._tools: List[Any] = []
        #: Operonx name → the proxy factory this client registered under it,
        #: kept so its proxies can be withdrawn without touching anything
        #: else in the registry.
        self._registered: Dict[str, Any] = {}

    # ------------------------------------------------------------ lifecycle

    async def connect(self) -> "MCPClient":
        """Start the server, handshake, and read its tool list.

        Raises:
            MCPError: the server could not be started or did not complete
                the handshake, or this client is already connected — a
                second connection would orphan the first one's server.
        """
        if self._conn is not None:
            raise MCPError(
                f"MCP server {self.server.name!r} is already connected — "
                f"close() it before connecting again"
            )
        try:
            from mcp import StdioServerParameters
        except ImportError as e:  # pragma: no cover - declared as an extra
            raise MCPError("MCP support needs the SDK: pip install 'operonx[mcp]'") from e

        params = StdioServerParameters(
            command=self.server.command,
            args=list(self.server.args),
            env=self.server.env,
            cwd=self.server.cwd,
        )
        loop = asyncio.get_running_loop()
        ready: "asyncio.Future[Any]" = loop.create_future()
        closing = asyncio.Event()
        owner = loop.create_task(
            _hold_connection(params, ready, closing), name=f"mcp:{self.server.name}"
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
                f"({self.server.command}): {_describe(error)}"
            ) from error

        self._session, self._tools = ready.result()
        self._conn = _Connection(
            owner=owner,
            closing=closing,
            finalizer=weakref.finalize(self, _abandoned, loop, closing, self.server.name),
        )
        return self

    @staticmethod
    async def _list_all_tools(session: Any) -> List[Any]:
        """Every page of ``tools/list``, not just the first.

        ``list_tools()`` returns one page plus a ``next_cursor``. Reading
        one page and dropping the cursor meant a paginating server's later
        tools were never registered — and ``allow=`` then reported them as
        "not provided", blaming the server for a tool it does provide.
        Measured against a 5-tool server with page size 2: operonx saw 2.
        """
        from mcp.types import PaginatedRequestParams

        collected: List[Any] = []
        cursor = None
        for _ in range(1000):  # a runaway-server backstop, not a page cap
            listed = await (
                session.list_tools()
                if cursor is None
                else session.list_tools(params=PaginatedRequestParams(cursor=cursor))
            )
            collected.extend(_attr(listed, "tools", default=[]) or [])
            cursor = _attr(listed, "next_cursor", "nextCursor")
            if not cursor:
                break
        return collected

    async def close(self) -> None:
        """Unregister this client's tools and shut the server down.

        Idempotent, and safe from any task. Local tools are not touched.

        Raises:
            MCPError: the shutdown itself failed. It used to be swallowed,
                which is how a cross-task close that raised ``Attempted to
                exit cancel scope in a different task`` went unnoticed.
                ``async with`` still logs rather than raises when an error
                is already on its way out, so the real one is not replaced.
        """
        # First, and whatever the shutdown does: a proxy left registered
        # after this point is advertised to the model and fails every call.
        unregister_mcp_tools(self)
        conn, self._conn = self._conn, None
        self._session = None
        if conn is None:
            return
        conn.finalizer.detach()
        conn.closing.set()
        owner = conn.owner
        if not owner.done():
            # `wait`, not `await owner`: a caller that stops waiting (its
            # own cancellation) must not cancel the shutdown half-way. The
            # owner finishes it either way.
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

    # ----------------------------------------------------------------- use

    @property
    def tools(self) -> List[Any]:
        """Tool descriptors as the server reported them."""
        return list(self._tools)

    async def call(self, name: str, arguments: Dict[str, Any]) -> str:
        """Invoke one tool and return its text.

        Raises:
            MCPError: the connection is closed, the call timed out, or the
                server reported ``isError``. Raising rather than returning
                the text is deliberate — the dispatch layer turns an
                exception into a tool message the model can act on, while
                returning error text as a result would read as an answer.
        """
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
            # with no text block. Reading only `content` returned "" — a
            # success carrying nothing, which is the shortened-result
            # failure `_flatten` exists to prevent.
            structured = _attr(result, "structured_content", "structuredContent")
            if structured is not None:
                import json as _json

                text = _json.dumps(structured, default=str)
        if bool(_attr(result, "is_error", "isError", default=False)):
            # In-band error. Ignoring the flag would format a failure as an
            # answer, which is the one thing a tool result must never do.
            raise MCPError(f"{self.server.name}__{name} reported an error: {text}")
        return text


#: JSON Schema type names → the Python annotations operonx wires against.
_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _signature_from_schema(schema: Dict[str, Any]) -> inspect.Signature:
    """Build a real call signature from an MCP tool's inputSchema.

    Operonx derives an op's inputs by **inspecting the function
    signature**, so a ``**kwargs`` proxy declares exactly one input called
    ``kwargs`` and every real argument is rejected:

        TypeError: 'text' is not a known input or output of proxy().
        Inputs: {'kwargs'}

    The proxy still accepts ``**kwargs`` at call time; only the *declared*
    signature is synthesised, which is what introspection reads.

    Required properties become positional-or-keyword parameters with no
    default. Optional ones default to ``None`` — an absent optional
    argument must not look like a missing required one.
    """
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    params = []
    for name, spec in properties.items():
        if not name.isidentifier():
            # A JSON key that is not a Python identifier cannot be a
            # parameter. Skipping silently would drop an argument the model
            # was told it could send.
            raise MCPError(
                f"tool argument {name!r} is not a valid Python identifier, so "
                f"it cannot be wired as an op input"
            )
        annotation = _JSON_TYPES.get((spec or {}).get("type"), Any)
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=inspect.Parameter.empty if name in required else None,
                annotation=annotation,
            )
        )
    # Required-without-default must precede defaulted parameters.
    params.sort(key=lambda p: p.default is not inspect.Parameter.empty)
    return inspect.Signature(params)


def _tool_flags(descriptor: Any) -> Dict[str, bool]:
    """Map MCP annotations onto operonx tool metadata.

    MCP's hints are optional and advisory, and they come from the server.
    Absent hints mean *unknown*, and unknown is treated as destructive —
    an unannotated third-party tool asks a human before it runs, which is
    the only safe reading of "we do not know what this does".
    """
    ann = _attr(descriptor, "annotations")
    readonly = _attr(ann, "read_only_hint", "readOnlyHint") if ann is not None else None
    destructive = _attr(ann, "destructive_hint", "destructiveHint") if ann is not None else None

    if readonly is True:
        return {"readonly": True, "destructive": False}
    if destructive is False:
        return {"readonly": False, "destructive": False}
    return {"readonly": False, "destructive": True}


async def register_mcp_tools(
    client: MCPClient,
    *,
    allow: Optional[List[str]] = None,
    prefix: Optional[str] = None,
) -> List[str]:
    """Register a connected server's tools, returning their operonx names.

    Must be called **before** the agent graph is built:
    ``get_tool_definitions()`` reads the registry at build time, so a tool
    registered afterwards exists but is never offered to the model.

    Args:
        client: A connected :class:`MCPClient`.
        allow: Server-side tool names to expose. Omit for all of them.
            Naming a tool the server does not provide raises, rather than
            silently handing the agent a narrower toolset than its author
            wrote.
        prefix: Override the namespace. Defaults to the server name.

    Returns:
        The registered operonx tool names, e.g. ``["fs__read_file"]``.
    """
    namespace = _UNSAFE.sub("_", (prefix or client.server.name).strip())
    available = {_attr(t, "name", default="") for t in client.tools}
    if allow is not None:
        missing = [n for n in allow if n not in available]
        if missing:
            raise MCPError(
                f"MCP server {client.server.name!r} does not provide {missing}. "
                f"It offers: {sorted(available)}"
            )

    # Everything that can fail is checked before anything is registered.
    # Registering tool by tool meant a failure part-way — a name collision,
    # a bad schema — left the earlier tools registered: advertised by
    # `get_tool_definitions()`, and raising "not connected" on every call
    # once `connect_mcp` had closed the client.
    planned: Dict[str, tuple] = {}
    for descriptor in client.tools:
        server_name = _attr(descriptor, "name", default="")
        if not server_name or (allow is not None and server_name not in allow):
            continue

        # The server-side half is third-party input. MCP allows dots and
        # arbitrary length; providers require ^[A-Za-z0-9_-]{1,64}$ and
        # reject the *whole request* when one name violates it — so a single
        # `github.create_issue` stopped every tool working, local ones too.
        full_name = f"{namespace}__{_UNSAFE.sub('_', server_name)}"[:64]
        if full_name in planned:
            raise MCPError(
                f"MCP server {client.server.name!r} has two tools that both become "
                f"{full_name!r} once made safe for a provider: "
                f"{planned[full_name][0]!r} and {server_name!r}. Pass allow= to "
                f"pick one."
            )
        if full_name in TOOL_REGISTRY:
            raise MCPError(
                f"{full_name!r} is already registered. Two servers sharing a "
                f"namespace would let one shadow the other's tools — pass "
                f"prefix= to separate them."
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
            # `@tool` would reject it later with a message about our
            # schema; saying whose schema it is saves the confusion.
            raise MCPError(
                f"MCP server {client.server.name!r} gave tool {server_name!r} an "
                f"inputSchema that is not a JSON Schema object: {schema!r}"
            )

        planned[full_name] = (
            server_name,
            description,
            schema,
            _signature_from_schema(schema),
            _tool_flags(descriptor),
        )

    registered: List[str] = []
    try:
        for full_name, (server_name, description, schema, signature, flags) in planned.items():
            factory = _make_proxy(
                client, server_name, full_name, description, schema, signature, flags
            )
            client._registered[full_name] = factory
            registered.append(full_name)
    except BaseException:
        # Nothing above should fail after the checks; if something does,
        # the registry goes back to exactly what it was.
        for full_name in registered:
            if TOOL_REGISTRY.get(full_name) is client._registered.pop(full_name, None):
                unregister_tool(full_name)
        raise

    return registered


def unregister_mcp_tools(client: MCPClient) -> List[str]:
    """Withdraw every tool ``client`` registered, returning their names.

    :meth:`MCPClient.close` does this itself; call it directly to stop
    offering a server's tools while keeping the connection. Local tools,
    and other clients' tools, are not touched — nor is a name that has
    since been taken by a different tool (after a ``clear_registry()``,
    say). Rebuild the agent graph afterwards: it reads the registry at
    build time.
    """
    removed: List[str] = []
    for full_name, factory in list(client._registered.items()):
        if TOOL_REGISTRY.get(full_name) is factory:
            unregister_tool(full_name)
            removed.append(full_name)
    client._registered.clear()
    return removed


def _make_proxy(
    client: MCPClient,
    server_name: str,
    full_name: str,
    description: str,
    schema: Dict[str, Any],
    signature: inspect.Signature,
    flags: Dict[str, bool],
) -> Any:
    """Register one `@tool` that forwards to the server; return its factory.

    A closure per tool, because the registry maps a name to one callable
    and the server-side name has to travel with it.
    """

    async def proxy(**kwargs: Any) -> dict:
        text = await client.call(server_name, kwargs)
        return {"result": text}

    # What operonx inspects to decide the op's inputs. Without it the op
    # declares one input named `kwargs` and rejects every real argument.
    proxy.__signature__ = signature
    proxy.__name__ = full_name
    proxy.__qualname__ = f"mcp:{full_name}"
    proxy.__doc__ = description

    return tool(
        name=full_name,
        description=description,
        schema=schema,
        max_result_chars=40_000,
        **flags,
    )(proxy)


async def connect_mcp(
    server: MCPServer,
    *,
    allow: Optional[List[str]] = None,
    prefix: Optional[str] = None,
) -> tuple[MCPClient, List[str]]:
    """Connect and register in one step.

    Returns ``(client, names)``. **Close the client** — it owns a child
    process::

        client, names = await connect_mcp(MCPServer(name="fs", command=…))
        try:
            agent = build_react_agent(...)
        finally:
            await client.close()
    """
    client = await MCPClient(server).connect()
    try:
        names = await register_mcp_tools(client, allow=allow, prefix=prefix)
    except Exception:
        try:
            await client.close()
        except MCPError:
            # The registration error is the one the caller needs; a
            # teardown failure raised here would replace it.
            LOGGER.warning(
                "MCP server %r: teardown failed after a registration error",
                server.name,
                exc_info=True,
            )
        raise
    return client, names
