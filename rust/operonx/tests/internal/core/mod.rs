//! Rust-internal integration tests for `core`.
//!
//! Mirrors `operonx/src/core/` structure — one module per subsystem tested
//! against Rust-specific invariants (no Python parallel).

// Populated as each subsystem lands in Phase 1+.

pub mod exceptions;
pub mod handle_api;
pub mod interrupt;
pub mod interrupt_seq_cancel;
pub mod macros;
pub mod refs;
pub mod resource_hub_setup;
pub mod stream_policy;
