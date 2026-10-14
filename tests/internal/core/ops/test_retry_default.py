"""A run's default retry: ``Operon(g, retry=Retry(...))``, ``@op(retry=False)``.

docs/RETRY_PLAN.md R2. The default is looked at only when an op without a
policy of its own fails, so an op that does not fail runs as before. A plain
``def`` op, which the scheduler's main loop runs inline, waits out its
backoff in a task of its own: the rest of the run keeps moving.
"""

import asyncio
import time

import pytest

from operonx import END, START, Operon, graph, op
from operonx.core.policy import Retry
from operonx.durable import MemoryJournal

pytestmark = pytest.mark.unit

WAIT = 0.25
DEFAULT = Retry(max_attempts=3, initial=WAIT, jitter=False)
FAST = Retry(max_attempts=3, initial=0.01, jitter=False)
CALLS: dict = {}
AT: dict = {}


def _call(name: str) -> int:
    CALLS[name] = CALLS.get(name, 0) + 1
    AT.setdefault(name, []).append(time.perf_counter())
    return CALLS[name]


@pytest.fixture(autouse=True)
def _reset():
    CALLS.clear()
    AT.clear()


@op
async def flaky_async(x: int) -> dict:
    if _call("flaky_async") == 1:
        raise ConnectionError("503 from upstream")
    return {"a": x}


@op
def flaky_def(x: int) -> dict:
    if _call("flaky_def") == 1:
        raise ConnectionError("503 from upstream")
    return {"d": x}


@op
async def slow_step(x: int) -> dict:
    await asyncio.sleep(0.02)
    return {"s": x}


@op
async def after_slow(s: int) -> dict:
    _call("after_slow")
    return {"t": s}


@op(retry=False)
async def pay(x: int) -> dict:
    _call("pay")
    raise ConnectionError("gateway timed out")


@op
async def bad(x: int) -> dict:
    _call("bad")
    raise ValueError("not a number")


@op(retry=Retry(max_attempts=1))
async def own_policy(x: int) -> dict:
    _call("own_policy")
    raise ConnectionError("503")


@op(timeout=__import__("operonx").Timeout(run=5))
async def timeout_only(x: int) -> dict:
    if _call("timeout_only") == 1:
        raise ConnectionError("503")
    return {"o": x}


@op
async def always_down(x: int) -> dict:
    _call("always_down")
    raise ConnectionError("503")


@op
async def stream(x: int):
    _call("stream")
    yield {"item": 1}
    raise ConnectionError("dropped mid-stream")


@graph
def one_async(x):
    a = flaky_async(x=x)
    START >> a >> END


@graph
def inline_beside_async(x):
    d = flaky_def(x=x)
    s = slow_step(x=x)
    t = after_slow(s=s["s"])
    START >> d >> END
    START >> s >> t >> END


@graph
def paying(x):
    p = pay(x=x)
    START >> p >> END


@graph
def bad_graph(x):
    b = bad(x=x)
    START >> b >> END


@graph
def own(x):
    o = own_policy(x=x)
    START >> o >> END


@graph
def with_timeout(x):
    o = timeout_only(x=x)
    START >> o >> END


@graph
def down(x):
    a = always_down(x=x)
    START >> a >> END


@graph
def streaming(x):
    s = stream(x=x)
    START >> s >> END


@graph
def per_use_off(x):
    a = flaky_async(x=x, retry=False)
    START >> a >> END


@graph
def inner(x):
    d = flaky_def(x=x)
    START >> d >> END


@graph
def outer(x):
    i = inner(x=x)
    START >> i >> END


def _engine(g, **kw):
    return Operon(g, params={"x": None}, **kw)


async def test_an_async_op_is_retried_by_the_default():
    handle = _engine(one_async, retry=FAST).start({"x": 3})
    out = await handle.result()
    assert out["a"] == 3 and "$errors" not in out
    assert CALLS["flaky_async"] == 2
    nodes = [n for n in handle.trace.nodes if n.op_name == "a"]
    assert [(n.attempt, n.status) for n in nodes] == [(1, "retried"), (2, "ok")]


async def test_without_a_default_nothing_is_retried():
    out = await _engine(one_async).run({"x": 3})
    assert "a" not in out and out["$errors"]
    assert CALLS["flaky_async"] == 1


async def test_an_inline_def_op_waits_in_a_task_and_the_run_keeps_moving():
    out = await _engine(inline_beside_async, retry=DEFAULT).run({"x": 5})
    assert out["d"] == 5 and out["t"] == 5 and "$errors" not in out
    assert CALLS["flaky_def"] == 2
    # The other branch went on during the backoff: the main loop did not sleep.
    assert AT["after_slow"][0] < AT["flaky_def"][1]
    assert AT["flaky_def"][1] - AT["flaky_def"][0] >= WAIT


async def test_an_inline_op_in_a_durable_run_is_retried_and_journalled_once():
    journal = MemoryJournal()
    out = await _engine(inline_beside_async, retry=FAST, journal=journal).run(
        {"x": 2}, run_id="r-1"
    )
    assert out["d"] == 2 and "$errors" not in out
    assert CALLS["flaky_def"] == 2


async def test_an_inline_op_in_a_nested_graph_is_retried():
    out = await _engine(outer, retry=FAST).run({"x": 4})
    assert out["d"] == 4 and "$errors" not in out
    assert CALLS["flaky_def"] == 2


async def test_retry_false_opts_out():
    out = await _engine(paying, retry=FAST).run({"x": 1})
    assert out["$errors"] and CALLS["pay"] == 1


async def test_retry_false_for_one_use():
    out = await _engine(per_use_off, retry=FAST).run({"x": 1})
    assert out["$errors"] and CALLS["flaky_async"] == 1


async def test_a_bug_is_not_retried():
    out = await _engine(bad_graph, retry=FAST).run({"x": 1})
    assert out["$errors"] and CALLS["bad"] == 1


async def test_the_ops_own_retry_wins():
    out = await _engine(own, retry=FAST).run({"x": 1})
    assert out["$errors"] and CALLS["own_policy"] == 1


async def test_an_op_with_only_a_timeout_takes_the_default_retry():
    out = await _engine(with_timeout, retry=FAST).run({"x": 6})
    assert out["o"] == 6 and CALLS["timeout_only"] == 2


async def test_gives_up_after_max_attempts():
    out = await _engine(down, retry=FAST).run({"x": 1})
    assert out["$errors"] and CALLS["always_down"] == 3


async def test_a_generator_is_not_retried_after_its_first_yield():
    out = await _engine(streaming, retry=FAST).run({"x": 1})
    assert out["$errors"] and CALLS["stream"] == 1


async def test_errors_raise_with_a_default_retry_succeeds_after_the_retry():
    out = await _engine(one_async, retry=FAST, errors="raise").run({"x": 9})
    assert out["a"] == 9


def test_retry_takes_a_retry():
    with pytest.raises(TypeError, match="retry= takes a Retry"):
        _engine(one_async, retry=3)


def test_a_graph_refuses_retry_false():
    with pytest.raises(TypeError, match="not to a graph"):
        inner(x=1, retry=False)
