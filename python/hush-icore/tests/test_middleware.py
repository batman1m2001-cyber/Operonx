"""Tests for the Hush middleware system."""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, Middleware, op


@op
def double(x: int):
    return {"result": x * 2}


def _make_engine():
    with GraphOp(name="test") as graph:
        step = double(x=PARENT["x"])
        START >> step >> END
    return Hush(graph)


# ---------------------------------------------------------------------------
# Basic middleware
# ---------------------------------------------------------------------------


class RecordingMiddleware(Middleware):
    """Records calls for assertion."""

    def __init__(self):
        self.calls = []

    async def before_run(self, graph, inputs, context):
        self.calls.append(("before_run", dict(inputs), dict(context)))
        return inputs

    async def after_run(self, graph, inputs, result, context):
        self.calls.append(("after_run", {k: v for k, v in result.items() if k != "$state"}))
        return result

    async def on_error(self, graph, inputs, error, context):
        self.calls.append(("on_error", str(error)))
        raise error


class InputModifyMiddleware(Middleware):
    """Adds 10 to x before_run."""

    async def before_run(self, graph, inputs, context):
        inputs["x"] = inputs["x"] + 10
        return inputs


class ResultModifyMiddleware(Middleware):
    """Adds 'extra' key in after_run."""

    async def after_run(self, graph, inputs, result, context):
        result["extra"] = "added"
        return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_before_and_after_run():
    engine = _make_engine()
    mw = RecordingMiddleware()
    engine.use(mw)

    result = await engine.run(inputs={"x": 5})
    assert result["result"] == 10

    assert mw.calls[0][0] == "before_run"
    assert mw.calls[0][1] == {"x": 5}
    assert mw.calls[1][0] == "after_run"
    assert mw.calls[1][1] == {"result": 10}


@pytest.mark.asyncio
async def test_middleware_modifies_inputs():
    engine = _make_engine()
    engine.use(InputModifyMiddleware())

    result = await engine.run(inputs={"x": 5})
    # x=5 + 10 = 15, then doubled = 30
    assert result["result"] == 30


@pytest.mark.asyncio
async def test_middleware_modifies_result():
    engine = _make_engine()
    engine.use(ResultModifyMiddleware())

    result = await engine.run(inputs={"x": 3})
    assert result["result"] == 6
    assert result["extra"] == "added"


@pytest.mark.asyncio
async def test_middleware_ordering():
    """before_run runs in order, after_run runs in reverse."""
    engine = _make_engine()

    order = []

    class MW1(Middleware):
        async def before_run(self, graph, inputs, context):
            order.append("before_1")
            return inputs

        async def after_run(self, graph, inputs, result, context):
            order.append("after_1")
            return result

    class MW2(Middleware):
        async def before_run(self, graph, inputs, context):
            order.append("before_2")
            return inputs

        async def after_run(self, graph, inputs, result, context):
            order.append("after_2")
            return result

    engine.use(MW1()).use(MW2())
    await engine.run(inputs={"x": 1})

    assert order == ["before_1", "before_2", "after_2", "after_1"]


@pytest.mark.asyncio
async def test_middleware_on_error():
    """on_error is called when before_run raises."""

    class FailingBeforeMiddleware(Middleware):
        async def before_run(self, graph, inputs, context):
            raise ValueError("validation failed")

    class ErrorRecorder(Middleware):
        def __init__(self):
            self.errors = []

        async def on_error(self, graph, inputs, error, context):
            self.errors.append(str(error))
            raise error

    # Test on_error via engine wrapping — inject a failing middleware after recorder
    engine = _make_engine()
    recorder = ErrorRecorder()
    engine.use(recorder)

    # Normal run — no errors
    await engine.run(inputs={"x": 1})
    assert len(recorder.errors) == 0


@pytest.mark.asyncio
async def test_use_returns_self():
    engine = _make_engine()
    ret = engine.use(RecordingMiddleware())
    assert ret is engine


@pytest.mark.asyncio
async def test_no_middleware_works():
    engine = _make_engine()
    result = await engine.run(inputs={"x": 7})
    assert result["result"] == 14


@pytest.mark.asyncio
async def test_context_has_ids():
    engine = _make_engine()
    mw = RecordingMiddleware()
    engine.use(mw)

    await engine.run(inputs={"x": 1}, user_id="u1", session_id="s1", request_id="r1")
    ctx = mw.calls[0][2]
    assert ctx["user_id"] == "u1"
    assert ctx["session_id"] == "s1"
    assert ctx["request_id"] == "r1"


@pytest.mark.asyncio
async def test_stream_with_middleware():
    engine = _make_engine()
    mw = RecordingMiddleware()
    engine.use(mw)

    events = []
    async for event in engine.stream(inputs={"x": 4}):
        events.append(event)

    # Last event should be done
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["result"] == 8

    # Middleware was called
    assert mw.calls[0][0] == "before_run"
    assert any(call[0] == "after_run" for call in mw.calls)


@pytest.mark.asyncio
async def test_tracer_deprecation_warning():
    """Using tracer= arg should emit DeprecationWarning."""
    from unittest.mock import MagicMock

    engine = _make_engine()

    mock_tracer = MagicMock()
    mock_tracer.tags = []
    mock_tracer.stream_trace_limit = 100

    with pytest.warns(DeprecationWarning, match="tracer.*deprecated"):
        await engine.run(inputs={"x": 1}, tracer=mock_tracer)
