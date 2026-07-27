---
name: example
description: Scaffold a new Python example with workflow, demo, and serve files
---

# /example — Create a New Example

Scaffold a new example folder under `examples/python/` with the standard file layout.

> Rust examples live in the sibling repo
> [operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs) under
> `examples/`.

## Usage

```
/example 16_my_feature        # Create example 16
/example 16_my_feature --llm  # Include LLM provider ops
```

## Steps

### 1. Create the folder

```bash
mkdir -p examples/python/ex{NN}_{name}
```

### 2. Generate files

Every example has this structure:

#### `workflow.py` — Shared graph definition

```python
"""Shared workflow definition for ex{NN}_{name}.

Defines the graph and ops — imported by demo.py, serve_python.py.
"""

from operonx.core import END, PARENT, START, GraphOp, op


@op
def my_op(x: int):
    return {"result": x * 2}


def build_workflow():
    with GraphOp(name="{name}") as graph:
        step = my_op(x=PARENT["x"])
        START >> step >> END
    return graph
```

#### `demo.py` — Quick local test (no server)

```python
"""Run workflow locally (no HTTP server).

Run: cd examples/python && uv run python ex{NN}_{name}/demo.py
"""

import asyncio
from operonx.core import Operon
from workflow import build_workflow


async def main():
    engine = Operon(build_workflow())
    result = await engine.run(inputs={"x": 5})
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
```

#### `serve_python.py` — HTTP server (`operonx[serve]`)

```python
"""Serve with FastAPI + uvicorn.

Run: cd examples/python && uv run python ex{NN}_{name}/serve_python.py
"""

import os
from operonx.serve import build_app
from operonx.core import Operon
from workflow import build_workflow

app = build_app(engine_factory=lambda: Operon(build_workflow()))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, port=int(os.environ.get("PORT", 8000)))
```

### 3. Test

```bash
cd examples/python && uv run python ex{NN}_{name}/demo.py
```

## Naming convention

- Folders: `ex{NN}_{snake_case_name}` (e.g., `ex16_custom_parser`)
- Numbers are sequential — check the last example number first
- Workflows: descriptive function name (e.g., `build_parser_workflow`)
