# Testing Guide

## Overview

- Framework: pytest với pytest-asyncio
- Location: `{package}/tests/`
- Config: `pyproject.toml`

## Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: marks tests as integration tests (may require external services)",
]
```

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── nodes/
│   ├── __init__.py
│   ├── flow/
│   │   ├── test_branch_node.py
│   │   └── test_operator_precedence.py
│   ├── graph/
│   │   └── test_graph_node.py
│   ├── iteration/
│   │   ├── test_for_loop_node.py
│   │   ├── test_map_node.py
│   │   └── test_while_loop_node.py
│   └── transform/
│       └── test_code_node.py
├── states/
│   ├── test_cell.py
│   ├── test_ref.py
│   ├── test_schema.py
│   └── test_state.py
├── registry/
│   └── test_resource_hub.py
└── tracers/
    └── test_local_tracer.py
```

## Running Tests

### All Tests

```bash
pytest tests/ -v
```

### Specific File/Directory

```bash
pytest tests/states/test_state.py -v
pytest tests/nodes/ -v
```

### Specific Test

```bash
pytest tests/states/test_state.py::TestSimpleLinearGraphValueFlow -v
pytest tests/states/test_state.py::TestSimpleLinearGraphValueFlow::test_ref_resolution -v
```

### Skip Integration Tests

```bash
pytest tests/ -v -m "not integration"
```

### Run Only Integration Tests

```bash
pytest tests/ -v -m integration
```

## Writing Tests

### Test Class Structure

```python
"""Tests for MemoryState - workflow state with Cell-based storage."""

import pytest
from hush.core.states.schema import StateSchema
from hush.core.states.state import MemoryState
from hush.core.nodes.graph.graph_node import GraphNode, START, END, PARENT
from hush.core.nodes.transform.code_node import CodeNode


# ============================================================
# Test 1: Simple Linear Graph Value Flow
# ============================================================

class TestSimpleLinearGraphValueFlow:
    """Test value injection and ref following in linear graph."""

    def test_input_set(self):
        """Test that input values are set correctly."""
        with GraphNode(name="linear_graph") as graph:
            node_a = CodeNode(
                name="node_a",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]}
            )
            START >> node_a >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"x": 5})

        assert state["linear_graph", "x", None] == 5

    def test_ref_resolution(self):
        """Test that refs are resolved correctly."""
        # ...
```

### Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_async_workflow():
    """Test async workflow execution."""
    graph = create_graph()
    result = await graph.run({"x": 10})
    assert result["output"] == 20
```

### Fixtures

```python
# conftest.py
import pytest
from hush.core import GraphNode, CodeNode, START, END, PARENT, StateSchema, MemoryState


@pytest.fixture
def simple_graph():
    """Create a simple single-node graph."""
    @code_node
    def double(x: int):
        return {"result": x * 2}

    with GraphNode(name="simple_graph") as graph:
        node = double(inputs={"x": PARENT["x"]}, outputs={"*": PARENT})
        START >> node >> END

    graph.build()
    return graph


@pytest.fixture
def create_state():
    """Factory fixture to create state from a graph with inputs."""
    def _create_state(graph: GraphNode, inputs: Dict[str, Any] = None) -> MemoryState:
        schema = StateSchema(graph)
        return schema.create_state(inputs=inputs or {})
    return _create_state
```

### Using Fixtures

```python
def test_with_fixtures(simple_graph, create_state):
    """Test using fixtures."""
    state = create_state(simple_graph, {"x": 5})
    assert state["simple_graph", "x", None] == 5
```

### Parametrized Tests

```python
@pytest.mark.parametrize("input_val,expected", [
    (5, 10),
    (0, 0),
    (-3, -6),
    (100, 200),
])
def test_double_node(simple_graph, create_state, input_val, expected):
    """Test double node with various inputs."""
    state = create_state(simple_graph, {"x": input_val})
    # Run graph...
    assert state["simple_graph", "result", None] == expected
```

## Integration Tests

### Marking Integration Tests

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_integration():
    """Test real OpenAI API call."""
    config = OpenAIConfig.from_yaml_file("configs/llm/openai.yaml")
    llm = LLMFactory.create(config)

    response = await llm.generate([
        {"role": "user", "content": "Say hello"}
    ])

    assert response.choices[0].message.content
```

### Environment Setup

```python
# conftest.py
import os
import pytest

@pytest.fixture
def openai_config():
    """Get OpenAI config from environment."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")

    return OpenAIConfig(
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        model="gpt-4"
    )
```

## Test Patterns

### Testing State Flow

```python
def test_value_flow_through_nodes(self):
    """Test value flow through multiple nodes."""
    with GraphNode(name="linear_graph") as graph:
        node_a = CodeNode(
            name="node_a",
            code_fn=lambda x: {"result": x + 10},
            inputs={"x": PARENT["x"]}
        )
        node_b = CodeNode(
            name="node_b",
            code_fn=lambda x: {"result": x * 2},
            inputs={"x": node_a["result"]}
        )
        START >> node_a >> node_b >> END

    graph.build()
    schema = StateSchema(graph)
    state = MemoryState(schema, inputs={"x": 5})

    # Simulate node_a execution
    x_val = state["linear_graph.node_a", "x", None]
    state["linear_graph.node_a", "result", None] = x_val + 10  # 15

    # Verify node_b can read from node_a
    assert state["linear_graph.node_b", "x", None] == 15
```

### Testing Multiple Contexts

```python
def test_different_context_values(self):
    """Test that different contexts have independent values."""
    # Setup graph...
    state = MemoryState(schema, inputs={"x": 0})

    # Simulate loop iterations
    state["loop_graph", "x", "iter_0"] = 10
    state["loop_graph", "x", "iter_1"] = 20
    state["loop_graph", "x", "iter_2"] = 30

    assert state["loop_graph", "x", "iter_0"] == 10
    assert state["loop_graph", "x", "iter_1"] == 20
    assert state["loop_graph", "x", "iter_2"] == 30
```

## Coverage

```bash
# Run with coverage
pytest tests/ --cov=hush --cov-report=html

# View report
open htmlcov/index.html
```
