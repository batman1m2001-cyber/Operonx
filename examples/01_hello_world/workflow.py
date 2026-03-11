"""Shared workflow definition for 01_hello_world.

Defines the graph and ops — imported by run.py, serve_python.py, serve_rust.py.
"""

from hush.core import END, PARENT, START, GraphOp
from hush.core.ops.transform.func_op import op


@op(rust="./rust_ops::text::greet_vi")
def greet(name: str):
    """Tạo greeting từ tên."""
    return {"greeting": f"Xin chào, {name}!"}


@op(rust="./rust_ops::text::greet")
def greet_en(name: str):
    """Tạo greeting tiếng Anh."""
    return {"greeting": f"Hello, {name}!"}


@op(rust="./rust_ops::text::to_upper")
def upper(text: str):
    """Chuyển thành uppercase."""
    return {"result": text.upper()}


@op(rust="./rust_ops::pipeline::step_a")
def step_a():
    return {"a_result": "Kết quả A"}


@op(rust="./rust_ops::pipeline::step_b")
def step_b():
    return {"b_result": "Kết quả B"}


@op(rust="./rust_ops::pipeline::merge_two")
def merge(a: str, b: str):
    return {"combined": f"{a} + {b}"}


def build_hello():
    """Single greeting workflow."""
    with GraphOp(name="hello-world") as graph:
        g = greet(name=PARENT["name"])
        START >> g >> END
    return graph


def build_chain():
    """Two ops chained: greet → uppercase."""
    with GraphOp(name="two-steps") as graph:
        g = greet_en(name=PARENT["name"], outputs={"*": PARENT})
        u = upper(text=g["greeting"])
        START >> g >> u >> END
    return graph


def build_parallel():
    """Two ops in parallel → merge."""
    with GraphOp(name="parallel") as graph:
        a = step_a()
        b = step_b()
        m = merge(a=a["a_result"], b=b["b_result"])
        START >> [a, b] >> m >> END
    return graph
