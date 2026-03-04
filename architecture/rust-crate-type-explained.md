# Rust Crate Types: rush-core Architecture

## Current Setup

rush-core is a **pure `rlib`** crate — standalone Rust engine with no PyO3 dependency.

```toml
# rush-core/Cargo.toml
[lib]
name = "rush_core"
crate-type = ["rlib"]
```

### Build & Test

```bash
# Run all tests (unit + integration, 130+ tests)
cd rush-core && cargo test

# Release build (optimized, LTO)
cd rush-core && cargo build --release

# Run rush-providers tests
cd rush-core && cargo test -p rush-providers
```

### Architecture

```
Python (build time)             Rust (run time)
─────────────────               ──────────────
GraphOp DSL                     Rush::new(json_str)
  │                               │
  ▼                               ▼
graph.serialize() ──JSON──→  GraphConfig deserialization
                               │
                               ▼
                           run_graph() scheduler
                               │
                               ▼
                           execute_leaf_op() / nested / for / while
```

- Python builds graphs via DSL, serializes to JSON
- Rust loads JSON config and executes the workflow
- No GIL, no PyO3 — pure Rust execution with rayon parallelism

### CI Flow

```
┌─ Rust Runtime CI ───────────────────────────────────────────┐
│                                                             │
│  Step 1: cargo test              (130+ Rust tests)          │
│          ↓                                                  │
│  Step 2: cargo test -p rush-providers  (provider tests)     │
│          ↓                                                  │
│  Step 3: cargo build --release   (verify release build)     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Historical Context

rush-core was originally a PyO3 extension module (`cdylib`) that used `maturin` to build
a Python-importable `.pyd`/`.so`. It was later converted to a pure `rlib` crate for:

1. **Simpler build** — no maturin, no Python dependency at build time
2. **Faster tests** — `cargo test` runs natively without GIL overhead
3. **Better parallelism** — rayon works without `allow_threads`/`with_gil` dance
4. **Standalone execution** — Rush engine can run independently of Python
