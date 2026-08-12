"""A spec-legal MCP server that paginates tools/list, as many real servers do."""
import asyncio

import mcp.server.stdio
import mcp_types as types
from mcp.server.lowlevel import Server

ALL = [
    types.Tool(name=f"tool_{i}", description=f"tool {i}",
               inputSchema={"type": "object", "properties": {}},
               annotations=types.ToolAnnotations(readOnlyHint=True))
    for i in range(5)
]
PAGE = 2


async def on_list_tools(ctx, params):
    cursor = getattr(params, "cursor", None) if params else None
    start = int(cursor) if cursor else 0
    page = ALL[start:start + PAGE]
    nxt = str(start + PAGE) if start + PAGE < len(ALL) else None
    return types.ListToolsResult(tools=page, nextCursor=nxt)


async def on_call_tool(ctx, params):
    return types.CallToolResult(content=[types.TextContent(type="text", text=f"ran {params.name}")])


async def main():
    server = Server("paged", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    async with mcp.server.stdio.stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())

asyncio.run(main())
