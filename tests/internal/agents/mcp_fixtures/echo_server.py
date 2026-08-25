"""A real MCP server, for testing the client against the protocol.

A mock would verify the code we wrote against the contract we assumed —
which is precisely the failure mode that produced four defects in this
codebase already. This speaks actual MCP over actual stdio.
"""

import sys

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

mcp = MCPServer(name="echo")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def echo(text: str) -> str:
    """Echo the text back. Annotated read-only, so it must not be gated."""
    return f"echo: {text}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def explode(reason: str) -> str:
    """Always fails, with a message the client is meant to see.

    ``ToolError`` is the SDK's way to say "a failure I anticipated": the call
    returns ``is_error=True`` with this text in ``content``. A bare
    ``RuntimeError`` would be treated as a crash and, since mcp 2.1,
    deliberately masked as ``Error executing tool explode`` so nothing from
    the original reaches the client. This fixture previously relied on that
    leak, which is what `crash` below now covers on purpose.
    """
    raise ToolError(f"deliberate failure: {reason}")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def crash(reason: str) -> str:
    """Fails the way an unanticipated bug does — the message must not leak."""
    raise RuntimeError(f"internal detail: {reason}")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def slow(seconds: float) -> str:
    """Sleep, to exercise the call timeout."""
    import time

    time.sleep(seconds)
    return "finished"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def braces() -> str:
    """Return JSON, which used to poison the next model call."""
    return '{"city": "Hanoi", "temp": 30}'


@mcp.tool()
def unannotated(x: str) -> str:
    """No annotations at all — a server that says nothing about what this
    does. Absent hints mean unknown, and unknown must be gated."""
    return f"did something with {x}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
    sys.exit(0)
