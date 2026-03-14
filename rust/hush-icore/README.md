# hush-icore

High-performance Rust execution backend for Hush workflows. Pure `rlib` — no PyO3, standalone engine.

[![crates.io](https://img.shields.io/crates/v/hush-icore)](https://crates.io/crates/hush-icore)

## Overview

Rust companion to [hush-icore (Python)](https://pypi.org/project/hush-icore/). Python builds workflow graphs via DSL, serializes to JSON, Rust loads and executes with:

- **DashMap** for lock-free concurrent state
- **tokio** async event-queue scheduler (1:1 port of Python scheduler)
- **Context hierarchy fallback** for streaming/iteration contexts
- **Plugin ops** via cdylib + `hush_plugin!` macro

## Usage

```rust
use hush_icore::Hush;

let engine = Hush::new(config_json)?;
let result = engine.run_json(inputs_json, request_id, None, None)?;
```

Typically used via [hush-serve](https://crates.io/crates/hush-serve) which handles HTTP routing.

## From Python

```python
from hush.core import Hush

engine = Hush(graph)
engine.serve(port=8000, backend="rust")  # Spawns hush-serve binary
```

## Benchmarks

~8x faster than Python backend on pure-compute workflows (1000 req, 50 CCU).

## License

Apache 2.0
