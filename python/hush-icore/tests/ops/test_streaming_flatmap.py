"""Test N-to-M streaming: one generator receives N items, yields M outputs.

Scenario:
    - Generator receives 3 items, yields variable outputs per item (total 10)
    - Downstream takes 0.5s per item
    - If concurrent: ~0.5s processing (all 10 in parallel)
    - If sequential: ~5s processing
"""

import asyncio
import time

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op


@op
async def explode(items: list):
    """N inputs → M outputs. Each item explodes into sub-items with 0.2s gap."""
    for item in items:
        await asyncio.sleep(0.2)
        for sub in range(item):
            yield {"value": sub, "source": item}


@op
async def process(value: int, source: int):
    """Simulate 0.5s processing per item."""
    await asyncio.sleep(0.5)
    return {"result": f"{source}:{value}"}


@pytest.mark.asyncio
async def test_n_to_m_streaming():
    """3 inputs → 10 yields (2+3+5), all processed concurrently."""
    with GraphOp(name="flatmap") as graph:
        gen = explode(items=PARENT["items"])
        proc = process(value=gen["value"], source=gen["source"])
        START >> gen >> proc >> END

    engine = Hush(graph)

    wall_start = time.monotonic()
    result = await engine.run(inputs={"items": [2, 3, 5]})
    wall_elapsed = time.monotonic() - wall_start

    # 2 yields from item=2: (2:0, 2:1)
    # 3 yields from item=3: (3:0, 3:1, 3:2)
    # 5 yields from item=5: (5:0, 5:1, 5:2, 5:3, 5:4)
    # Total: 10 results
    assert len(result["result"]) == 10

    results = sorted(result["result"])
    assert "2:0" in results
    assert "2:1" in results
    assert "3:0" in results
    assert "5:4" in results

    print(f"\nWall time: {wall_elapsed:.2f}s")
    print(f"Results: {sorted(result['result'])}")

    # 3 items × 0.2s yield gap = 0.6s producing + 0.5s parallel processing ≈ 1.1s
    # Sequential would be 0.6s + 10 × 0.5s = 5.6s
    assert wall_elapsed < 3, f"Took {wall_elapsed:.2f}s — likely not concurrent"
