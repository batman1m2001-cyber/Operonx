"""What runs after a loop, and when a loop stops.

A back-edge loop is rewritten into a hidden loop op that runs once per
iteration, each iteration at its own context. Two things went wrong at
that boundary:

* **Ops after the loop ran on every iteration.** The loop op emitted a
  frame per iteration and the outer scheduler routed each one — to every
  successor, whichever way the loop's branch went. An op on the exit arm
  of ``if_(done == True, finish).else_(step)`` ran on iteration 0 (while
  the branch was choosing ``step``), again on every later iteration, and
  its successors with it. A subgraph wrapping a loop yielded once per
  iteration, so the op after the subgraph ran once per iteration too.
  Successors now run once, when the loop exits, at the loop's own
  context, and only along the exit the loop actually took.

* **A back-edge source that raised kept the loop going.** Termination
  asked "did the source run this iteration", and a failing op has run.
  The loop re-entered with the same state and spun to the 1000-iteration
  cap, failing every time. A failing op emits nothing, so its back-edge
  does not fire and the loop stops at that iteration.
"""

from __future__ import annotations

import asyncio

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_

CALLS: list = []


@op
def step(n: int) -> dict:
    CALLS.append(("step", n))
    return {"n": n + 1, "done": n + 1 >= 3, "failed": False}


@op
def finish(n: int = None) -> dict:
    CALLS.append(("finish", n))
    return {"final": n}


@op
def after(final: int = None) -> dict:
    CALLS.append(("after", final))
    return {"after": final}


@op
def on_error(n: int = None) -> dict:
    CALLS.append(("on_error", n))
    return {"handled": n}


def _calls(name):
    return [c for c in CALLS if c[0] == name]


# ── the exit arm ────────────────────────────────────────────────────────


@graph
def exit_arm():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    f = finish(n=s["n"])
    a = after(final=f["final"])
    START >> s >> if_(s["done"] == True, f).else_(s)  # noqa: E712
    f >> a >> END


async def test_exit_arm_op_runs_once_with_the_final_values():
    CALLS.clear()
    out = await Operon(exit_arm).run(inputs={})
    assert _calls("step") == [("step", 0), ("step", 1), ("step", 2)]
    assert _calls("finish") == [("finish", 3)]
    assert _calls("after") == [("after", 3)]
    assert out["after"] == 3
    # Per-iteration values of loop state are still streamed as before.
    assert out["n"] == [1, 2, 3]


async def test_exit_arm_runs_once_when_the_loop_exits_on_the_first_iteration():
    CALLS.clear()
    out = await Operon(exit_arm).run(inputs={"n": 5})
    assert _calls("step") == [("step", 5)]
    assert _calls("finish") == [("finish", 6)]
    assert out["after"] == 6


# ── several exits: only the one taken runs ──────────────────────────────


@op
def step_or_fail(n: int, fail_at: int = 99) -> dict:
    CALLS.append(("step", n))
    return {"n": n + 1, "done": n + 1 >= 3, "failed": n + 1 == fail_at}


@graph
def two_exits():
    PARENT.declare(n=0, fail_at=99)
    s = step_or_fail(n=PARENT["n"], fail_at=PARENT["fail_at"])
    s["n"] >> PARENT["n"]
    e = on_error(n=s["n"])
    START >> s >> if_(s["failed"] == True, e).if_(s["done"] == True, END).else_(s)  # noqa: E712
    e >> END


async def test_exit_to_end_does_not_run_the_other_exit_arm():
    CALLS.clear()
    await Operon(two_exits).run(inputs={})
    assert len(_calls("step")) == 3
    assert _calls("on_error") == []


async def test_the_exit_arm_taken_runs_once():
    CALLS.clear()
    out = await Operon(two_exits).run(inputs={"fail_at": 2})
    assert len(_calls("step")) == 2
    assert _calls("on_error") == [("on_error", 2)]
    assert out["handled"] == 2


# ── an op after a subgraph that contains a loop ─────────────────────────


@graph
def counter():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    START >> s >> if_(s["done"] == True, END).else_(s)  # noqa: E712


@graph
def after_loop_subgraph():
    c = counter()
    a = after(final=c["n"])
    START >> c >> a >> END


async def test_op_after_a_loop_subgraph_runs_once():
    CALLS.clear()
    out = await Operon(after_loop_subgraph).run(inputs={})
    assert len(_calls("step")) == 3
    assert _calls("after") == [("after", 3)]
    assert out["after"] == 3


# ── an op after the loop joined with a branch that finishes later ───────


@op
async def side() -> dict:
    await asyncio.sleep(0.02)
    CALLS.append(("side",))
    return {"p": "P"}


@op
def join(final: int = None, p: str = None) -> dict:
    CALLS.append(("join", final, p))
    return {"joined": [final, p]}


@graph
def loop_then_join():
    PARENT.declare(n=0)
    sd = side()
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    j = join(final=s["n"], p=sd["p"])
    START >> [sd, s]
    s >> if_(s["done"] == True, j).else_(s)  # noqa: E712
    sd >> j
    j >> END


async def test_join_after_the_loop_sees_the_final_iteration():
    CALLS.clear()
    out = await Operon(loop_then_join).run(inputs={})
    assert _calls("join") == [("join", 3, "P")]
    assert out["joined"] == [3, "P"]


# ── an exit through a plain edge out of the loop body ───────────────────


@op
def bump(v: int) -> dict:
    CALLS.append(("bump", v))
    return {"v": v + 1, "done": v + 1 >= 3}


@op
def relay(v: int = None) -> dict:
    CALLS.append(("relay", v))
    return {"out": v}


@graph
def plain_exit_edge():
    PARENT.declare(v=0)
    b = bump(v=PARENT["v"])
    r = relay(v=b["v"])  # outside the cycle: an exit through a plain edge
    b["v"] >> PARENT["v"]
    START >> b >> if_(b["done"] == True, END).else_(b)  # noqa: E712
    b >> r >> END


async def test_plain_exit_edge_runs_once_after_the_loop():
    CALLS.clear()
    out = await Operon(plain_exit_edge).run(inputs={})
    assert len(_calls("bump")) == 3
    assert _calls("relay") == [("relay", 3)]
    assert out["out"] == 3


# ── a back-edge source that raises ──────────────────────────────────────

CHECKS = {"n": 0}


@op
def check(n: int) -> dict:
    CHECKS["n"] += 1
    if n >= 2:
        raise ValueError("boom")
    return {"ok": True}


@op
async def acheck(n: int) -> dict:
    CHECKS["n"] += 1
    if n >= 2:
        raise ValueError("boom")
    return {"ok": True}


@graph
def raising_source():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    c = check(n=s["n"])
    START >> s >> c
    c >> s  # back-edge from a plain op
    s >> END


@graph
def raising_async_source():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    c = acheck(n=s["n"])
    START >> s >> c
    c >> s
    s >> END


def _errors(state, op_name):
    """Every recorded error of ``op_name``, whatever context it ran at."""
    found = []
    for (full, var), idx in state.schema._var_to_idx.items():
        if var == "error" and full.endswith("." + op_name):
            found += [v for v in state._cells[idx].contexts.values() if v]
    return found


async def _stops_at_the_failing_iteration(g):
    CALLS.clear()
    CHECKS["n"] = 0
    out = await asyncio.wait_for(Operon(g).run(inputs={}), timeout=10)
    # step(0) -> check(1) ok -> step(1) -> check(2) raises -> stop.
    assert len(_calls("step")) == 2
    assert CHECKS["n"] == 2
    assert out["n"] == [1, 2]
    errors = _errors(out["$state"], "c")
    assert len(errors) == 1 and "boom" in errors[0]


async def test_a_raising_back_edge_source_stops_the_loop():
    await _stops_at_the_failing_iteration(raising_source)


async def test_a_raising_async_back_edge_source_stops_the_loop():
    await _stops_at_the_failing_iteration(raising_async_source)


# ── the same inside a fan-out: the back-edge source behind .collect() ───


@op
def fan(n: int):
    for i in range(2):
        yield {"i": i + n}


@op
def gather(items: list) -> dict:
    CHECKS["n"] += 1
    if max(items) >= 2:
        raise ValueError("boom")
    return {"count": len(items)}


@graph
def raising_collect_source():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    f = fan(n=s["n"])
    g = gather(items=f["i"].collect())
    START >> s >> f >> g
    g >> s
    s >> END


async def test_a_raising_back_edge_source_behind_a_fan_out_stops_the_loop():
    CALLS.clear()
    CHECKS["n"] = 0
    await asyncio.wait_for(Operon(raising_collect_source).run(inputs={}), timeout=10)
    # step(0): items [1, 2] -> gather raises on the first iteration.
    assert len(_calls("step")) == 1
    assert CHECKS["n"] == 1
