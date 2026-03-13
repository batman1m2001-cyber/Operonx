//! Tracer trait — base interface for trace backends.
//!
//! Mirrors Python's `hush.core.tracing.base.Tracer`.
//! Implementations receive collected trace data (JSON Value) and send to their backend.
//! Called by FlushWorker in a background task via `tokio::spawn_blocking`.

use serde_json::Value;
use std::fmt::Debug;

/// Base trait for trace backends.
///
/// Implementations are `Send + Sync` so they can be shared across tokio tasks.
/// `flush()` is synchronous — FlushWorker runs it inside `spawn_blocking`.
pub trait Tracer: Send + Sync + Debug {
    /// Send trace data to the backend.
    ///
    /// `trace_data` is a JSON object matching the ui-hush-eyes IngestRequest format:
    /// `{"nodes": [...], "tags": [...], "request_id": "...", "summary": {...}, ...}`.
    fn flush(&self, trace_data: Value) -> Result<(), Box<dyn std::error::Error + Send + Sync>>;

    /// Static tags for this tracer instance.
    /// Merged with dynamic tags (from op `$tags` outputs) at flush time.
    fn tags(&self) -> Vec<String> {
        vec![]
    }

    /// Max stream context groups to keep per generator. `None` = keep all.
    /// Default: 100. Orphaned stream_context nodes are also removed.
    fn stream_trace_limit(&self) -> Option<usize> {
        Some(100)
    }
}
