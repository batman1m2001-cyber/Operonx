"""``.collect()`` must not store the collected op's outputs a second time (roadmap C1).

A collect hands its consumer the stream's values as lists, at a context of
its own. It did that by storing **every** output of the collected op there,
through the op's normal store — so each output's push to a ``PARENT`` cell
fired again with the whole list. A reducer cell fed per item by a sibling
output got each item twice: ``operator.add`` gave
``['item0', 'item1', 'item2', ['item0'], ['item1'], ['item2']]``, and
``dict_merge`` raised ``ReducerError`` on the list it was handed.

The collect now writes only the variables its consumer reads through
``.collect()``, and writes them without pushing.
"""

from __future__ import annotations

import operator

from operonx import END, PARENT, START, Operon, graph, op
from operonx.reducers import dict_merge


@op
def each(n: int):
    for i in range(n):
        yield {"i": i}


@op
def work(i: int) -> dict:
    return {"row": i, "log": [f"item{i}"], "seen": {f"k{i}": i}}


@op
def report(rows: list) -> dict:
    return {"count": len(rows), "rows_seen": rows}


@graph
def with_collect(n):
    PARENT.declare(log=[], reducers={"log": operator.add})
    e = each(n=n)
    w = work(i=e["i"])
    w["log"] >> PARENT["log"]
    r = report(rows=w["row"].collect())
    START >> e >> w >> r >> END


@graph
def without_collect(n):
    PARENT.declare(log=[], reducers={"log": operator.add})
    e = each(n=n)
    w = work(i=e["i"])
    w["log"] >> PARENT["log"]
    START >> e >> w >> END


async def _cell(g, var: str):
    engine = Operon(g, params={"n": None})
    out = await engine.run(inputs={"n": 3})
    return out, out["$state"].get(engine.name, var)


async def test_collect_reducer_no_double_write():
    """`evidence/repros/repro_collect_reducer.py`: the same cell with and
    without the collect."""
    out, log = await _cell(with_collect, "log")
    _, log_plain = await _cell(without_collect, "log")

    assert log_plain == ["item0", "item1", "item2"]
    assert log == ["item0", "item1", "item2"]
    assert out["count"] == 3
    assert out["rows_seen"] == [0, 1, 2]
    assert "$errors" not in out


@graph
def dict_reducer(n):
    PARENT.declare(seen={}, reducers={"seen": dict_merge})
    e = each(n=n)
    w = work(i=e["i"])
    w["seen"] >> PARENT["seen"]
    r = report(rows=w["row"].collect())
    START >> e >> w >> r >> END


async def test_collect_dict_reducer_does_not_raise():
    out, seen = await _cell(dict_reducer, "seen")

    assert seen == {"k0": 0, "k1": 1, "k2": 2}
    assert out["count"] == 3
    assert "$errors" not in out


async def test_the_collected_var_itself_is_not_pushed_again():
    """The collected list is the consumer's input, not a new write of the
    var: a push on that var must not receive it."""

    @op
    def work_one(i: int) -> dict:
        return {"row": [i]}

    @graph
    def g(n):
        PARENT.declare(rows=[], reducers={"rows": operator.add})
        e = each(n=n)
        w = work_one(i=e["i"])
        w["row"] >> PARENT["rows"]
        r = report(rows=w["row"].collect())
        START >> e >> w >> r >> END

    out, rows = await _cell(g, "rows")

    assert rows == [0, 1, 2]
    assert out["rows_seen"] == [[0], [1], [2]]
