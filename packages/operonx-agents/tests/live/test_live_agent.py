"""The runner against a real model (``-m live``; tiny spend).

- A 3-tool agent on ``qwen3.7-plus``, streamed, inside an op: it calls all
  three tools, answers from their results, and the trace nests
  ``turn[n]`` → ``model``, tools under the op.
- A 20-turn conversation over a ``RedisSession`` (``REDIS_URL``, a
  throwaway server): each run continues the same session, and the last
  turn recalls a fact from the first.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from operonx import END, START, Operon, graph, op

from operonx_agents import (
    Agent,
    Model,
    ModelSettings,
    RedisSession,
    RedisStateStore,
    Runner,
    TextDelta,
    ToolCallFinished,
    UsageLimits,
    tool,
)

CALLED: list = []


@tool(readonly=True)
async def order_status(order_id: str) -> str:
    """The shipping status of an order.

    Args:
        order_id: The order code, e.g. A1B2C3D4.
    """
    CALLED.append("order_status")
    return f"order {order_id}: shipped on 2 October"


@tool(readonly=True)
async def customer_tier(customer_id: str) -> str:
    """The loyalty tier of a customer.

    Args:
        customer_id: The customer's id, e.g. C-42.
    """
    CALLED.append("customer_tier")
    return f"customer {customer_id}: gold"


@tool(readonly=True)
async def refund_policy(tier: str) -> str:
    """How many days a customer of a loyalty tier has to ask for a refund.

    Args:
        tier: The tier name: bronze, silver or gold.
    """
    CALLED.append("refund_policy")
    return {"gold": "60 days", "silver": "30 days"}.get(tier.lower(), "14 days")


SUPPORT = Agent(
    name="support",
    model=Model("qwen3.7-plus", deadline=90, settings=ModelSettings(max_tokens=400)),
    instructions=(
        "You answer customer-support questions. Use the tools for every fact; never "
        "guess. Be brief."
    ),
    tools=[order_status, customer_tier, refund_policy],
    limits=UsageLimits(turns=6, total_tokens=20_000),
)
EVENTS: list = []


@op
async def support(question: str) -> dict:
    async for event in Runner.stream(SUPPORT, question):
        EVENTS.append(event)
    res = event.result
    return {"status": res.status, "output": res.output, "turns": res.turns}


@graph
def chat(question):
    s = support(question=question)
    START >> s >> END


async def test_a_three_tool_agent_on_qwen(live_hub):
    CALLED.clear()
    EVENTS.clear()
    events = EVENTS
    question = (
        "Customer C-42 asks about order A1B2C3D4: has it shipped, what is their loyalty "
        "tier, and how many days do they have to ask for a refund?"
    )
    started = time.perf_counter()
    handle = Operon(chat, params={"question": None}).start({"question": question})
    out = await handle.result()
    took = time.perf_counter() - started
    result = events[-1].result
    print(
        f"\nqwen3.7-plus 3-tool agent: {out['status']} in {result.turns} turns, {took:.1f}s, "
        f"usage {result.usage.to_dict()}, tools {CALLED}\nanswer: {result.output!r}"
    )
    assert out["status"] == "completed", result.error
    assert set(CALLED) == {"order_status", "customer_tier", "refund_policy"}
    answer = result.output.lower()
    assert "60" in answer and ("shipped" in answer or "2 october" in answer)
    assert result.usage.input_tokens > 0 and result.usage.requests == result.turns
    assert any(isinstance(e, TextDelta) for e in events), "the answer streamed"
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert len(finished) == len(CALLED) and all(f.ok for f in finished)
    names = [n.op_name for n in handle.trace.nodes]
    assert names.count("turn") == result.turns and names.count("model") == result.turns
    assert {"order_status", "customer_tier", "refund_policy"} <= set(names)


@pytest.mark.skipif(not os.environ.get("REDIS_URL"), reason="REDIS_URL not set")
async def test_a_twenty_turn_redis_session(live_hub):
    from redis import asyncio as aioredis

    client = aioredis.from_url(os.environ["REDIS_URL"])
    prefix = f"live-{uuid.uuid4().hex}:"
    session = RedisSession("chat", client, prefix=prefix + "session:")
    store = RedisStateStore(client, prefix=prefix + "run:")
    agent = Agent(
        name="companion",
        model=Model("qwen3.7-plus", deadline=60, settings=ModelSettings(max_tokens=60)),
        instructions="You are a terse assistant. Answer in one short sentence.",
        limits=UsageLimits(turns=2),
    )
    turns = ["My favourite bird is the pelican. Just say ok."]
    turns += [f"Turn {i}: what is {i} plus {i}? Reply with the number only." for i in range(2, 20)]
    turns += ["What is my favourite bird? Reply with the bird only."]
    try:
        results = []
        started = time.perf_counter()
        for text in turns:
            results.append(await Runner.run(agent, text, session=session, store=store))
        took = time.perf_counter() - started
        items = await session.get_items()
        usage = sum((r.usage for r in results[1:]), results[0].usage)
        print(
            f"\n20-turn Redis session: {len(items)} items, {took:.1f}s, usage {usage.to_dict()}"
            f"\nlast answer: {results[-1].output!r}"
        )
        assert all(r.status == "completed" for r in results), [r.error for r in results]
        assert len(items) == 40, "one user and one assistant item per run"
        assert [i["role"] for i in items] == ["user", "assistant"] * 20
        assert "PELICAN" in results[-1].output.upper(), "turn 20 recalls turn 1 over Redis"
        assert results[-1].usage.input_tokens > results[0].usage.input_tokens
        for r in results:
            saved = await store.load(r.run_id)
            assert saved.status == "completed" and saved.saved == 2
    finally:
        for key in await client.keys(prefix + "*"):
            await client.delete(key)
        await client.aclose()
