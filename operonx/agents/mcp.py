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
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from operonx.agents.tool import TOOL_REGISTRY, tool

__all__ = [
    "MCPServer",
    "MCPClient",
    "MCPError",
    "register_mcp_tools",
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


class MCPClient:
    """A live connection to one MCP server.

    Holds the transport and session open for its lifetime, so it must be
    closed. Use :func:`connect_mcp` unless you need to manage that
    yourself.
    """

    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self._stack: Optional[AsyncExitStack] = None
        self._session: Any = None
        self._tools: List[Any] = []

    # ------------------------------------------------------------ lifecycle

    async def connect(self) -> "MCPClient":
        """Start the server, handshake, and read its tool list."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as e:  # pragma: no cover - declared as an extra
            raise MCPError("MCP support needs the SDK: pip install 'operonx[mcp]'") from e

        params = StdioServerParameters(
            command=self.server.command,
            args=list(self.server.args),
            env=self.server.env,
            cwd=self.server.cwd,
        )
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools = await self._list_all_tools(session)
        except Exception as e:
            await stack.aclose()
            raise MCPError(
                f"could not connect to MCP server {self.server.name!r} "
                f"({self.server.command}): {type(e).__name__}: {e}"
            ) from e

        self._stack = stack
        self._session = session
        self._tools = tools
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
        """Shut the server down. Idempotent."""
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:  # noqa: BLE001 — teardown must not mask the real error
                pass

    async def __aenter__(self) -> "MCPClient":
        return await self.connect()

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

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

    registered: List[str] = []
    for descriptor in client.tools:
        server_name = _attr(descriptor, "name", default="")
        if not server_name or (allow is not None and server_name not in allow):
            continue

        # The server-side half is third-party input. MCP allows dots and
        # arbitrary length; providers require ^[A-Za-z0-9_-]{1,64}$ and
        # reject the *whole request* when one name violates it — so a single
        # `github.create_issue` stopped every tool working, local ones too.
        full_name = f"{namespace}__{_UNSAFE.sub('_', server_name)}"[:64]
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

        _make_proxy(client, server_name, full_name, description, schema)
        registered.append(full_name)

    return registered


def _make_proxy(
    client: MCPClient,
    server_name: str,
    full_name: str,
    description: str,
    schema: Dict[str, Any],
) -> None:
    """Register one `@tool` that forwards to the server.

    A closure per tool, because the registry maps a name to one callable
    and the server-side name has to travel with it.
    """
    flags = _tool_flags(next(t for t in client.tools if _attr(t, "name") == server_name))

    async def proxy(**kwargs: Any) -> dict:
        text = await client.call(server_name, kwargs)
        return {"result": text}

    # What operonx inspects to decide the op's inputs. Without it the op
    # declares one input named `kwargs` and rejects every real argument.
    proxy.__signature__ = _signature_from_schema(schema)
    proxy.__name__ = full_name
    proxy.__qualname__ = f"mcp:{full_name}"
    proxy.__doc__ = description

    tool(
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
        await client.close()
        raise
    return client, names
