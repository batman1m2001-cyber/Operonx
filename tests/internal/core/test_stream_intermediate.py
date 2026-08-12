"""F5 — watching a generator that feeds a consumer.

Two separate things were true, and together they read as "streaming
frames are dropped":

1. ``handle`` frames are the graph's **outputs**. A generator wired into
   a downstream op emits none of them, whatever it yields. That is by
   design — ``result()`` is built from those frames — but nothing said so.
2. ``stream(mode="updates")`` did see every op, and then delivered nothing
   until an *output* frame arrived, because ``async for _ in handle`` was
   the pacer. Measured: four yields 150ms apart, all released together at
   the end. That is the shape of an LLM streaming into a consumer, so the
   one mode that could watch it was not actually live.

(1) is now documented; (2) is fixed by pacing on the write bus.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op

pytestmark = pytest.mark.unit

TICK = 0.06


def _late_output_graph(name="late"):
    """A generator whose only consumer emits once, at the very end."""

    @op
    async def produce(n: int):
        for i in range(4):
            await asyncio.sleep(TICK)
            yield {"chunk": str(i)}

    @op
    def gather(chunk: list):
        return {"joined": "".join(chunk)}

    with GraphOp(name=name) as g:
        p = produce(n=PARENT["n"])
        j = gather(chunk=p["chunk"].collect())
        START >> p >> j >> END
    return g


class TestUpdatesAreLive:
    @pytest.mark.asyncio
    async def test_chunks_arrive_before_the_run_ends(self):
        """The regression this guards released all four together."""
        started = time.perf_counter()
        arrivals = []
        async for batch in Operon(_late_output_graph("live")).stream({"n": 1}, mode="updates"):
            for payload in batch.values():
                if "chunk" in payload and not isinstance(payload["chunk"], list):
                    arrivals.append(time.perf_counter() - started)

        assert len(arrivals) == 4
        # The last chunk cannot be produced before 4 ticks. If the first
        # one arrives near that too, the stream was buffered, not live.
        assert arrivals[0] < arrivals[-1] * 0.6, f"delivery was batched: {arrivals}"

    @pytest.mark.asyncio
    async def test_every_chunk_is_delivered_in_order(self):
        seen = []
        async for batch in Operon(_late_output_graph("order")).stream({"n": 1}, mode="updates"):
            for payload in batch.values():
                if "chunk" in payload and not isinstance(payload["chunk"], list):
                    seen.append(payload["chunk"])
        assert seen == ["0", "1", "2", "3"]

    @pytest.mark.asyncio
    async def test_the_downstream_op_still_reports(self):
        """Pacing changed; completeness must not."""
        joined = [
            payload["joined"]
            async for batch in Operon(_late_output_graph("done")).stream({"n": 1}, mode="updates")
            for payload in batch.values()
            if "joined" in payload
        ]
        assert "0123" in joined


class TestFramesAreOutputsOnly:
    """Pinned deliberately: widening it would put every intermediate var
    into ``result()``, which is built from the same frames."""

    @pytest.mark.asyncio
    async def test_a_consumed_generator_emits_no_frames(self):
        chunks = [
            data
            async for _op, _ctx, data in Operon(_late_output_graph("f_out")).stream(
                {"n": 1}, mode="frames"
            )
            if "chunk" in data
        ]
        assert chunks == []

    @pytest.mark.asyncio
    async def test_an_output_bound_generator_does_emit_frames(self):
        @op
        def produce(n: int):
            for i in range(3):
                yield {"chunk": str(i)}

        with GraphOp(name="bound") as g:
            p = produce(n=PARENT["n"])
            START >> p >> END

        chunks = [
            data["chunk"]
            async for _op, _ctx, data in Operon(g).stream({"n": 1}, mode="frames")
            if "chunk" in data
        ]
        assert chunks == ["0", "1", "2"]

    @pytest.mark.asyncio
    async def test_the_result_is_unaffected_by_any_of_this(self):
        result = await Operon(_late_output_graph("res")).run(inputs={"n": 1})
        assert result["joined"] == "0123"
        assert "chunk" not in result, "intermediate vars must stay out of the result"
