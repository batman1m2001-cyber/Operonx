# Rust and Python

The Rust crate (`operonx`) mirrors the Python package (`operonx`) op-for-op.
Both runtimes share the same workflow JSON contract — a graph defined in
either language can run on either backend.

## When to pick which

| Workload | Recommended | Why |
|---|---|---|
| LLM / agent / RAG | Python | I/O-bound; Python's async stack is sufficient and the provider ecosystem is richer. |
| CPU-bound transforms (parsing, tokenization, math) | Rust | Faster per op, no GIL, parallel scheduler. |
| Mixed | Python with Rust plugin ops | Define hot ops as cdylib crates; Python orchestrates. |
| Edge / standalone binary | Rust | One static binary, no Python runtime. |

## Parity invariants

The two backends are kept in sync via shared JSON test fixtures under
`tests/spec/`. Each fixture pins a graph + inputs + expected outputs;
both the Python and Rust runtimes must produce identical results.

Specifically guaranteed:

- Same op semantics (`LLMOp`, `EmbeddingOp`, `RerankOp`, `BranchOp`,
  `GraphOp`, generator ops, loops).
- Same edge semantics (`>>` hard, `>>~` soft).
- Same state model (`PARENT`, `op[key]`, output mapping).
- Same five `ResourceHub` failure branches and two warnings (see
  [Resource hub](resource-hub.md)).
- `Operon::new(graph_json)` does **not** auto-load `.env` or
  `resources.yaml`, mirroring Python's `Operon(graph)` decoupling.

## Plugin ops (cdylib)

Custom Rust ops are dispatched via the `OpRegistry` trait in
`rust/operonx/src/registry.rs`. To add a Rust op consumed from Python:

1. Write a `fn(&serde_json::Value) -> serde_json::Value` function in your
   cdylib crate.
2. Export via `hush_plugin!(func_name)` macro from the `operonx-plugin`
   crate.
3. Reference from Python via `@op(rust="./my_crate::module::func")` —
   Python falls back to the function body if the cdylib isn't loaded.

```python
@op(rust="./rust_ops::pipeline::double")
def double(x: int):
    return {"result": x * 2}  # Python fallback
```

## Build targets

| Crate | Type | How |
|---|---|---|
| `operonx` | rlib | `cd rust && cargo build --release` |
| `operonx-macros` | proc-macro | Built transitively as a `operonx` dep |
| User cdylib ops | cdylib | `cargo build --release` in the user's crate |

## Out of scope (deferred)

- Rust HTTP serve port (Axum-based). Python `operonx[serve]` is
  authoritative for now.
- Rust OTEL tracing backend. Python `operonx[otel]` is authoritative.
