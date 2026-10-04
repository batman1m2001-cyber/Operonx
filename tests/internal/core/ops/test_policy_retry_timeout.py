"""Op-level retry and timeout: ``@op(retry=Retry(...), timeout=Timeout(...))``.

R1 in docs/roadmap/ROADMAP.md, F13 in track1_dogfood.md. Before R1,
``@op(retry=...)`` was a ``TypeError`` and a hung op held its run forever.
"""

import asyncio
import time

import pytest

from operonx import END, START, Operon, graph, op
from operonx.core.policy import TRANSIENT, Retry, Timeout

FAST = dict(initial=0.01, jitter=False)


def _nodes(handle, name):
    return [n for n in handle.trace.nodes if n.op_name == name]


# ── retry ────────────────────────────────────────────────────────────────────

CALLS = {"flaky": 0, "bad": 0, "gen_late": 0, "gen_early": 0, "slow": 0, "after": 0}


@op(retry=Retry(max_attempts=3, **FAST))
async def flaky(x: int) -> dict:
    CALLS["flaky"] += 1
    if CALLS["flaky"] < 3:
        raise ConnectionError("503 from upstream")
    return {"y": x * 10}


@op
def plus_one(y: int) -> dict:
    CALLS["after"] += 1
    return {"z": y + 1}


@graph
def flaky_graph(x):
    f = flaky(x=x)
    p = plus_one(y=f["y"])
    START >> f >> p >> END


async def test_retry_transient_then_success():
    CALLS["flaky"] = 0
    handle = Operon(flaky_graph, params={"x": None}).start({"x": 2})
    out = await handle.result()

    assert out["z"] == 21
    assert "$errors" not in out
    assert CALLS["flaky"] == 3
    attempts = _nodes(handle, "f")
    assert [n.attempt for n in attempts] == [1, 2, 3]
    assert [n.status for n in attempts] == ["retried", "retried", "ok"]
    assert "ConnectionError: 503 from upstream" in attempts[0].error
    # The last attempt keeps the op_id the next op's upstream points at.
    (after,) = _nodes(handle, "p")
    assert after.upstreams[0].from_op_id == attempts[-1].op_id
    assert len({n.op_id for n in attempts}) == 3


@op(retry=Retry(max_attempts=3, **FAST))
async def bad_input(x: int) -> dict:
    CALLS["bad"] += 1
    raise ValueError("not a number")


@graph
def bad_graph(x):
    b = bad_input(x=x)
    START >> b >> END


async def test_retry_not_on_valueerror():
    CALLS["bad"] = 0
    out = await Operon(bad_graph, params={"x": None}).run({"x": 1})
    assert CALLS["bad"] == 1
    assert "not a number" in str(out["$errors"]) and "ValueError" in str(out["$errors"])


async def test_retry_gives_up_after_max_attempts():
    calls = []

    @op(retry=Retry(max_attempts=2, **FAST))
    async def always_down(x: int) -> dict:
        calls.append(x)
        raise TimeoutError("upstream timed out")

    @graph
    def g(x):
        a = always_down(x=x)
        p = plus_one(y=a["y"])
        START >> a >> p >> END

    CALLS["after"] = 0
    out = await Operon(g, params={"x": None}).run({"x": 1})
    assert len(calls) == 2
    assert CALLS["after"] == 0
    assert "upstream timed out" in str(out["$errors"])


async def test_retry_backoff_spacing():
    stamps = []

    @op(retry=Retry(max_attempts=4, initial=0.05, backoff=2.0, jitter=False))
    async def spaced(x: int) -> dict:
        stamps.append(time.perf_counter())
        raise ConnectionError("down")

    @graph
    def g(x):
        s = spaced(x=x)
        START >> s >> END

    await Operon(g, params={"x": None}).run({"x": 1})
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(gaps) == 3
    for gap, want in zip(gaps, (0.05, 0.1, 0.2)):
        assert want <= gap < want + 0.08, gaps


def test_retry_delay_jitter_bounds():
    r = Retry(initial=1.0, backoff=3.0, max_interval=5.0)
    for attempt, full in ((1, 1.0), (2, 3.0), (3, 5.0), (9, 5.0)):
        for _ in range(50):
            assert full / 2 <= r.delay(attempt) <= full
    assert Retry(initial=1.0, jitter=False).delay(2) == 2.0


def test_transient_classifier():
    class Http(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    assert TRANSIENT(TimeoutError()) and TRANSIENT(asyncio.TimeoutError())
    assert TRANSIENT(ConnectionResetError()) and TRANSIENT(Http(503)) and TRANSIENT(Http(429))
    assert not TRANSIENT(Http(400)) and not TRANSIENT(ValueError()) and not TRANSIENT(KeyError())


@op(retry=Retry(max_attempts=3, **FAST))
async def gen_fails_late(n: int):
    CALLS["gen_late"] += 1
    yield {"item": 1}
    raise ConnectionError("dropped mid-stream")


@op
def keep(item: int) -> dict:
    return {"kept": item}


@graph
def gen_late_graph(n):
    g = gen_fails_late(n=n)
    k = keep(item=g["item"])
    START >> g >> k >> END


async def test_generator_not_retried_after_first_yield():
    CALLS["gen_late"] = 0
    out = await Operon(gen_late_graph, params={"n": None}).run({"n": 1})
    assert CALLS["gen_late"] == 1
    assert out["kept"] == 1  # the one item, not repeated
    assert "dropped mid-stream" in str(out["$errors"])


@op(retry=Retry(max_attempts=3, **FAST))
async def gen_fails_early(n: int):
    CALLS["gen_early"] += 1
    if CALLS["gen_early"] < 2:
        raise ConnectionError("not connected yet")
    for i in range(n):
        yield {"item": i}


@graph
def gen_early_graph(n):
    g = gen_fails_early(n=n)
    k = keep(item=g["item"])
    START >> g >> k >> END


async def test_generator_retried_before_first_yield():
    CALLS["gen_early"] = 0
    out = await Operon(gen_early_graph, params={"n": None}).run({"n": 3})
    assert CALLS["gen_early"] == 2
    assert out["kept"] == [0, 1, 2]
    assert "$errors" not in out


async def test_sync_op_with_retry_runs_and_retries():
    calls = []

    @op(retry=Retry(max_attempts=2, **FAST))
    def sync_flaky(x: int) -> dict:
        calls.append(x)
        if len(calls) == 1:
            raise ConnectionError("blip")
        return {"y": x}

    @graph
    def g(x):
        s = sync_flaky(x=x)
        p = plus_one(y=s["y"])
        START >> s >> p >> END

    out = await Operon(g, params={"x": None}).run({"x": 7})
    assert out["z"] == 8 and len(calls) == 2  # consumed downstream


# ── timeout ──────────────────────────────────────────────────────────────────


@op(timeout=Timeout(run=0.2))
async def slow(x: int) -> dict:
    CALLS["slow"] += 1
    await asyncio.sleep(5)
    return {"y": x}


@graph
def slow_graph(x):
    s = slow(x=x)
    p = plus_one(y=s["y"])
    START >> s >> p >> END


async def test_timeout_records_and_skips_successors():
    CALLS["after"] = 0
    t0 = time.perf_counter()
    out = await Operon(slow_graph, params={"x": None}).run({"x": 1})
    took = time.perf_counter() - t0

    assert took < 0.4, took
    (error,) = map(str, out["$errors"].values())
    assert "TimeoutError" in error and "Timeout(run=0.2)" in error
    assert "z" not in out and CALLS["after"] == 0


async def test_timeout_records_and_retries():
    CALLS["slow"] = 0
    retried = slow(x=1, retry=Retry(max_attempts=2, **FAST))  # outside a graph: just the op
    assert retried._policy.retry.max_attempts == 2

    @graph
    def g(x):
        s = slow(x=x, retry=Retry(max_attempts=2, **FAST))
        START >> s >> END

    handle = Operon(g, params={"x": None}).start({"x": 1})
    out = await handle.result()
    assert CALLS["slow"] == 2
    assert [n.status for n in _nodes(handle, "s")] == ["retried", "error"]
    assert "TimeoutError" in str(out["$errors"])


async def test_idle_timeout_generator():
    @op(timeout=Timeout(idle=0.2))
    async def stalls(n: int):
        for i in range(n):
            if i == 2:
                await asyncio.sleep(5)
            yield {"item": i}

    @graph
    def g(n):
        s = stalls(n=n)
        k = keep(item=s["item"])
        START >> s >> k >> END

    t0 = time.perf_counter()
    out = await Operon(g, params={"n": None}).run({"n": 4})
    assert time.perf_counter() - t0 < 0.5
    assert out["kept"] == [0, 1]
    assert "Timeout(idle=0.2)" in str(out["$errors"])


async def test_run_timeout_spans_a_generator():
    @op(timeout=Timeout(run=0.25))
    async def ticker(n: int):
        for i in range(n):
            await asyncio.sleep(0.1)
            yield {"item": i}

    @graph
    def g(n):
        t = ticker(n=n)
        k = keep(item=t["item"])
        START >> t >> k >> END

    out = await Operon(g, params={"n": None}).run({"n": 10})
    assert out["kept"] == [0, 1]
    assert "TimeoutError" in str(out["$errors"])


async def test_cpu_timeout_abandons_thread():
    @op(bound="cpu", timeout=Timeout(run=0.2))
    def crunch(x: int) -> dict:
        time.sleep(1.0)
        return {"y": x}

    @graph
    def g(x):
        c = crunch(x=x)
        START >> c >> END

    t0 = time.perf_counter()
    out = await Operon(g, params={"x": None}).run({"x": 1})
    assert time.perf_counter() - t0 < 0.5
    assert "TimeoutError" in str(out["$errors"])


async def test_timeout_does_not_fire_on_a_fast_op():
    @op(timeout=Timeout(run=1.0), retry=Retry(max_attempts=2, **FAST))
    async def quick(x: int) -> dict:
        return {"y": x + 1}

    @graph
    def g(x):
        q = quick(x=x)
        p = plus_one(y=q["y"])
        START >> q >> p >> END

    handle = Operon(g, params={"x": None}).start({"x": 1})
    out = await handle.result()
    assert out["z"] == 3 and "$errors" not in out  # consumed downstream
    assert [n.attempt for n in _nodes(handle, "q")] == [1]


async def test_outer_cancel_is_not_a_timeout():
    started = asyncio.Event()

    @op(timeout=Timeout(run=5))
    async def waits(x: int) -> dict:
        started.set()
        await asyncio.sleep(5)
        return {"y": x}

    @graph
    def g(x):
        w = waits(x=x)
        START >> w >> END

    handle = Operon(g, params={"x": None}).start({"x": 1})
    await started.wait()
    handle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handle.result()
    assert handle.errors == {}


# ── construction ─────────────────────────────────────────────────────────────


def test_timeout_refused_on_inline_op():
    with pytest.raises(ValueError, match='bound="cpu"'):

        @op(timeout=Timeout(run=1))
        def plain(x: int) -> dict:
            return {"y": x}

        plain(x=1)


def test_idle_refused_on_batch_op():
    with pytest.raises(ValueError, match="idle"):

        @op(timeout=Timeout(idle=1))
        async def batch(x: int) -> dict:
            return {"y": x}

        batch(x=1)


def test_bare_numbers_refused():
    with pytest.raises(TypeError, match=r"Retry\(max_attempts=3\)"):
        op(retry=3)
    with pytest.raises(TypeError, match=r"Timeout\(run=1\)"):
        op(timeout=1)
    with pytest.raises(ValueError):
        Retry(max_attempts=0)
    with pytest.raises(ValueError):
        Timeout()
    with pytest.raises(TypeError):
        Retry(on="ConnectionError")


@op
async def fetch(url: str, timeout: float = 1.0) -> dict:
    return {"got": f"{url}@{timeout}"}


async def test_timeout_param_stays_an_input():
    @graph
    def g(url):
        f = fetch(url=url, timeout=7.5)  # a float: the function's own argument
        START >> f >> END

    out = await Operon(g, params={"url": None}).run({"url": "u"})
    assert out["got"] == "u@7.5"


async def test_per_call_override():
    calls = []

    @op(retry=Retry(max_attempts=5, **FAST))
    async def shaky(x: int) -> dict:
        calls.append(x)
        raise ConnectionError("down")

    @graph
    def g(x):
        s = shaky(x=x, retry=Retry(max_attempts=2, **FAST))
        START >> s >> END

    await Operon(g, params={"x": None}).run({"x": 1})
    assert len(calls) == 2


# ── a subgraph as an op ─────────────────────────────────────────────────────

DONE = {"inner": 0}


@op
async def inner_slow(x: int) -> dict:
    await asyncio.sleep(1.0)
    DONE["inner"] += 1
    return {"y": x}


@graph
def slow_sub(x):
    s = inner_slow(x=x)
    START >> s >> END


async def test_graph_timeout_cancels_its_ops():
    @graph
    def outer(x):
        sub = slow_sub(x=x, timeout=Timeout(run=0.2))
        p = plus_one(y=sub["y"])
        START >> sub >> p >> END

    DONE["inner"] = 0
    CALLS["after"] = 0
    t0 = time.perf_counter()
    out = await Operon(outer, params={"x": None}).run({"x": 1})
    assert time.perf_counter() - t0 < 0.5
    assert any(
        k.endswith(".sub") and "Timeout(run=0.2)" in str(v) for k, v in out["$errors"].items()
    )
    assert CALLS["after"] == 0
    await asyncio.sleep(1.0)
    assert DONE["inner"] == 0  # cancelled, not left running


def test_graph_refuses_retry_and_idle():
    with pytest.raises(TypeError, match="retry"):
        slow_sub(x=1, retry=Retry())
    with pytest.raises(ValueError, match="idle"):
        slow_sub(x=1, timeout=Timeout(idle=1))


# ── a retried op's output is consumed downstream ────────────────────────────


async def test_retried_outputs_reach_their_consumers():
    """Batch and generator retried, read by the next op, in a fail-fast run
    whose graph also has an error edge — every R1 path at once."""
    calls = {"batch": 0, "gen": 0, "handler": 0}

    @op(retry=Retry(max_attempts=3, **FAST))
    async def lookup(x: int) -> dict:
        calls["batch"] += 1
        if calls["batch"] < 3:
            raise ConnectionError("blip")
        return {"y": x * 10}

    @op(timeout=Timeout(run=1.0))
    async def consume(y: int) -> dict:
        return {"z": y + 1}

    @op(retry=Retry(max_attempts=2, **FAST))
    async def stream(n: int):
        calls["gen"] += 1
        if calls["gen"] == 1:
            raise ConnectionError("not connected yet")
        for i in range(n):
            yield {"item": i}

    @op
    def per_item(item: int) -> dict:
        return {"kept": item * 2}

    @op
    def handler(error: str) -> dict:
        calls["handler"] += 1
        return {"z": -1}

    @graph
    def g(x):
        look = lookup(x=x)
        c = consume(y=look["y"])
        st = stream(n=x)
        k = per_item(item=st["item"])
        h = handler()
        START >> [look, st]
        look >> c >> END
        st >> k >> END
        look.on_error(h)
        h >> END

    handle = Operon(g, params={"x": None}, errors="raise").start({"x": 3})
    out = await handle.result()

    assert out["z"] == 31  # the third attempt's y reached consume
    assert out["kept"] == [0, 2, 4]  # the second attempt's items reached per_item
    assert calls == {"batch": 3, "gen": 2, "handler": 0}
    assert "$errors" not in out
    (c_node,) = [n for n in handle.trace.nodes if n.op_name == "c"]
    final = [n for n in handle.trace.nodes if n.op_name == "look"][-1]
    assert c_node.upstreams[0].from_op_id == final.op_id and final.attempt == 3
