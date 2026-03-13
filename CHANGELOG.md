# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- **Graph factory in `Hush.__init__`**: Accept a callable graph factory + `params` dict. Loads env/resources first, then calls `factory(**params)`. Enables `Hush(build_agent, env=..., resources=...)` where LLM ops eagerly resolve from the hub without lazy hacks.
- **`HushApp(env=, resources=)`**: Load env/resources upfront in `HushApp.__init__` so subsequent `app.endpoint("/path", graph=build_xxx())` calls work with eager LLM resolution.
- **Rust generator output aggregation fix**: When graph outputs have `ref: null` (populated by `push_output_refs`), `get_outputs()` now checks the graph's own state before falling back to terminal ops. Fixes Rust returning `{}` for generator workflows with `>> PARENT["key"]` forwarding.

### Changed

- **Example 09 serve scripts**: Use factory pattern `Hush(build_agent, env=..., resources=...)` instead of `Hush(build_agent(), env=..., resources=...)`.
- **Example 10 serve scripts**: Use `HushApp(env=..., resources=...)` instead of passing env/resources to `app.serve()`.
- **Example 11**: Removed `filter_results` op from `build_partial_failure()` — forward outputs directly via `>> PARENT["key"]`.
- **`Endpoint.__init__`**: Passes `env=False` to `Hush()` since env/resources are already loaded by `HushApp`.

### Previous

- **Plugin system (hush-plugin)**: New `hush-plugin` crate with `hush_plugin!` macro for building cdylib plugins. Replaces hardcoded `builtin_ops` with dynamic op registry (`OpRegistry` trait). hush-serve loads plugins at runtime via `--plugin` flag using `libloading`.
- **Tracing tests**: 13 TraceCollector tests in hush-icore (covering the root graph timing bug) + 19 Langfuse batch format tests in hush-telemetry.
- **Example folder restructure**: Converted examples 03, 04, 06, 07 from single files to standard folder layout (`workflow.py`, `run.py`, `serve_python.py`, `serve_rust.py`, `client.py`).
- **Rust serve plugin loading**: `--plugin` CLI argument in hush-serve to load cdylib op plugins. `PluginRegistry` implements `OpRegistry` via FFI calls.
- **Python bridge plugin support**: `_rust_bridge.py` auto-detects and passes `--plugin` when spawning hush-serve with `rust_ops` parameter.
- **Benchmarks**: Extended `bench_runner.rs` with plugin-aware benchmarks.

### Fixed

- **Critical: Langfuse traces not appearing** — Root graph op was missing `$start_time`/`$end_time`/`$duration_ms` metadata in `engine.rs`. TraceCollector's `iter_executed()` uses `$start_time` to detect executed ops, so the root node was invisible, causing `sort_by_edges()` DFS to return 0 nodes.
- **Langfuse environment field** — Added `"environment": "default"` to trace-create events (required by Langfuse v3+ for dashboard visibility).
- **Langfuse batch builder** — Extracted `build_batch()` from `flush()` for testability (pure data transformation, no I/O).

### Changed

- **`builtin_ops` → `registry`**: Replaced `hush-icore/src/builtin_ops/` module with `OpRegistry` trait in `registry.rs`. All op dispatch now goes through the registry. Test ops moved to `TestRegistry` in `tests/common/mod.rs`.
- **Error variant rename**: `RushError::BuiltinOpError` → `RushError::RegistryError`.
- **hush-serve state**: `AppState` now stores `Option<Arc<dyn OpRegistry>>` and threads it through to the engine.

### Removed

- `rust/hush-icore/src/builtin_ops/mod.rs` and `ops.rs` (replaced by plugin system)
- `rust/hush-icore/tests/builtin_ops.rs` (replaced by `test_ops.rs` using `TestRegistry`)
- `examples/07_embeddings_and_rag.py` single file (replaced by folder)
