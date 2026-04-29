# Rust examples

Each subdirectory is a **standalone crate** — its own `Cargo.toml`,
`Cargo.lock`, `.env` (gitignored), and `resources.yaml` where
relevant. Not a workspace member; copy a directory out of the repo
and it builds against the published crate from crates.io.

## Run an example

```bash
cd examples/rust/exNN_<name>
cargo run --release
```

For LLM examples, copy `.env.example` to `.env` and fill in your key
first.

## Dev-mode override

`examples/rust/.cargo/config.toml` adds:

```toml
[patch.crates-io]
operonx        = { path = "../../rust/operonx" }
operonx-macros = { path = "../../rust/operonx-macros" }
```

So when you run `cargo run` from any `examples/rust/exNN_*/` while
inside this repo, you build against the workspace crates rather than
the registry version. Users copying an example out drop that file
along with the rest of the parent dir and the published version
takes over.

## Index

| # | Example | Crate features | Status |
|---|---------|----------------|--------|
| 01 | `ex01_hello_world` | (defaults) | runs |
| 02 | `ex02_data_pipeline` | (defaults) | runs |
| 03 | `ex03_llm_chat` | (defaults) | runs |
| 04 | `ex04_llm_advanced` | (defaults) | runs (limited — structured output / tool calling are pending) |
| 05 | `ex05_loops_and_branches` | (defaults) | runs (limited — `if_()` fires every branch; see below) |
| 07 | `ex07_embeddings_and_rag` | (defaults) | runs |
| 08 | `ex08_error_handling` | (defaults) | runs |
| 09 | `ex09_agent_workflow` | (defaults) | runs (limited — tool loop blocked on real branch routing) |
| 10 | `ex10_multi_model` | (defaults) | runs |
| 11 | `ex11_parallel_advanced` | (defaults) | runs |
| 12 | `ex12_rag_advanced` | (defaults) | runs |
| 13 | `ex13_graph` | (defaults) | runs |
| 14 | `ex14_streaming_tracing` | (defaults) | runs (limited — generator per-item dispatch accumulates rather than fans out) |
| 15 | `ex15_callbot_streaming` | (defaults) | runs (limited — same generator caveat) |

The current Rust feature set (`langfuse`, `operonx-eyes`, `onnx`,
`triton`) compiles in by default; per-provider feature gating is
deferred to v0.7. See `REFACTOR_post_v0.6.2.md` for the plan.

## Runtime parity caveats

See [`examples/README.md` § Rust runtime parity](../README.md#rust-runtime-parity)
for the canonical list. Highlights:

- **Nested `@graph` composition** — runs (sub-engine cached
  process-wide); pure-noop nesting still pays the per-call
  `tokio::spawn` + `mpsc` setup. Real fix in
  `REFACTOR_post_v0.6.2.md`.
- **`if_()` branch routing** — graphs parse, scheduler fires every
  case target and lets a soft-edge merge pick the answer. Real
  selective routing blocked on the ref-transform evaluator.
- **Generator per-item dispatch** — accumulated, not streamed.
- **`engine.stream(...)`** — not yet implemented.

## Regenerating `graph.json`

LLM-using examples ship a checked-in `graph.json` so the Rust binary
doesn't need a Python build step. The `operonx-pack` CLI (registered
by `pip install operonx`) re-emits it from the matching Python
builder. From inside the example dir:

```bash
cd examples/python/ex03_llm_chat
operonx-pack \
    main::basic_chat \
    main::chain_chat \
    main::summarize_pipeline \
    -o ../../rust/ex03_llm_chat/graph.json
```

Pass `--no-bootstrap` for pure-compute examples that don't need a
`resources.yaml` lookup at build time.
