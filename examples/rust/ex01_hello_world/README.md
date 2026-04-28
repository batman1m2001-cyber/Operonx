# 01 — Hello World (Rust)

Three tiny graphs, no API keys. Mirrors the Python `ex01_hello_world`
example so you can compare the two runtimes side by side.

| Scenario   | Ops                       | Shape             |
|------------|---------------------------|-------------------|
| `hello`    | `greet`                   | 1 node            |
| `chain`    | `greet_en → upper`        | 2 nodes in series |
| `parallel` | `step_a + step_b → merge` | fan-out + fan-in  |

## Project layout

```
ex01_hello_world/
├── Cargo.toml         # operonx + inventory + serde_json
├── src/main.rs        # #[op] declarations + run loop
├── graph.json         # graph specs (one per scenario)
└── inputs.json        # illustrative inputs
```

The `inventory` dep is required at the call site because the `#[op]`
proc-macro expands to an `inventory::submit!{ ... }` block. Once the
macro re-exports its dependencies through `operonx::*` you will be able
to drop it; for v0.6.2 it stays explicit.

This directory is a self-contained starter crate. Copy it anywhere, run
`cargo run --release`, and you have a working Operonx Rust workspace to
build on.

## Run

```bash
cargo run --release
```

## Authoring graph specs

The `graph.json` here was produced by serialising the matching Python
graphs (see `examples/python/ex01_hello_world/workflow.py`). When you
write a brand-new Rust workflow you can either author the JSON directly
(it is a stable, documented schema) or build it via the Python DSL and
ship the JSON alongside your Rust binary. Both are first-class.
