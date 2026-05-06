//! State model — MemoryState, StateSchema, Ref, Cell.
//!
//! Mirrors Python `operonx/core/states/`.
//!
//! Note: Rust's `ref` is a reserved keyword. The file stays `ref.rs` (matching
//! Python's `ref.py`) but the module identifier is `ref_` — see §3a.7.

pub mod cell;
#[path = "ref.rs"]
pub mod ref_;
pub mod schema;
pub mod scratch_ref;
pub mod state;
