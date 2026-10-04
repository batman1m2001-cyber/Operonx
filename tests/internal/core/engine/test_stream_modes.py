"""R2: ``engine.stream(mode=[...])`` yields ``(mode, chunk)``, and the
``tasks`` mode reports each op invocation (and each child execution) as
``TaskStarted`` / ``TaskFinished`` / ``TaskFailed``, with its attempt.
"""

from __future__ import annotations

import pytest

from operonx import END, START, EmitOp, InterruptOp, Operon, Retry, child, graph, op
from operonx.checkpoint import CustomEvent, InterruptEvent
from operonx.core.runtime import TaskFailed, TaskFinished, TaskStarted

pytestmark = pytest.mark.unit


@op
async def seed(n: int) -> dict:
    return {"n": n}


@op
async def tokens(n: int):
    for i in range(n):
        yield {"token": f"t{i}"}


@op
async def agent(n: int) -> dict:
    async with child("model", inputs={"n": n}) as c:
        c.outputs = {"reply": "ok"}
    return {"answer": "ok"}


@graph
def pipeline(n):
    s = seed(n=n)
    t = tokens(n=s["n"])
    a = agent(n=s["n"])
    e = EmitOp(channel="progress", payload=s["n"])
    START >> s >> t >> END
    s >> a >> END
    s >> e >> END


def _engine():
    return Operon(pipeline, params={"n": None})


@pytest.mark.asyncio
async def test_stream_multi_mode():
    updates = [u async for u in _engine().stream({"n": 2}, mode="updates")]
    got = [pair async for pair in _engine().stream({"n": 2}, mode=["updates", "custom", "tasks"])]
    assert all(isinstance(p, tuple) and len(p) == 2 for p in got)
    assert {m for m, _ in got} == {"updates", "custom", "tasks"}
    assert [c for m, c in got if m == "updates"] == updates, "a mode reads the same alone or not"
    (custom,) = [c for m, c in got if m == "custom"]
    assert isinstance(custom, CustomEvent) and custom.payload == 2
    assert all(
        isinstance(c, (TaskStarted, TaskFinished, TaskFailed)) for m, c in got if m == "tasks"
    )


@pytest.mark.asyncio
async def test_stream_a_list_of_one_mode_still_pairs():
    got = [p async for p in _engine().stream({"n": 1}, mode=["frames"])]
    assert got and all(m == "frames" and len(c) == 3 for m, c in got)


@pytest.mark.asyncio
async def test_stream_tasks_mode():
    events = [e async for e in _engine().stream({"n": 2}, mode="tasks")]
    started = [e.op for e in events if isinstance(e, TaskStarted)]
    finished = [e.op for e in events if isinstance(e, TaskFinished)]
    ops = {"engine.s", "engine.t", "engine.a", "engine.e", "engine.a.model"}
    names = {op.split(".", 1)[1] for op in started}
    assert names == {op.split(".", 1)[1] for op in ops}, started
    assert sorted(started) == sorted(finished), (
        "one start and one end per invocation, not per yield"
    )
    for name in started:
        assert events.index(
            next(e for e in events if isinstance(e, TaskStarted) and e.op == name)
        ) < (events.index(next(e for e in events if isinstance(e, TaskFinished) and e.op == name)))
    model = next(e for e in events if isinstance(e, TaskFinished) and e.op.endswith(".a.model"))
    assert model.ctx == ("main", "model[0]") and model.attempt == 1 and model.duration_ms >= 0


TRIES: list = []


@op(retry=Retry(max_attempts=2, initial=0.001, jitter=False))
async def flaky() -> dict:
    TRIES.append(1)
    if len(TRIES) == 1:
        raise ConnectionError("503 from the CRM")
    return {"ok": True}


@op
async def broken() -> dict:
    raise ValueError("bad input")


@graph
def failing():
    f = flaky()
    b = broken()
    START >> f >> END
    START >> b >> END


@pytest.mark.asyncio
async def test_stream_tasks_mode_reports_failures_and_attempts():
    TRIES.clear()
    events = [e async for e in Operon(failing).stream({}, mode="tasks")]
    flaky_events = [(type(e).__name__, e.attempt) for e in events if e.op.endswith(".f")]
    assert flaky_events == [
        ("TaskStarted", 1),
        ("TaskFailed", 1),
        ("TaskStarted", 2),
        ("TaskFinished", 2),
    ]
    retried = next(e for e in events if isinstance(e, TaskFailed) and e.op.endswith(".f"))
    assert retried.retrying and "ConnectionError: 503 from the CRM" in retried.error
    (bad,) = [e for e in events if isinstance(e, TaskFailed) and e.op.endswith(".b")]
    assert bad.error == "ValueError: bad input" and not bad.retrying and not bad.cancelled


@graph
def asks():
    q = InterruptOp(payload="ok?")
    START >> q >> END


@pytest.mark.asyncio
async def test_interrupts_go_to_their_own_mode_when_asked_for():
    got = []
    async for mode, chunk in Operon(asks).stream({}, mode=["updates", "interrupts"]):
        got.append((mode, chunk))
        if isinstance(chunk, InterruptEvent):
            chunk.resume("yes")
    events = [(m, c) for m, c in got if isinstance(c, InterruptEvent)]
    assert [m for m, _ in events] == ["interrupts"], "once, in its own mode"


@pytest.mark.parametrize(
    "mode, message",
    [
        (["updates", "nope"], "valid modes"),
        (["updates", "updates"], "more than once"),
        ([], "at least one"),
        ("bogus", "valid modes"),
    ],
)
@pytest.mark.asyncio
async def test_stream_rejects_unknown_mode(mode, message):
    with pytest.raises(ValueError, match=message):
        async for _ in _engine().stream({"n": 1}, mode=mode):
            pass
