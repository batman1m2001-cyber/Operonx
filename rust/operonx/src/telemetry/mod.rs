//! Telemetry — tracers and observability backends.
//!
//! Mirrors Python `operon/telemetry/`. Note: `OTELTracer` is deferred to v0.7
//! (see §6a of MIGRATION_rust.md); the `otel` feature flag is intentionally absent.

pub mod backends;
pub mod plugin;
pub mod tracers;

pub use plugin::{register_all, register_langfuse, LangfuseResource};
pub use tracers::{LangfuseTracer, OperonEyesTracer};
