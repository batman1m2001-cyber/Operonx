"""Tests for engine.start() — streaming output API (replaced engine.stream())."""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op


@op
def double(x: int):
    return {"result": x * 2}


# =============================================================================
# Test 1: start() on batch graph yields one frame
# =============================================================================


class TestStartBatchGraph:
    @pytest.mark.asyncio
    async def test_batch_graph_yields_one_frame(self):
        """Batch graph via start() yields one frame with output."""
        with GraphOp(name="batch") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        engine = Hush(g)
        frames = []
        async for op_name, ctx, data in engine.start(inputs={"x": 5}):
            frames.append((op_name, ctx, data))

        assert len(frames) == 1
        assert frames[0][0] == "d"
        assert frames[0][2]["result"] == 10


# =============================================================================
# Test 2: start() on generator graph yields one frame per item
# =============================================================================


class TestStartGeneratorGraph:
    @pytest.mark.asyncio
    async def test_generator_yields_one_frame_per_item(self):
        """Generator graph via start() yields one frame per item."""

        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        with GraphOp(name="stream_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["value"])
            START >> s >> d >> END

        engine = Hush(g)
        frames = []
        async for op_name, ctx, data in engine.start(inputs={"items": [1, 2, 3]}):
            frames.append((op_name, ctx, data))

        assert len(frames) == 3
        assert sorted(f[2]["result"] for f in frames) == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_frame_has_op_name(self):
        """Frames include the op name."""

        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        with GraphOp(name="test") as g:
            s = source(items=PARENT["items"])
            START >> s >> END

        engine = Hush(g)
        frame_ops = []
        async for op_name, ctx, data in engine.start(inputs={"items": [1]}):
            frame_ops.append(op_name)

        assert len(frame_ops) == 1
        assert frame_ops[0] == "s"


# =============================================================================
# Test 3: start() with async generator
# =============================================================================


class TestStartAsyncGenerator:
    @pytest.mark.asyncio
    async def test_async_generator_streams(self):
        """Async generator works with start()."""

        @op
        async def async_source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="async_stream") as g:
            s = async_source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Hush(g)
        frames = []
        async for op_name, ctx, data in engine.start(inputs={"items": [10, 20]}):
            frames.append(data)

        assert len(frames) == 2
        assert sorted(f["result"] for f in frames) == [20, 40]


# =============================================================================
# Test 4: start() with empty generator
# =============================================================================


class TestStartEmptyGenerator:
    @pytest.mark.asyncio
    async def test_empty_generator_yields_no_frames(self):
        """Empty generator: handle yields no frames."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="empty_stream") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Hush(g)
        frames = []
        async for op_name, ctx, data in engine.start(inputs={"items": []}):
            frames.append(data)

        assert len(frames) == 0


# =============================================================================
# Test 5: collect() — last-value-wins aggregation
# =============================================================================


class TestCollect:
    @pytest.mark.asyncio
    async def test_collect_returns_last_value(self):
        """handle.collect() merges frames, last-value-wins for generators."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="collect_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Hush(g)
        result = await engine.start(inputs={"items": [1, 2, 3]}).collect()

        assert result["result"] == 6  # last-value-wins

    @pytest.mark.asyncio
    async def test_run_returns_last_value(self):
        """engine.run() returns merged dict (last-value-wins) + $state."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="run_compat") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Hush(g)
        result = await engine.run(inputs={"items": [1, 2, 3]})

        assert result["result"] == 6  # last-value-wins
        assert "$state" in result
