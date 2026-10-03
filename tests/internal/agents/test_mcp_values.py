"""`MCPClient.call_value`: a tool's value, for code — not its text, for a model.

`call()` returns text, which is right for a model and wrong for code: a list
arrives as one text block per item, so one person read as a bare record and
no people as nothing at all. A caller building a workflow on MCP tools had
to guess which. `call_value()` returns the server's structured value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
pytest.importorskip("mcp", reason="needs operonx[mcp]")

from operonx.agents.mcp import MCPClient, MCPServer  # noqa: E402

SERVER = Path(__file__).parent / "mcp_fixtures" / "values_server.py"


@pytest.fixture
async def client():
    c = await MCPClient(
        MCPServer(name="values", command=sys.executable, args=[str(SERVER)])
    ).connect()
    yield c
    await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [0, 1, 2])
async def test_a_list_stays_a_list_whatever_its_length(client, n):
    got = await client.call_value("people", {"n": n})
    assert isinstance(got, list) and len(got) == n


@pytest.mark.asyncio
async def test_the_text_alone_could_not_tell(client):
    # what the model reads: one item is indistinguishable from a record
    one = await client.call("people", {"n": 1})
    record = await client.call("person", {"name": "Linh"})
    assert one == record


@pytest.mark.asyncio
async def test_a_record_and_a_scalar_come_back_as_values(client):
    assert await client.call_value("person", {"name": "Bao"}) == {"name": "Bao", "role": "IT"}
    assert await client.call_value("count", {}) == 2


@pytest.mark.asyncio
async def test_plain_text_stays_text(client):
    assert await client.call_value("note", {}) == "no structure here"
