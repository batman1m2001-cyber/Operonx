---
name: example
description: Scaffold a new example with workflow, demo, bench, and serve files
---

# /example — Create a New Example

Scaffold a new example folder with the standard file layout.

## Usage

```
/example 16_my_feature        # Create example 16
/example 16_my_feature --llm  # Include LLM provider ops
```

## Steps

### 1. Create the folder

```bash
mkdir -p examples/{NN}_{name}
```

### 2. Generate files

Every example has this structure:

#### `workflow.py` — Shared graph definition

```python
"""Shared workflow definition for {NN}_{name}.

Defines the graph and ops — imported by demo.py, serve_python.py, serve_rust.py.
"""

from hush.core import END, PARENT, START, GraphOp
from hush.core.ops.transform.func_op import op


@op(rust="./rust_ops::module::func")
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

Chạy: cd examples && uv run python {NN}_{name}/demo.py
"""

import asyncio
from hush.core import Hush
from workflow import build_workflow

async def main():
    engine = Hush(build_workflow())
    result = await engine.run(inputs={"x": 5})
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
```

#### `serve_python.py` — Python backend

```python
"""Serve with Python backend (FastAPI + uvicorn).

Chạy: cd examples && uv run python {NN}_{name}/serve_python.py
"""

import os
from hush.core import Hush
from workflow import build_workflow

engine = Hush(build_workflow())
engine.serve(port=int(os.environ.get("PORT", 8000)))
```

#### `serve_rust.py` — Rust backend

```python
"""Serve with Rust backend (Axum).

Requires: cd rust && cargo build --release -p hush-serve
          cd examples/rust_ops && cargo build --release

Chạy: cd examples && uv run python {NN}_{name}/serve_rust.py
"""

import os
from hush.core import Hush
from workflow import build_workflow

engine = Hush(build_workflow())
engine.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    rust_ops="rust_ops",
)
```

#### `bench.py` — Benchmark both backends

Copy from the nearest similar example (01 for pure-compute, 03 for LLM) and update:
- `ENDPOINTS` list with the new workflow's paths and payloads
- Docstring with the example name

### 3. Add Rust ops (if needed)

If the workflow uses `@op(rust="./rust_ops::module::func")`:
1. Create the module in `examples/rust_ops/src/{module}.rs`
2. Add functions and export via `hush_plugin!(..., new_func)`
3. Update `examples/rust_ops/src/lib.rs` to declare the module

### 4. Test

```bash
# Demo
cd examples && uv run python {NN}_{name}/demo.py

# Bench (both backends)
cd examples && uv run python {NN}_{name}/bench.py
```

### 5. Copy to hush-examples (if project exists)

If `/home/thanglq150188/Work/hush-examples/` exists, copy the new example there too.

## Naming convention

- Folders: `{NN}_{snake_case_name}` (e.g., `16_custom_parser`)
- Numbers are sequential — check the last example number first
- Workflows: descriptive function name (e.g., `build_parser_workflow`)
