"""Fixture: Ref `.getattr_via_call` style lookup on a dict.

Locks Stage-3 architecture: Rust's GetAttr variant on Value::Object
behaves like GetItem (returns the keyed entry).
"""

from operonx.core import END, GraphOp, PARENT, START, op


@op
def make_obj():
    return {"obj": {"name": "Alice", "age": 30}}


@op
def echo(value):
    return {"got": value}


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        src = make_obj()
        # `getattr` transform is emitted when Python sees `src["obj"].name`
        # (attr access on a Ref). At the wire level it's a `getattr`
        # transform — different from `getitem`. Both should resolve the
        # same way on a dict-shaped value.
        consumer = echo(value=src["obj"].name)
        consumer["got"] >> PARENT["got"]
        START >> src >> consumer >> END
    return graph
