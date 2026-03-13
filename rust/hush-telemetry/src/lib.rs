//! hush-telemetry — Tracing backends for hush-icore.
//!
//! Provides external tracer implementations that send trace data to
//! observability platforms. All tracers implement `hush_icore::tracing::Tracer`.
//!
//! - `HushEyesTracer` — HTTP POST to ui-hush-eyes local server
//! - `LangfuseTracer` — Batch ingestion to Langfuse REST API
//! - `OtelTracer` — OpenTelemetry span export (feature-gated)

pub mod hush_eyes;
pub mod langfuse;

#[cfg(feature = "otel")]
pub mod otel;

pub use hush_eyes::HushEyesTracer;
pub use langfuse::LangfuseTracer;
