//! Entry binary for shared spec `core` tests (JSON-fixture driven).
//!
//! Run via `cargo test -p operonx --test spec_core`.

mod common;

#[path = "spec/core/mod.rs"]
mod core;
