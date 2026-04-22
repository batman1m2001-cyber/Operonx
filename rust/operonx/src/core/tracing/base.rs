//! [`Tracer`] trait — what telemetry backends implement.
//!
//! Mirrors Python [`operon/core/tracing/base.py`](../../../../operon/core/tracing/base.py).
//!
//! Per plan §4b.3 `flush()` is **sync**, not async — Python runs it in a
//! thread pool. Rust mirrors this via `tokio::task::spawn_blocking`.
//! Tracers that need HTTP inside `flush()` use `reqwest::blocking::Client`
//! (or `tokio::runtime::Handle::block_on` on the blocking thread).

use std::fmt::Debug;

use super::models::TraceData;
use super::trace_filter::TraceFilter;
use crate::core::exceptions::OperonError;

/// Base trait for telemetry backends.
pub trait Tracer: Send + Sync + Debug {
    /// Short identifier used in log lines. Defaults to `"unknown"`.
    fn name(&self) -> &str {
        "unknown"
    }

    /// Static tags merged onto every flushed trace. Defaults to no tags.
    fn tags(&self) -> &[String] {
        &[]
    }

    /// Per-tracer sampling cap on `stream_context` groups. `None` = keep all.
    fn stream_trace_limit(&self) -> Option<usize> {
        Some(100)
    }

    /// Optional node filter applied before flushing.
    fn trace_filter(&self) -> Option<&TraceFilter> {
        None
    }

    /// Return a serializable config dict for Rust-side export.
    ///
    /// Per §6b.1, tracers constructed with a `resource=` key return `None`
    /// here (Rust can't rehydrate from a `ResourceHub` that doesn't own the
    /// config on the Rust side). Direct-config tracers return `Some(dict)`.
    fn to_config_dict(&self) -> Option<serde_json::Value> {
        None
    }

    /// Push one fully-collected trace payload to the backend.
    ///
    /// Called by [`FlushWorker`](super::flush_worker::FlushWorker) on a
    /// blocking worker thread — implementations may do sync HTTP / file IO
    /// freely.
    fn flush(&self, trace: &TraceData) -> Result<(), OperonError>;
}
