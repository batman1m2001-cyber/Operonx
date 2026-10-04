"""R2: ``run_context()`` — what an op body can know about its run.

Information only: ids, the attempt, the deadline, the caller's typed
``context``, and an ``idempotency_key`` that is stable across retries and
differs between runs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from operonx import END, START, Operon, Retry, RunContext, Timeout, graph, op, run_context
from operonx.core.runtime import invocation_key

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class Tenant:
    name: str


SEEN: list = []


@op
async def look() -> dict:
    SEEN.append(run_context())
    return {"done": True}


@graph
def one():
    a = look()
    START >> a >> END


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()
    yield
    SEEN.clear()


@pytest.mark.asyncio
async def test_run_context_fields():
    handle = Operon(one).start({}, trace_id="run-1", session_id="cust-7", context=Tenant("acme"))
    await handle.result()
    (rc,) = SEEN
    assert isinstance(rc, RunContext)
    assert rc.run_id == "run-1"
    assert rc.thread_id == "cust-7"
    assert rc.op_path == "one.a"
    assert rc.ctx == ("main",)
    assert rc.attempt == 1
    assert rc.deadline is None and rc.remaining is None
    assert rc.context == Tenant("acme")
    assert rc.idempotency_key == invocation_key("run-1", "one.a", ("main",))


@pytest.mark.asyncio
async def test_run_context_without_session_or_context():
    out = await Operon(one).run({})
    (rc,) = SEEN
    assert rc.thread_id is None, "no session_id given: the run belongs to no thread"
    assert rc.context is None
    assert rc.run_id == out["$state"].request_id


def test_run_context_outside_a_run_is_none():
    assert run_context() is None


@pytest.mark.asyncio
async def test_run_context_is_read_only():
    await Operon(one).run({})
    with pytest.raises(AttributeError):
        SEEN[0].attempt = 3  # type: ignore[misc]


ATTEMPTS: list = []


@op(retry=Retry(max_attempts=3, initial=0.001, jitter=False))
async def flaky() -> dict:
    rc = run_context()
    ATTEMPTS.append((rc.attempt, rc.idempotency_key))
    if rc.attempt < 3:
        raise ConnectionError("503")
    return {"ok": True}


@graph
def retried():
    f = flaky()
    START >> f >> END


@pytest.mark.asyncio
async def test_idempotency_key_stable_across_retries_and_runs_differ():
    ATTEMPTS.clear()
    engine = Operon(retried)
    await engine.run({}, trace_id="r-1")
    await engine.run({}, trace_id="r-2")
    first, second = ATTEMPTS[:3], ATTEMPTS[3:]
    assert [a for a, _ in first] == [1, 2, 3]
    assert len({k for _, k in first}) == 1, "a retried attempt reuses its key"
    assert first[0][1] != second[0][1], "another run, another key"


@op
def per_item(n: int):
    for i in range(n):
        yield {"i": i}


@op
async def keyed(i: int) -> dict:
    return {"key": run_context().idempotency_key, "ctx": run_context().ctx}


@graph
def fan(n):
    g = per_item(n=n)
    k = keyed(i=g["i"].parallel())
    START >> g >> k >> END


@pytest.mark.asyncio
async def test_each_fanned_out_invocation_has_its_own_key():
    out = await Operon(fan, params={"n": None}).run({"n": 3}, trace_id="fan")
    assert sorted(out["ctx"]) == [("main", "[0]"), ("main", "[1]"), ("main", "[2]")]
    assert len(set(out["key"])) == 3


@op(timeout=Timeout(run=5))
async def bounded() -> dict:
    rc = run_context()
    return {"deadline": rc.deadline, "remaining": rc.remaining, "now": time.monotonic()}


@graph
def timed():
    b = bounded()
    START >> b >> END


@pytest.mark.asyncio
async def test_run_context_deadline():
    out = await Operon(timed).run({})
    assert out["deadline"] == pytest.approx(out["now"] + 5, abs=0.5)
    assert 4.5 < out["remaining"] <= 5


@op(bound="cpu")
def in_a_thread() -> dict:
    return {"path": run_context().op_path}


@graph
def threaded():
    t = in_a_thread()
    START >> t >> END


@pytest.mark.asyncio
async def test_run_context_reaches_a_cpu_bound_op():
    out = await Operon(threaded).run({})
    assert out["path"] == "threaded.t"


def test_invocation_key_shape():
    key = invocation_key("r", "g.op", ("main", "[0]"))
    assert len(key) == 32 and int(key, 16) >= 0
    assert key != invocation_key("r", "g.op", ("main", "[1]"))
    assert key != invocation_key("r", "g.op2", ("main", "[0]"))
    # no ambiguity from joining: ("a.b",) vs ("a", "b")
    assert invocation_key("r", "g", ("a.b",)) != invocation_key("r", "g", ("a", "b"))
