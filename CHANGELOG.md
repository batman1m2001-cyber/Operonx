# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- **Plugin system (rush-plugin)**: New `rush-plugin` crate with `rush_plugin!` macro for building cdylib plugins. Replaces hardcoded `builtin_ops` with dynamic op registry (`OpRegistry` trait). rush-serve loads plugins at runtime via `--plugin` flag using `libloading`.
- **Tracing tests**: 13 TraceCollector tests in rush-core (covering the root graph timing bug) + 19 Langfuse batch format tests in rush-telemetry.
- **Example folder restructure**: Converted examples 03, 04, 06, 07 from single files to standard folder layout (`workflow.py`, `run.py`, `serve_python.py`, `serve_rust.py`, `client.py`).
- **Rust serve plugin loading**: `--plugin` CLI argument in rush-serve to load cdylib op plugins. `PluginRegistry` implements `OpRegistry` via FFI calls.
- **Python bridge plugin support**: `_rust_bridge.py` auto-detects and passes `--plugin` when spawning rush-serve with `rust_ops` parameter.
- **Benchmarks**: Extended `bench_runner.rs` with plugin-aware benchmarks.

### Fixed

- **Critical: Langfuse traces not appearing** — Root graph op was missing `$start_time`/`$end_time`/`$duration_ms` metadata in `engine.rs`. TraceCollector's `iter_executed()` uses `$start_time` to detect executed ops, so the root node was invisible, causing `sort_by_edges()` DFS to return 0 nodes.
- **Langfuse environment field** — Added `"environment": "default"` to trace-create events (required by Langfuse v3+ for dashboard visibility).
- **Langfuse batch builder** — Extracted `build_batch()` from `flush()` for testability (pure data transformation, no I/O).

### Changed

- **`builtin_ops` → `registry`**: Replaced `rush-core/src/builtin_ops/` module with `OpRegistry` trait in `registry.rs`. All op dispatch now goes through the registry. Test ops moved to `TestRegistry` in `tests/common/mod.rs`.
- **Error variant rename**: `RushError::BuiltinOpError` → `RushError::RegistryError`.
- **rush-serve state**: `AppState` now stores `Option<Arc<dyn OpRegistry>>` and threads it through to the engine.

### Removed

- `rust/rush-core/src/builtin_ops/mod.rs` and `ops.rs` (replaced by plugin system)
- `rust/rush-core/tests/builtin_ops.rs` (replaced by `test_ops.rs` using `TestRegistry`)
- `examples/07_embeddings_and_rag.py` single file (replaced by folder)
