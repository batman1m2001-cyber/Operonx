//! Rust-internal integration tests for `core`.
//!
//! Mirrors `operonx/src/core/` structure — one module per subsystem tested
//! against Rust-specific invariants (no Python parallel).

// Populated as each subsystem lands in Phase 1+.

pub mod exceptions;
pub mod interrupt;
pub mod macros;
pub mod resource_hub_setup;
pub mod stream_policy;
