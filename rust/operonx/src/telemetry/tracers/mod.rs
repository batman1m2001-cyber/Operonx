//! Tracer implementations — OperonEyesTracer, LangfuseTracer.
//!
//! Mirrors Python `operon/telemetry/tracers/`. `otel.rs` is deferred to v0.7
//! (see MIGRATION_rust.md §6a).

pub mod base;
pub mod langfuse;
pub mod operon_eyes;

pub use langfuse::LangfuseTracer;
pub use operon_eyes::{OperonEyesTracer, DEFAULT_HOST, DEFAULT_PORT};
