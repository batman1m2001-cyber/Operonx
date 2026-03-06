"""Tests for engine.stream() — real-time streaming event delivery."""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op


@op
def double(x: int):
    return {"result": x * 2}


# =============================================================================
# Test 1: stream() on batch graph yields single done event
# =============================================================================


class TestStreamBatchGraph:
    @pytest.mark.asyncio
    async def test_batch_graph_yields_done_only(self):
        """Non-streaming graph via stream() yields only a 'done' event."""
        with GraphOp(name="batch") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        engine = Hush(g)
        events = []
        async for event in engine.stream(inputs={"x": 5}):
            events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "done"
        assert events[0]["data"]["result"] == 10
        assert "$state" in events[0]["data"]


# =============================================================================
# Test 2: stream() on generator graph yields token + done events
# =============================================================================


class TestStreamGeneratorGraph:
    @pytest.mark.asyncio
    async def test_generator_yields_token_events(self):
        """Generator graph via stream() yields token events then done."""

        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        with GraphOp(name="stream_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["value"])
            START >> s >> d >> END

        engine = Hush(g)
        events = []
        async for event in engine.stream(inputs={"items": [1, 2, 3]}):
            events.append(event)

        # Should have token events from the generator + 1 done event
        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(token_events) == 3
        assert len(done_events) == 1

        # Token events come from the source generator
        token_values = [e["data"]["value"] for e in token_events]
        assert sorted(token_values) == [1, 2, 3]

        # Done event has accumulated results
        assert sorted(done_events[0]["data"]["result"]) == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_generator_token_events_have_op_name(self):
        """Token events include the op name."""

        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        with GraphOp(name="test") as g:
            s = source(items=PARENT["items"])
            START >> s >> END

        engine = Hush(g)
        events = []
        async for event in engine.stream(inputs={"items": [1]}):
            events.append(event)

        token_events = [e for e in events if e["type"] == "token"]
        assert len(token_events) == 1
        assert token_events[0]["op"] == "s"


# =============================================================================
# Test 3: stream() with async generator
# =============================================================================


class TestStreamAsyncGenerator:
    @pytest.mark.asyncio
    async def test_async_generator_streams(self):
        """Async generator works with stream()."""

        @op
        async def async_source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="async_stream") as g:
            s = async_source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Hush(g)
        events = []
        async for event in engine.stream(inputs={"items": [10, 20]}):
            events.append(event)

        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(token_events) == 2
        assert len(done_events) == 1
        assert sorted(done_events[0]["data"]["result"]) == [20, 40]


# =============================================================================
# Test 4: stream() with empty generator
# =============================================================================


class TestStreamEmptyGenerator:
    @pytest.mark.asyncio
    async def test_empty_generator_yields_done_only(self):
        """Empty generator yields no tokens, just done."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="empty_stream") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        engine = Hush(g)
        events = []
        async for event in engine.stream(inputs={"items": []}):
            events.append(event)

        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(token_events) == 0
        assert len(done_events) == 1
        assert done_events[0]["data"]["result"] == []


# =============================================================================
# Test 5: run() still works unchanged (backward compat)
# =============================================================================


class TestRunUnchanged:
    @pytest.mark.asyncio
    async def test_run_returns_accumulated_result(self):
        """engine.run() still returns accumulated dict, no streaming."""

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

        assert result["result"] == [2, 4, 6]
        assert "$state" in result
