"""An MCP server whose tools return data, not prose: lists, records, scalars.

A list comes over the wire as one text block per item, so its text alone
cannot tell one item from a record, or no items from an empty string. The
value travels in ``structuredContent``.
"""

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer(name="values")
PEOPLE = [{"name": "Linh", "role": "COO"}, {"name": "Bao", "role": "IT"}]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def people(n: int) -> list[dict]:
    """The first n people."""
    return PEOPLE[:n]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def person(name: str) -> dict:
    """One person, as a record."""
    return next((p for p in PEOPLE if p["name"] == name), {})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def count() -> int:
    """How many people."""
    return len(PEOPLE)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def note() -> str:
    """A plain sentence."""
    return "no structure here"


if __name__ == "__main__":
    mcp.run()
