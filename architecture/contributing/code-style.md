# Code Style Guide

## Tools

- **Formatter**: Ruff (hoặc Black)
- **Linter**: Ruff
- **Type Checker**: (optional) mypy/pyright

## Configuration

```toml
# pyproject.toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "W"]
ignore = ["E501"]  # Line too long - handled by formatter
```

## General Guidelines

### Naming Conventions

```python
# Classes: PascalCase
class GraphNode:
    pass

class BaseTracer:
    pass

# Functions/methods: snake_case
def get_current():
    pass

def insert_node_trace():
    pass

# Constants: UPPER_SNAKE_CASE
MAX_RETRIES = 3
DEFAULT_DB_PATH = Path("~/.hush/traces.db")

# Private: underscore prefix
_current_graph = ContextVar("current_graph")
_TRACER_REGISTRY = {}

# Module-level "globals": underscore prefix
_store: Optional[TraceStore] = None
_background: Optional[BackgroundWorker] = None
```

### Type Hints

```python
# Required cho public APIs
def run(
    self,
    texts: Union[str, List[str]],
    **kwargs: Any
) -> Dict[str, Any]:
    pass

# Optional cho internal code
def _process(self, data):
    pass
```

### Docstrings

```python
# Public methods: docstring required
def stream(
    self,
    messages: List[ChatCompletionMessageParam],
    temperature: float = 0.0,
) -> AsyncGenerator[ChatCompletionChunk, None]:
    """Stream responses từ LLM.

    Args:
        messages: List of chat messages
        temperature: Controls randomness (0.0-2.0)

    Returns:
        AsyncGenerator yielding ChatCompletionChunk

    Raises:
        ConnectionError: If connection fails
    """

# Private methods: docstring optional
def _prepare_params(self, **kwargs):
    pass
```

### Imports

```python
# Standard library
import asyncio
from pathlib import Path
from typing import Dict, List, Optional

# Third party (blank line after stdlib)
import pydantic
from openai import AsyncOpenAI

# Local imports (blank line after third party)
from hush.core.nodes import BaseNode
from hush.core.states import MemoryState
```

## Class Structure

### __slots__ Usage

```python
# Prefer __slots__ cho performance-critical classes
class Cell:
    __slots__ = ["_data", "default_value"]

    def __init__(self, default_value=None):
        self._data = {}
        self.default_value = default_value
```

### ClassVar vs Instance

```python
class LLMConfig(YamlModel):
    # Class variables: ClassVar
    _category: ClassVar[str] = "llm"
    _type: ClassVar[str] = "openai"

    # Instance variables: type hints
    api_key: str
    base_url: str
```

### Property vs Method

```python
class MemoryState:
    # Property: no arguments, cached/simple access
    @property
    def user_id(self) -> str:
        return self._user_id

    # Method: has side effects or complex logic
    def record_execution(self, node_name: str, parent: str):
        self._execution_order.append({...})
```

## Async Patterns

### Async Methods

```python
# Async methods cho I/O operations
async def run(self, texts: List[str]) -> Dict:
    async with aiohttp.ClientSession() as session:
        response = await session.post(...)
    return response

# Sync wrapper khi cần
def run_sync(self, texts: List[str]) -> Dict:
    return asyncio.run(self.run(texts))
```

### AsyncGenerator

```python
async def stream(self, messages: List) -> AsyncGenerator[Chunk, None]:
    async for chunk in self.client.chat.completions.create(
        stream=True,
        **params
    ):
        yield chunk
```

## Error Handling

```python
# Specific exceptions
class WorkflowError(Exception):
    pass

class NodeExecutionError(WorkflowError):
    pass

# Catching exceptions
try:
    result = await node.run()
except NodeExecutionError as e:
    self._handle_failure(node, e)
except Exception as e:
    # Log unexpected errors
    logger.error(f"Unexpected error: {e}")
    raise
```

## Comments

```python
# Explain WHY, not WHAT
# Bad:
x = x + 1  # Increment x

# Good:
# Retry count starts at 0, so we need +1 for human-readable display
attempt_number = retry_count + 1

# Section headers cho readability
# ============================================================
# Core Methods
# ============================================================

def stream(self, ...):
    pass
```

## File Organization

```
hush/
├── core/
│   ├── __init__.py         # Public exports
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── base.py         # BaseNode
│   │   ├── graph/          # GraphNode related
│   │   ├── iteration/      # Loop nodes
│   │   └── transform/      # CodeNode, ParserNode
│   ├── states/
│   │   ├── __init__.py
│   │   ├── schema.py
│   │   ├── state.py
│   │   ├── ref.py
│   │   └── cell.py
│   └── tracers/
│       ├── __init__.py
│       ├── base.py
│       └── store.py
```

## Pre-commit (optional)

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```
