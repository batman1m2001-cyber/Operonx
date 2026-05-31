//! Telemetry exporters — receive post-processor event batches and ship to
//! external observability backends.
//!
//! Mirrors Python `operonx/telemetry/exporters/`. Tier-1 lean builds get
//! the JSON file exporter from `core/tracing/exporters`; Langfuse +
//! related backends live here under their respective feature flags.

#[cfg(feature = "langfuse")]
pub mod langfuse;

#[cfg(feature = "langfuse")]
pub use langfuse::LangfuseExporter;
