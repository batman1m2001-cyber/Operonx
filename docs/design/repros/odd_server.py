"""A server exercising: optional args, an exotic tool name, a very long name,
and structured-only output."""
from typing import Optional

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer(name="odd")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def search(query: str, limit: Optional[int] = None, tag: Optional[str] = None) -> str:
    """Search. limit/tag are optional and MUST be omitted, not sent as null."""
    return f"query={query!r} limit={limit!r} tag={tag!r}"


@mcp.tool(name="read.file:v2", annotations=ToolAnnotations(readOnlyHint=True))
def dotted(path: str) -> str:
    """A tool whose server-side name is not a safe provider identifier."""
    return f"read {path}"


@mcp.tool(
    name="a" * 70,
    annotations=ToolAnnotations(readOnlyHint=True),
)
def longname() -> str:
    """A tool whose name blows past the provider's 64-char limit."""
    return "long"


if __name__ == "__main__":
    mcp.run(transport="stdio")
