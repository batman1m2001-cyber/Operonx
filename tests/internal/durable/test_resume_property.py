"""R3's core proof (RUNTIME_R3_PLAN §5): a run stopped at any journal step and
resumed ends exactly as the run that never stopped.

Hypothesis varies one graph that holds every construct the scheduler drives
— a generator, items through a sequential or a parallel edge, an item that
fails with an error edge, `.collect()`, a branch, a subgraph, a reducer cell
written by every item, a loop — and the point the run stops at. The stop is
a journal that refuses its n-th step (the process dies there: what it had
not written is lost). The resumed run must give the uninterrupted run's
outputs, `$errors` and reducer cell, and must not run again any execution
the journal had seen end.
"""

from __future__ import annotations

import asyncio
import operator
from collections import Counter

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_
from operonx.core.runtime import run_context
from operonx.durable import END as STEP_END
from operonx.durable import MemoryJournal

pytestmark = pytest.mark.unit

RAN: Counter = Counter()


def _ran(name: str) -> None:
    rc = run_context()
    RAN[(name, rc.ctx if rc is not None else ())] += 1


class Stop(BaseException):
    """The process dying at a journal step."""


class StoppingJournal(MemoryJournal):
    """Takes ``after`` steps, then refuses the rest, as a dead process would."""

    def __init__(self, after: int):
        super().__init__()
        self.after, self.taken = after, 0

    def append(self, run_id, steps):
        for step in steps:
            if self.taken >= self.after:
                raise Stop(f"stopped after {self.after} steps")
            super().append(run_id, [step])
            self.taken += 1


@op
async def items(n: int, fail: int):
    _ran("items")
    for i in range(n):
        yield {"i": i, "fail": fail}


@op
async def work(i: int, fail: int) -> dict:
    _ran("work")
    await asyncio.sleep(0.001 * ((i * 7) % 3))  # items finish out of order
    if i == fail:
        raise ValueError(f"item {i} is bad")
    return {"v": i * 10, "acc": [i]}


@op
def rescue(error: str = None) -> dict:
    _ran("rescue")
    return {"rescued": 1}


@op
def gather(vals: list = None) -> dict:
    _ran("gather")
    return {"total": sum(v for v in (vals or []) if v is not None)}


@op
def check(total: int) -> dict:
    _ran("check")
    return {"big": total >= 30}


@op
def big(total: int) -> dict:
    _ran("big")
    return {"label": f"big:{total}"}


@op
def small(total: int) -> dict:
    _ran("small")
    return {"label": f"small:{total}"}


@op
def merge(big_label: str = None, small_label: str = None) -> dict:
    _ran("merge")
    return {"label": big_label or small_label}


@op
def shout(label: str) -> dict:
    _ran("shout")
    return {"loud": label.upper()}


@graph
def inner(label):
    s = shout(label=label)
    START >> s >> END


@op
def step(n: int, limit: int) -> dict:
    _ran("step")
    return {"n": n + 1, "done": n + 1 >= limit}


@graph
def counter(limit):
    PARENT.declare(n=0)
    s = step(n=PARENT["n"], limit=limit)
    s["n"] >> PARENT["n"]
    START >> s >> if_(s["done"] == True, END).else_(s)  # noqa: E712


@graph
def flow(n, fail, limit, parallel=False):
    PARENT.declare(acc=[], reducers={"acc": operator.add})
    g = items(n=n, fail=fail)
    w = work(i=g["i"].parallel() if parallel else g["i"], fail=g["fail"])  # topology, at build
    w["acc"] >> PARENT["acc"]
    r = rescue()
    w.on_error(r)
    c = gather(vals=w["v"].collect())
    k = check(total=c["total"])
    b, s = big(total=c["total"]), small(total=c["total"])
    m = merge(big_label=b["label"], small_label=s["label"])
    sub = inner(label=m["label"])
    loop = counter(limit=limit)
    START >> g >> w >> c >> k >> if_(k["big"] == True, b).else_(s)  # noqa: E712
    b >> m
    s >> m
    m >> sub >> loop >> END


def _flow(parallel: bool):
    return flow(n=None, fail=None, limit=None, parallel=parallel)


def _outcome(out: dict, state, parallel: bool) -> dict:
    """What a run answered, comparably: outputs, failures, the reducer cell.
    A `.parallel()` stream's values arrive in completion order, which no two
    runs share (`.parallel()` does not keep order): compared as a multiset."""
    acc = state[state.schema.name, "acc"]
    # keyed without the root's name, which is the engine variable's
    errors = {k.split(".", 1)[1]: v["type"] for k, v in (out.get("$errors") or {}).items()}
    plain = {k: v for k, v in out.items() if not k.startswith("$")}
    if parallel:
        plain = {k: sorted(v, key=repr) if isinstance(v, list) else v for k, v in plain.items()}
    return {"out": plain, "errors": errors, "acc": sorted(acc or [])}


async def _uninterrupted(parallel, inputs):
    RAN.clear()
    out = await Operon(_flow(parallel)).run(inputs)
    return _outcome(out, out["$state"], parallel), sum(RAN.values())


async def _stopped_then_resumed(parallel, inputs, after):
    journal = StoppingJournal(after)
    engine = Operon(
        _flow(parallel),
        journal=journal,
        durability="sync",
    )
    RAN.clear()
    try:
        await engine.run(inputs, run_id="r")
    except BaseException:  # noqa: BLE001 — the stop, however it surfaces
        pass
    before = Counter(RAN)
    _, steps = journal.read("r")
    ended = {(s.op, s.ctx) for s in steps if s.index == STEP_END}
    journal.after = 10**9
    RAN.clear()
    handle = await engine.resume("r")
    out = await handle.result()
    return _outcome(out, handle.state, parallel), before, Counter(RAN), ended, len(steps)


@settings(
    max_examples=int(__import__("os").environ.get("OPERONX_R3_EXAMPLES", "60")),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    parallel=st.booleans(),
    n=st.integers(0, 4),
    fail=st.integers(-1, 3),
    limit=st.integers(1, 3),
    after=st.integers(0, 40),
)
def test_a_stopped_run_resumes_to_the_uninterrupted_result(parallel, n, fail, limit, after):
    inputs = {"n": n, "fail": fail, "limit": limit}
    expected, total = asyncio.run(_uninterrupted(parallel, inputs))
    got, before, again, ended, kept = asyncio.run(_stopped_then_resumed(parallel, inputs, after))

    assert got == expected
    # nothing the journal saw end ran again on resume: op bodies that ran in
    # both halves are only those whose end the stop cut off
    for (name, ctx), count in again.items():
        full = [key for key in ended if key[0].endswith("." + name) and key[1] == ctx]
        assert not full, f"{name} at {ctx} ended before the stop and ran again"


class DrainingJournal(MemoryJournal):
    """Asks the run to drain once it has taken ``after`` steps — a deploy
    stopping the worker at that point."""

    def __init__(self, after: int):
        super().__init__()
        self.after, self.taken, self.drain = after, 0, None

    def append(self, run_id, steps):
        super().append(run_id, steps)
        self.taken += len(steps)
        if self.taken >= self.after and self.drain is not None:
            self.drain()  # once
            self.drain = None


async def _drained_then_resumed(parallel, inputs, after):
    journal = DrainingJournal(after)
    engine = Operon(
        _flow(parallel),
        journal=journal,
        durability="sync",  # append runs in a worker thread
    )
    handle = engine.start(inputs, run_id="d")
    loop = asyncio.get_running_loop()
    journal.drain = lambda: loop.call_soon_threadsafe(handle.state._durable.drain)
    await handle.collect()
    status = journal.runs()[0].status
    if status == "ok":  # it ended before the drain came
        return _outcome(await handle.result(), handle.state, parallel), status
    resumed = await engine.resume("d")
    out = await resumed.result()
    return _outcome(out, resumed.state, parallel), status


@settings(
    max_examples=int(__import__("os").environ.get("OPERONX_R3_EXAMPLES", "60")),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    parallel=st.booleans(),
    n=st.integers(0, 4),
    fail=st.integers(-1, 3),
    limit=st.integers(1, 3),
    after=st.integers(1, 40),
)
def test_a_drained_run_resumes_to_the_uninterrupted_result(parallel, n, fail, limit, after):
    inputs = {"n": n, "fail": fail, "limit": limit}
    expected, _ = asyncio.run(_uninterrupted(parallel, inputs))
    got, status = asyncio.run(_drained_then_resumed(parallel, inputs, after))

    assert status in ("ok", "drained")
    event(f"status: {status}")
    assert got == expected
