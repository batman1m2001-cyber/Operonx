"""A loop that hits its iteration cap is a failure, reported (roadmap C8).

The hidden loop a back-edge is rewritten into stops after 1000 iterations.
It stopped silently: the run returned normally, ``$errors`` was empty, and
the 1000th iteration's values looked like an answer. It now records
``LoopLimitExceeded`` in ``$errors`` under the loop and runs nothing after
it, like any op that fails.

The cap is set on the branch that loops back:
``if_(cond, END, max_iterations=N).else_(step)``.
"""

from __future__ import annotations

import pytest

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_


@op
def step(n: int) -> dict:
    return {"n": n + 1, "done": False}


@op
def counted(n: int, limit: int = 3) -> dict:
    return {"n": n + 1, "done": n + 1 >= limit}


@op
def finish(n: int = None) -> dict:
    return {"final": n}


def _loop_errors(out: dict) -> dict:
    return {k: v for k, v in (out.get("$errors") or {}).items() if "__loop_" in k}


@graph
def runaway():
    """`evidence/probes/p1_errors_retry_parallel.py` (P3)."""
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    START >> s >> if_(s["done"] == True, END).else_(s)  # noqa: E712


async def test_loop_cap_reports_error():
    engine = Operon(runaway)
    out = await engine.run(inputs={})

    errors = _loop_errors(out)
    assert list(errors) == [f"{engine.name}.__loop_0__"]
    record = errors[f"{engine.name}.__loop_0__"]
    assert record["type"] == "LoopLimitExceeded"
    message = record["message"]
    assert message.startswith("LoopLimitExceeded: ")
    assert "1000 iterations" in message
    assert "max_iterations" in message  # says how to change it
    assert out["$state"].get(engine.name, "n") == 1000


@graph
def capped():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    START >> s >> if_(s["done"] == True, END, max_iterations=5).else_(s)  # noqa: E712


async def test_max_iterations_sets_the_cap():
    engine = Operon(capped)
    out = await engine.run(inputs={})

    assert out["$state"].get(engine.name, "n") == 5
    assert "5 iterations" in _loop_errors(out)[f"{engine.name}.__loop_0__"]["message"]


@graph
def exits_on_the_last_allowed_iteration():
    PARENT.declare(n=0)
    s = counted(n=PARENT["n"], limit=3)
    s["n"] >> PARENT["n"]
    f = finish(n=s["n"])
    START >> s >> if_(s["done"] == True, f, max_iterations=3).else_(s)  # noqa: E712
    f >> END


async def test_a_loop_that_exits_within_the_cap_reports_nothing():
    """Three iterations under a cap of three: the third did not loop back."""
    out = await Operon(exits_on_the_last_allowed_iteration).run(inputs={})

    assert out["final"] == 3
    assert "$errors" not in out


@graph
def cap_before_exit():
    PARENT.declare(n=0)
    s = counted(n=PARENT["n"], limit=10)
    s["n"] >> PARENT["n"]
    f = finish(n=s["n"])
    START >> s >> if_(s["done"] == True, f, max_iterations=4).else_(s)  # noqa: E712
    f >> END


async def test_the_ops_after_a_capped_loop_do_not_run():
    engine = Operon(cap_before_exit)
    out = await engine.run(inputs={})

    assert "final" not in out
    assert out["$errors"][f"{engine.name}.__loop_0__"]["type"] == "LoopLimitExceeded"


@graph
def inner_runaway():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    START >> s >> if_(s["done"] == True, END, max_iterations=2).else_(s)  # noqa: E712


@op
def after(n: int = None) -> dict:
    return {"z": n}


@graph
def outer_of_runaway():
    s = inner_runaway()
    a = after(n=s["n"])
    START >> s >> a >> END


async def test_a_capped_loop_inside_a_subgraph_is_reported_under_its_path():
    """Nested, the loop is keyed by its full path like any nested op."""
    engine = Operon(outer_of_runaway)
    out = await engine.run(inputs={})

    errors = out["$errors"]
    assert errors[f"{engine.name}.s.__loop_0__"]["type"] == "LoopLimitExceeded"


def test_max_iterations_on_a_branch_that_closes_no_loop_is_refused():
    with pytest.raises(ValueError, match="max_iterations.*closes no loop"):

        @graph
        def no_loop():
            s = counted(n=0)
            f = finish(n=s["n"])
            START >> s >> if_(s["done"] == True, f, max_iterations=3).else_(END)  # noqa: E712
            f >> END

        Operon(no_loop)


@pytest.mark.parametrize("bad", [0, -1, 2.5, True])
def test_max_iterations_must_be_a_positive_int(bad):
    s = step(n=0)
    with pytest.raises(ValueError, match="max_iterations"):
        if_(s["done"] == True, END, max_iterations=bad)  # noqa: E712
