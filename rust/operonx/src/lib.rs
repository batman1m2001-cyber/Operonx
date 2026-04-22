//! # operonx
//!
//! High-performance Rust execution backend for Operon workflows.
//!
//! Mirrors the Python `operon` package at every level — folder structure,
//! type names, method names, OOP hierarchy. See [MIGRATION_rust.md](../../../MIGRATION_rust.md).
//!
//! ## Module layout
//!
//! - [`core`] — engine, ops, state, refs, registry, tracing, middleware
//! - [`providers`] — LLM, embedding, reranker, ONNX, Triton, auth
//! - [`telemetry`] — tracers and observability backends
//!
//! Phase 0 scaffold — see `MIGRATION_rust.md` §11 for phased implementation plan.

pub mod core;
pub mod providers;
pub mod telemetry;

// Re-export the proc macros from operonx-macros for ergonomic `use operonx::{op, resource, model};`
pub use operonx_macros::{model, op, resource};

// User-facing API (per MIGRATION_rust.md §3a.1).
pub use core::engine::{
    CollectMode, ExecutionHandle, FrameEvent, FrameSender, GraphEnvelope, Operon, OperonBuilder,
    Scheduler,
};
pub use core::exceptions::{OperonError, SUPPORTED_SCHEMA_VERSION};
pub use core::middleware::{Middleware, MiddlewareContext};
pub use core::registry::{OpEntry, OpKind, ResourceEntry};
