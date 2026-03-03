"""Shared fixtures for hush-serve tests."""

import pytest
from hush.core import END, PARENT, START, GraphOp, op


@op
def double(x: int):
    return {"result": x * 2}


@op
def add(a: int, b: int):
    return {"result": a + b}


@op
def greet(name: str):
    return {"greeting": f"Hello, {name}!"}


@op
def slow_op(x: int):
    import time

    time.sleep(0.05)
    return {"result": x * 10}


@op
def failing_op(x: int):
    raise ValueError(f"Intentional failure with x={x}")


@pytest.fixture
def double_graph():
    with GraphOp(name="doubler") as g:
        step = double(x=PARENT["x"])
        START >> step >> END
    return g


@pytest.fixture
def add_graph():
    with GraphOp(name="adder") as g:
        step = add(a=PARENT["a"], b=PARENT["b"])
        START >> step >> END
    return g


@pytest.fixture
def greet_graph():
    with GraphOp(name="greeter") as g:
        step = greet(name=PARENT["name"])
        START >> step >> END
    return g


@pytest.fixture
def chain_graph():
    """Two-step chain: double then add."""
    with GraphOp(name="chain") as g:
        d = double(x=PARENT["x"])
        a = add(a=d["result"], b=PARENT["y"])
        START >> d >> a >> END
    return g


@pytest.fixture
def slow_graph():
    with GraphOp(name="slow") as g:
        step = slow_op(x=PARENT["x"])
        START >> step >> END
    return g


@pytest.fixture
def failing_graph():
    with GraphOp(name="failing") as g:
        step = failing_op(x=PARENT["x"])
        START >> step >> END
    return g
