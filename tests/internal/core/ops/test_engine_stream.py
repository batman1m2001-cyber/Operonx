"""Tests for engine.start() — streaming output API (replaced engine.stream())."""

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op


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

        engine = Operon(g)
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

        engine = Operon(g)
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

        engine = Operon(g)
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

        engine = Operon(g)
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

        engine = Operon(g)
        frames = []
        async for op_name, ctx, data in engine.start(inputs={"items": []}):
            frames.append(data)

        assert len(frames) == 0


# =============================================================================
# Test 5: collect() — last-value-wins aggregation
# =============================================================================


class TestCollect:
    @pytest.mark.asyncio
    async def test_collect_groups_by_key(self):
        """handle.collect() merges frames by key into lists."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="collect_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Operon(g)
        result = await engine.start(inputs={"items": [1, 2, 3]}).collect()

        assert result["result"] == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_collect_flat_mode(self):
        """handle.collect('flat') returns ordered list of frame dicts."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="flat_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Operon(g)
        result = await engine.start(inputs={"items": [1, 2, 3]}).collect("flat")

        assert isinstance(result, list)
        assert len(result) == 3
        assert sorted(r["result"] for r in result) == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_collect_unwrap(self):
        """handle.collect(unwrap=True) unwraps single-item lists to scalars."""

        with GraphOp(name="unwrap_test") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        engine = Operon(g)
        result = await engine.start(inputs={"x": 5}).collect(unwrap=True)

        assert result["result"] == 10  # single frame → scalar

    @pytest.mark.asyncio
    async def test_run_unwraps_single_frames(self):
        """engine.run() unwraps single-frame outputs to scalars."""

        with GraphOp(name="run_scalar") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        engine = Operon(g)
        result = await engine.run(inputs={"x": 5})

        assert result["result"] == 10
        assert "$state" in result

    @pytest.mark.asyncio
    async def test_run_returns_lists_for_generators(self):
        """engine.run() returns lists for generator outputs."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="run_gen") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Operon(g)
        result = await engine.run(inputs={"items": [1, 2, 3]})

        assert result["result"] == [2, 4, 6]
        assert "$state" in result
