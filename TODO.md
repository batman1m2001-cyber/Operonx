# Hush-ai TODO

## Status Legend
- [x] Done
- [ ] Pending
- [~] In progress

---

## Phase 1: Fix Core Bugs + Examples

### 1A. Fix Ref.apply() in Rust mode
- [x] Investigate Ref.apply() — Python callables can't cross FFI boundary (design limitation)
- [x] Fix hush-icore: `_serialize_transforms()` raises clear `ValueError` when Ref.apply(callable) is serialized
- [x] Fix example 10: replace `Ref.apply(lambda)` with `@op(rust="...")` pattern
- [x] Remove `spawn_provider_task` — revert to unified `spawn_blocking_task` for all IO-bound ops

### 1B. Fix Rust loop condition evaluator
- [x] Root cause: `loop_eval.rs` `resolve_expr_token()` didn't handle Python's `True`/`False`/`None` literals
- [x] Fix: add `True`→`true`, `False`→`false`, `None`→`null` mapping in `resolve_expr_token()`
- [x] Add 8 unit tests in `loop_eval.rs` (True/False/None, counter, truthiness, not, len)
- [x] Fix `process_agent_response` in rust_ops — was simulating tool results, now actually computes them
- [x] Verify: example 09 bench shows Python=2054ms, Rust=2010ms (was 10s due to 10 max iterations)

### 1C. Fix Langfuse tracing (root graph timing)
- [x] Root cause: `engine.rs` didn't store `$start_time`/`$end_time`/`$duration_ms` for root graph op
- [x] TraceCollector's `iter_executed()` uses `$start_time` to detect executed ops — root was invisible
- [x] Add `"environment": "default"` to Langfuse trace-create events (v3+ requirement)
- [x] Extract `build_batch()` from `flush()` for testability
- [x] 13 TraceCollector tests in hush-icore + 19 Langfuse batch format tests in hush-telemetry

### 1D. Plugin system (cdylib)
- [x] `hush-plugin` crate with `hush_plugin!` macro
- [x] Replace `builtin_ops` with `OpRegistry` trait + `registry.rs`
- [x] `hush-serve` loads plugins at runtime via `--plugin` flag using `libloading`
- [x] `_rust_bridge.py` auto-detects and passes `--plugin` when spawning hush-serve

### 1E. Example folder restructure
- [x] Convert examples 03, 04, 06, 07 from single files to folder layout
- [x] Standard pattern: `workflow.py`, `run.py`, `serve_python.py`, `serve_rust.py`, `client.py`
- [x] Graph factory pattern: `Hush(build_fn, env=..., resources=...)`
- [x] `HushApp(env=, resources=)` for multi-endpoint examples

### 1F. Rust generator output aggregation
- [x] Fix `get_outputs()` for null-ref outputs (populated by `push_output_refs`)
- [x] Check graph's own state before falling back to terminal ops
- [x] 14 stream aggregation tests

---

## Phase 2: Docs Sync (Guide + Architecture)

### Cherry-picked example mapping for guide

| Chapter | Title | Example | Status |
|---------|-------|---------|--------|
| 00 | Overview | — | [x] Fixed old refs (ForOp/MapOp/WhileOp, dead example paths) |
| 01 | Install & Setup | — | [ ] Review |
| 02 | Quickstart | 01, 02 | [ ] Review |
| 03 | Core Concepts | 02 | [ ] Review |
| 04 | LLM Integration | 03 | [ ] Review |
| 05 | Loops & Branches | 05 | [x] **Full rewrite** (generators + @graph.loop) |
| 06 | Embeddings & RAG | 07 | [x] Fixed MapOp/Each batch embedding section |
| 07 | Error Handling | 08 | [ ] Review |
| 08 | Parallel Execution | 11 | [x] **Rewritten** (generator ops, fan-out/fan-in, partial failure) |
| 09 | Tracing | 06 | [ ] Review |
| 10 | Agent Workflow | 09 | [x] **Full rewrite** (@graph.loop + tool calling) |
| 11 | Multi-Model | 10 | [ ] Review |
| 12 | Shorthand Syntax | all | [x] **Full rewrite** (generators, @graph.loop, removed old ops) |
| 13 | Rust Mode & Plugin | 01-12 (all with serve_rust.py) | [x] **Rewritten** (cdylib plugin system, hush_plugin! macro) |

### Architecture docs
- [x] `rust-python-split.md`: Added cdylib plugin system section
- [ ] Others: verify accuracy

---

## Phase 3: CI/CD Publishing

### 3A. Name availability
- [ ] Check PyPI: `hush-icore`, `hush-providers`, `hush-telemetry`, `hush-serve`
- [ ] Check crates.io: `hush-icore`, `hush-providers`, `hush-telemetry`, `hush-serve`, `hush-plugin`, `hush-eyes`
- [ ] Fallback plan: `hush-ai-*` if taken

### 3B. Package builds
- [ ] `uv build` for all 4 Python packages
- [ ] `cargo build --workspace --release` for all Rust crates
- [ ] `cargo test --workspace` passes

### 3C. Rust crates.io publish order
```
hush-plugin (standalone)
hush-providers (standalone)
hush-icore (depends on hush-providers)
hush-telemetry (depends on hush-icore)
hush-serve (depends on hush-icore + hush-providers + hush-telemetry)
hush-eyes (standalone)
```

### 3D. Cargo.toml fixes
- [ ] Add `repository` field to `hush-plugin/Cargo.toml`
- [ ] Verify all crates have: name, version, edition, license, description, repository

### 3E. PyPI publishing setup
- [ ] Create PyPI account + verify email
- [ ] Set up OIDC trusted publisher for each package
- [ ] Create "pypi" environment in GitHub repo Settings

### 3F. Package READMEs
- [ ] Python: hush-icore, hush-providers, hush-telemetry, hush-serve
- [ ] Rust: hush-icore, hush-providers, hush-plugin, hush-serve, hush-telemetry

### 3G. publish.yaml updates
- [ ] Add `hush-plugin` to version-check loop
- [ ] Add `cargo publish -p hush-plugin` as first step
- [ ] Add `sleep 30` between each `cargo publish`

---

## Phase 4: Separate Examples Project

### Structure
```
hush-examples/
├── pyproject.toml        # installs from PyPI, NO uv.sources
├── README.md
├── .env.example
├── 01_hello_world/
├── 02_data_pipeline/
└── ... (all examples, no bench.py/serve files)
```

### Tasks
- [ ] Create hush-examples/ with PyPI-only dependencies
- [ ] Copy examples (workflow.py + demo.py only)
- [ ] Verify install from built wheels
- [ ] Run pure-compute examples (01, 02, 05, 08, 11, 13, 14, 15)
- [ ] Run API-key examples with OPENAI_API_KEY

---

## Known Design Limitations

- **Ref.apply() in Rust mode**: Python callables cannot cross the FFI boundary. Use `@op(rust="...")` instead. `_serialize_transforms()` now raises a clear `ValueError` at build time.
