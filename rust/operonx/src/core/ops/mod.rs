//! Op base class, sentinels, and concrete op types.
//!
//! Mirrors Python `operonx/core/ops/`. Also contains `cache.rs` — a Rust-internal
//! utility (Python equivalent lives in the `cache` field on `BaseOp`, but the
//! implementation is Rust-only).

#[path = "_events.rs"]
pub mod events;

pub mod base;
pub mod cache;
pub mod edges;
pub mod flow;
pub mod graph;
pub mod params;
pub mod transform;

pub use events::{Interrupt, INTERRUPT_KEY};
