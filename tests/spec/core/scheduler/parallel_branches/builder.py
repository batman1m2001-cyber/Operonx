"""Fixture: two independent ops off START, both feed PARENT outputs."""

from operon.core import END, PARENT, START, GraphOp

from tests.spec._ops import add_one, double


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        doubler = double(
            name="doubler",
            x=PARENT["x"],
            outputs={"result": PARENT["twice"]},
        )
        plus_one = add_one(
            name="plus_one",
            n=PARENT["x"],
            outputs={"answer": PARENT["incremented"]},
        )
        START >> doubler >> END
        START >> plus_one >> END
    return graph
