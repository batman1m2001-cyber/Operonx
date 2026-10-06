"""A server two of whose tool names collide once sanitised.

``read.file`` and ``read_file`` both become ``<namespace>__read_file``. MCP
allows the dot; providers do not, so operonx has to rewrite it — and a
rewrite can map two distinct names onto one.
"""

import sys

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer(name="clash")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def first() -> str:
    """Listed before the clash, so it is registered before the clash is found."""
    return "first"


@mcp.tool(name="read.file", annotations=ToolAnnotations(readOnlyHint=True))
def dotted(path: str) -> str:
    """Read a file (dotted name)."""
    return f"dotted {path}"


@mcp.tool(name="read_file", annotations=ToolAnnotations(readOnlyHint=True))
def plain(path: str) -> str:
    """Read a file (plain name)."""
    return f"plain {path}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
    sys.exit(0)
