# hush-serve

Auto-generate HTTP API servers from Hush workflow graphs. Dual backend: Python (FastAPI) or Rust (Axum).

[![PyPI](https://img.shields.io/pypi/v/hush-serve)](https://pypi.org/project/hush-serve/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)

## Installation

```bash
pip install hush-serve
```

For the Rust backend:

```bash
cargo install hush-serve
```

## Quick Start

```python
from hush.core import GraphOp, op, START, END, PARENT
from hush.serve import HushApp

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="doubler") as graph:
    step = double(x=PARENT["x"])
    START >> step >> END

app = HushApp()
app.endpoint("/double", graph=graph)

# Python backend (FastAPI + uvicorn)
app.serve(port=8000)

# Rust backend (Axum — ~8x faster)
app.serve(port=8000, backend="rust")
```

## Auto-Generated Routes

Each endpoint automatically creates:

| Route | Type | Description |
|-------|------|-------------|
| `POST /path` | Sync | JSON request/response |
| `POST /path/stream` | SSE | Server-Sent Events streaming |
| `WS /path/ws` | WebSocket | Bidirectional communication |
| `GET /health` | System | Health check |
| `GET /endpoints` | System | List all registered endpoints |

## Dual Backend

```python
# Python: FastAPI + uvicorn (default)
app.serve(port=8000)

# Rust: Axum + hush-icore (requires cargo install hush-serve)
app.serve(port=8000, backend="rust")

# Rust with custom plugin ops
app.serve(port=8000, backend="rust", rust_ops="rust_ops")
```

The Rust backend automatically finds the `hush-serve` binary via:
1. Monorepo build: `rust/target/release/hush-serve`
2. PATH lookup: `cargo install hush-serve`

## Decorator Style

```python
app = HushApp()

@app.endpoint("/chat")
@graph
def chatbot(query):
    llm = chain(resource="gpt-4o", template={"user": "{query}"}, query=query)
    START >> llm >> END
```

## Related Packages

| Package | Description |
|---------|-------------|
| [hush-icore](https://pypi.org/project/hush-icore/) | Core workflow engine (required) |
| [hush-providers](https://pypi.org/project/hush-providers/) | LLM, embedding, reranking (optional) |
| [hush-serve (crate)](https://crates.io/crates/hush-serve) | Standalone Rust HTTP server |

## License

Apache 2.0
