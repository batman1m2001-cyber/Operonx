//! Entry binary for Rust-internal `core` tests.
//!
//! Run via `cargo test -p operonx --test internal_core`.

mod common;

#[path = "internal/core/mod.rs"]
mod core;
