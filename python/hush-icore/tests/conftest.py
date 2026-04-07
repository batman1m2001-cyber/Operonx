"""Shared fixtures and utilities for pytest test suite."""

from typing import Any, Dict

import pytest

from hush.core import (
    END,
    PARENT,
    START,
    FuncOp,
    GraphOp,
    MemoryState,
    StateSchema,
    op,
)

# ============================================================
# Common Test Utilities
# ============================================================


def assert_test(name: str, condition: bool):
    """Helper function for test assertions with descriptive names."""
    assert condition, f"Test failed: {name}"


# ============================================================
# Common Fixtures
# ============================================================


@pytest.fixture
def simple_code_fn():
    """Simple code function that doubles input."""
    return lambda x: {"result": x * 2}


@pytest.fixture
def add_fn():
    """Code function that adds two numbers."""
    return lambda a, b: {"result": a + b}


@pytest.fixture
def increment_fn():
    """Code function that increments by 1."""
    return lambda x: {"x": x + 1}


# ============================================================
# Graph Fixtures
# ============================================================


@pytest.fixture
def simple_graph():
    """Create a simple single-node graph."""

    @op
    def double(x: int):
        return {"result": x * 2}

    with GraphOp(name="simple_graph") as graph:
        node = double(inputs={"x": PARENT["x"]}, outputs={"*": PARENT})
        START >> node >> END

    graph.build()
    return graph


@pytest.fixture
def linear_graph():
    """Create a linear two-node graph: add_10 -> multiply_2."""
    with GraphOp(name="linear_graph") as graph:
        node_a = FuncOp(
            name="add_10", code_fn=lambda x: {"result": x + 10}, inputs={"x": PARENT["x"]}
        )
        node_b = FuncOp(
            name="multiply_2",
            code_fn=lambda x: {"result": x * 2},
            inputs={"x": node_a["result"]},
            outputs={"*": PARENT},
        )
        START >> node_a >> node_b >> END

    graph.build()
    return graph


# ============================================================
# State Fixtures
# ============================================================


@pytest.fixture(autouse=True)
def clear_op_cache():
    """Clear the class-level op cache between tests to prevent cross-test pollution."""
    from hush.core.ops.base import BaseOp

    yield
    BaseOp._cache_stores.clear()


@pytest.fixture
def create_state():
    """Factory fixture to create state from a graph with inputs."""

    def _create_state(graph: GraphOp, inputs: Dict[str, Any] = None) -> MemoryState:
        schema = StateSchema(graph)
        return schema.create_state(inputs=inputs or {})

    return _create_state
