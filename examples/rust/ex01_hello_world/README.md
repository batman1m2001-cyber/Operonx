# 01 — Hello World (Rust)

Three tiny graphs mirroring the Python side. All `#[op]` bodies are declared inline in `demo.rs`; the pre-serialized graphs live in `graph.json`.

| Scenario   | Ops                       | Shape             |
|------------|---------------------------|-------------------|
| `hello`    | `greet`                   | 1 node            |
| `chain`    | `greet_en → upper`        | 2 nodes in series |
| `parallel` | `step_a + step_b → merge` | fan-out + fan-in  |

## Run

```bash
cargo run --release -p operonx --example ex01_hello_world
cargo run --release -p operonx --example ex01_hello_world -- --runs 20
cargo run --release -p operonx --example ex01_hello_world -- --langfuse
```

Writes `examples/bench_results/ex01_hello_world_rust.json`.

## Regenerating `graph.json`

When `examples/python/ex01_hello_world/workflow.py` changes, regenerate with
[`examples/python/_dump_graph.py`](../../python/_dump_graph.py) — it strips
`python_callable` and redacts any `api_key` / `secret_key` / token fields
that `resources.yaml` expansion would otherwise inline:

```bash
uv run python -m examples.python._dump_graph ex01_hello_world \
    --scenarios hello chain parallel \
    --factories build_hello build_chain build_parallel
```

