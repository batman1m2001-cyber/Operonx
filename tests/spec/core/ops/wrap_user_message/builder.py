"""Fixture: `wrap_user(text) -> {msg: {role: user, content: text}}`."""

from operon.core import END, PARENT, START, GraphOp

from tests.spec._ops import wrap_user


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        wrap = wrap_user(
            name="wrap",
            text=PARENT["text"],
            outputs={"msg": PARENT["msg"]},
        )
        START >> wrap >> END
    return graph
