# `tests/spec/` — Phase 9 parity fixtures

Each subfolder is **one behavioural test** expressed as a `(graph, inputs) → expected outputs` triple. Both the Python and Rust implementations read the **same** JSON files; drift between them is caught by this harness.

## Layout

```
spec/<area>/<name>/
├── graph.json      output of `Operon.export_config()` (Python) — the serialized workflow
├── inputs.json     inputs dict passed to engine.run()
└── expected.json   expected outputs dict (timing keys stripped before compare)
```

## What's deliberately excluded from these fixtures

- Live LLM / embedding / reranker HTTP calls. Provider parity lands in a follow-up batch via `wiremock`.
- Telemetry-backed assertions (Langfuse, OperonEyes). Trace-shape parity lands in `spec/telemetry/` separately.
- Anything non-deterministic that isn't covered by seeds/mocks.

## Running

```bash
# Rust — via the spec_core binary
cd rust && cargo test -p operonx --test spec_core
# Target one fixture
cd rust && cargo test -p operonx --test spec_core single_code_op
```

## Timing-key stripping

`$start_time`, `$end_time`, `$duration_ms` are attached by the engine on every run and vary between invocations. The harness in `tests/common/mod.rs` strips them before comparison — they are *presence-checked* separately in the `timing_keys_present` fixture.
