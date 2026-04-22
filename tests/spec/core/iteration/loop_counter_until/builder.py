"""Fixture: feedback loop with string `until` condition.

`GraphOp.loop(until="count >= 5", count=0)` re-dispatches until the
expression evaluates true. Each iteration pipes `increment(counter)` and
assigns the result back to `PARENT["count"]`.
"""

from operon.core import END, PARENT, START, GraphOp

from tests.spec._ops import increment


def build_graph() -> GraphOp:
    with GraphOp.loop(name="main", until="count >= 5", count=0) as graph:
        inc = increment(counter=PARENT["count"])
        inc["counter"] >> PARENT["count"]
        START >> inc >> END
    return graph
