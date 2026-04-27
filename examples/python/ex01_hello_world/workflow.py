"""Graphs for ex01_hello_world.

Three tiny graphs exercising the three fundamental topologies of Operon:

* ``build_hello``     — single op.
* ``build_chain``     — two ops in series (``greet → upper``).
* ``build_parallel``  — two ops in parallel then a merge.

No external API keys needed.
"""

from operonx.core import END, PARENT, START, GraphOp, op


@op
def greet(name: str):
    """Tạo greeting từ tên."""
    return {"greeting": f"Xin chào, {name}!"}


@op
def greet_en(name: str):
    """Tạo greeting tiếng Anh."""
    return {"greeting": f"Hello, {name}!"}


@op
def upper(text: str):
    return {"result": text.upper()}


@op
def step_a():
    return {"a_result": "Kết quả A"}


@op
def step_b():
    return {"b_result": "Kết quả B"}


@op
def merge(a: str, b: str):
    return {"combined": f"{a} + {b}"}


def build_hello() -> GraphOp:
    with GraphOp(name="hello-world") as graph:
        g = greet(name=PARENT["name"], outputs={"greeting": PARENT["greeting"]})
        START >> g >> END
    return graph


def build_chain() -> GraphOp:
    with GraphOp(name="two-steps") as graph:
        g = greet_en(name=PARENT["name"], outputs={"greeting": PARENT["greeting"]})
        u = upper(text=g["greeting"], outputs={"result": PARENT["result"]})
        START >> g >> u >> END
    return graph


def build_parallel() -> GraphOp:
    with GraphOp(name="parallel") as graph:
        a = step_a()
        b = step_b()
        m = merge(
            a=a["a_result"],
            b=b["b_result"],
            outputs={"combined": PARENT["combined"]},
        )
        START >> a >> m >> END
        START >> b >> m
    return graph
