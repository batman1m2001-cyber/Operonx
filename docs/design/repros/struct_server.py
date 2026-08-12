import asyncio

import mcp.server.stdio
import mcp_types as types
from mcp.server.lowlevel import Server

ALL = [types.Tool(name="stats", description="structured-only output",
                  inputSchema={"type": "object", "properties": {}},
                  annotations=types.ToolAnnotations(readOnlyHint=True))]

async def on_list_tools(ctx, params):
    return types.ListToolsResult(tools=ALL)

async def on_call_tool(ctx, params):
    # Spec-legal: structured output, no text mirror.
    return types.CallToolResult(content=[], structuredContent={"rows": 42, "ok": True})

async def main():
    s = Server("st", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    async with mcp.server.stdio.stdio_server() as (r, w):
        await s.run(r, w, s.create_initialization_options())
asyncio.run(main())
