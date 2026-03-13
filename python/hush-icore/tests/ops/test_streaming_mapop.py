"""Test streaming fan-out behaves like the old MapOp — concurrent processing.

Scenario:
    - Generator yields 10 items, 0.2s apart (total ~2s to produce all)
    - Downstream async op takes 2s per item
    - If concurrent: total ~4s (2s producing + 2s processing in parallel)
    - If sequential: total ~22s (2s producing + 10 * 2s processing)
"""

import asyncio
import time

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op


@op
async def produce_items(count: int):
    """Generator that yields items with a 0.2s gap."""
    for i in range(count):
        await asyncio.sleep(0.2)
        yield {"item": i, "produced_at": time.monotonic()}


@op
async def slow_process(item: int):
    """Simulate heavy processing — 2s per item."""
    start = time.monotonic()
    await asyncio.sleep(2)
    return {
        "result": item * 10,
        "process_start": start,
        "process_end": time.monotonic(),
    }


@pytest.mark.asyncio
async def test_streaming_concurrent_fanout():
    """Verify streaming fan-out runs downstream ops concurrently."""
    with GraphOp(name="stream_map") as graph:
        gen = produce_items(count=PARENT["count"])
        proc = slow_process(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(graph)

    wall_start = time.monotonic()
    result = await engine.run(inputs={"count": 10})
    wall_elapsed = time.monotonic() - wall_start

    # Should have 10 results
    assert len(result["result"]) == 10
    assert sorted(result["result"]) == [i * 10 for i in range(10)]

    # Concurrent: ~4s (2s yield + 2s parallel processing)
    # Sequential would be ~22s
    # Allow generous margin — should be well under 8s
    print(f"\nWall time: {wall_elapsed:.2f}s")
    print(f"Results: {result['result']}")
    assert wall_elapsed < 8, f"Took {wall_elapsed:.2f}s — likely sequential, not concurrent"

    # Verify process windows overlap (concurrency proof)
    starts = result["process_start"]
    ends = result["process_end"]
    # If concurrent, the gap between first start and last start should be small
    start_spread = max(starts) - min(starts)
    print(
        f"Process start spread: {start_spread:.2f}s (should be ~2s if streaming, ~0s if all at once)"
    )
    # All 10 should start within the ~2s yield window, not sequentially
    assert start_spread < 5, f"Start spread {start_spread:.2f}s — not concurrent enough"
