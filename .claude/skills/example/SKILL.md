---
name: example
description: Scaffold a new example with workflow, demo, bench, and serve files
---

# /example — Create a New Example

Scaffold a new example folder under `examples/python/` with the standard file layout.

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

Defines the graph and ops — imported by demo.py, serve_python.py, serve_rust.py.
"""

from operonx.core import END, PARENT, START, GraphOp, op


@op(rust="my_op")
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

#### `serve_python.py` — Python backend (`operonx[serve]`)

```python
"""Serve with Python backend (FastAPI + uvicorn).

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

#### `serve_rust.py` / Rust binary — Rust backend (Axum)

The Rust serve binary is invoked from the command line — there is no Python wrapper:

```bash
cd rust && cargo build --release -p operonx-serve
./rust/target/release/operonx-serve --graph ./graph.json --port 8000
```

If the example needs Rust ops, register them with `#[op]` in a Rust crate that
links into `operonx-serve` (no cdylib runtime loading is implemented).

#### `bench.py` — Benchmark both backends

Copy from the nearest similar example (ex01 for pure-compute, ex03 for LLM) and update:
- `ENDPOINTS` list with the new workflow's paths and payloads
- Docstring with the example name

### 3. Add Rust ops (if needed)

If the workflow uses `@op(rust="my_op")`:

1. Add the function to `examples/rust_ops/src/{module}.rs` (or wherever your
   Rust crate lives).
2. Annotate with `#[op(name = "my_op")]` from `operonx-macros`.
3. Make sure the crate is linked into the binary that runs the workflow
   (e.g., `operonx-serve`).

### 4. Test

```bash
# Demo
cd examples/python && uv run python ex{NN}_{name}/demo.py

# Bench (both backends)
cd examples/python && uv run python ex{NN}_{name}/bench.py
```

## Naming convention

- Folders: `ex{NN}_{snake_case_name}` (e.g., `ex16_custom_parser`)
- Numbers are sequential — check the last example number first
- Workflows: descriptive function name (e.g., `build_parser_workflow`)
