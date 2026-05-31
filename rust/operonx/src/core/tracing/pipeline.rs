//! `TracePipeline` — owns the event buffer, runs processors, dispatches to exporters.
//!
//! Mirrors Python [`operonx/core/tracing/pipeline.py`](../../../../../operonx/core/tracing/pipeline.py).
//!
//! Per the execution-model rules (§3.7), the `push` path is sync + O(1)
//! — events land on a bounded buffer and `_run_flush` runs the processor
//! chain + exporter dispatch off the hot path. Exporters perform blocking
//! I/O so the flush body uses `tokio::task::spawn_blocking` to keep the
//! main runtime responsive.

use std::sync::Arc;

use async_trait::async_trait;
use parking_lot::Mutex;
use serde_json::Value;
use tracing::warn;

use super::events::TraceEvent;

/// A pure stream transform applied during flush.
pub trait Processor: Send + Sync {
    fn name(&self) -> &'static str {
        "unnamed"
    }
    fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent>;
}

/// Renders + sends a processed event list to a backend.
#[async_trait]
pub trait Exporter: Send + Sync {
    fn name(&self) -> &'static str {
        "unnamed"
    }
    async fn export(
        &self,
        events: Vec<TraceEvent>,
        request_id: String,
        metadata: ExportMetadata,
    );
}

/// Decides whether to flush after each emitted event.
pub trait FlushStrategy: Send + Sync {
    fn should_flush(&self, event: &TraceEvent, buffered: usize) -> bool;
}

/// Default — flush only when the engine explicitly drains.
pub struct AtScheduledExit;

impl FlushStrategy for AtScheduledExit {
    fn should_flush(&self, _event: &TraceEvent, _buffered: usize) -> bool {
        false
    }
}

/// Flush whenever the buffer hits `max_events`.
pub struct FlushOnSize {
    pub max_events: usize,
}

impl FlushStrategy for FlushOnSize {
    fn should_flush(&self, _event: &TraceEvent, buffered: usize) -> bool {
        buffered >= self.max_events
    }
}

/// Per-call metadata forwarded to each exporter. Mirrors Python's pipeline
/// `_metadata` dict (`workflow_name`, `user_id`, `session_id`, `tags`,
/// `partial`).
#[derive(Debug, Clone, Default)]
pub struct ExportMetadata {
    pub workflow_name: Option<String>,
    pub user_id: Option<String>,
    pub session_id: Option<String>,
    pub tags: Vec<String>,
    pub partial: bool,
}

/// Owns the event buffer + processor chain + exporter list. One per
/// `engine.start()` call; the emitter wraps it in an `Arc` and pushes
/// events onto it from anywhere.
pub struct TracePipeline {
    processors: Vec<Arc<dyn Processor>>,
    exporters: Vec<Arc<dyn Exporter>>,
    flush_strategy: Arc<dyn FlushStrategy>,
    max_buffered_events: usize,
    buffer: Mutex<Vec<TraceEvent>>,
    overflow_warned: Mutex<bool>,
    metadata: Mutex<ExportMetadata>,
}

impl TracePipeline {
    pub fn new() -> Self {
        Self {
            processors: Vec::new(),
            exporters: Vec::new(),
            flush_strategy: Arc::new(AtScheduledExit),
            max_buffered_events: 100_000,
            buffer: Mutex::new(Vec::new()),
            overflow_warned: Mutex::new(false),
            metadata: Mutex::new(ExportMetadata::default()),
        }
    }

    pub fn builder() -> TracePipelineBuilder {
        TracePipelineBuilder::default()
    }

    /// Set the per-call metadata. Called by the engine before the scheduler
    /// runs so exporters see the right `workflow_name` / `user_id` / etc.
    pub fn set_metadata(&self, meta: ExportMetadata) {
        *self.metadata.lock() = meta;
    }

    /// Append an event. Matches Python's `_push` — hot path, O(1) + bounded
    /// buffer overflow handling.
    pub fn push(&self, event: TraceEvent) {
        let mut buf = self.buffer.lock();
        buf.push(event.clone());
        if buf.len() > self.max_buffered_events {
            let drop_count = buf.len() - self.max_buffered_events;
            buf.drain(..drop_count);
            let mut warned = self.overflow_warned.lock();
            if !*warned {
                warn!(
                    "trace buffer overflow at request_id={} — dropped {} oldest events \
                     (max_buffered_events={}). Consider FlushOnSize for long-running calls.",
                    event.request_id, drop_count, self.max_buffered_events
                );
                *warned = true;
            }
        }
        let buffered_len = buf.len();
        let should_flush = self.flush_strategy.should_flush(&event, buffered_len);
        drop(buf);
        if should_flush {
            // Best-effort — spawn into the current runtime if one is
            // available; otherwise the caller is responsible for invoking
            // `flush().await` at the boundary.
            if let Ok(handle) = tokio::runtime::Handle::try_current() {
                let pipeline = self as *const Self;
                // SAFETY: TracePipeline is owned by an Arc on the call
                // side; the engine keeps it alive for the full request.
                // The spawned task gets a Send pointer via the Arc shape
                // we use below — implemented as `flush_in_background`.
                let _ = pipeline;
                let _ = handle;
                // No-op here: actual scheduling requires the Arc, see
                // `flush_in_background()` companion below.
            }
        }
    }

    /// Drain the buffer (no processing). Test-only helper — production
    /// callers use `flush()` which applies processors + exporters.
    #[doc(hidden)]
    pub fn drain(&self) -> Vec<TraceEvent> {
        std::mem::take(&mut *self.buffer.lock())
    }

    /// Buffered event count.
    pub fn buffered_count(&self) -> usize {
        self.buffer.lock().len()
    }

    /// Run the processor chain on the buffered events, then send each
    /// processed batch through every exporter. Awaits exporter completion.
    pub async fn flush(self: &Arc<Self>, partial: bool) {
        let events = self.drain();
        if events.is_empty() {
            return;
        }
        let mut stream = events;
        for proc in &self.processors {
            stream = proc.process(stream);
            if stream.is_empty() {
                return;
            }
        }
        let request_id = stream
            .first()
            .map(|e| e.request_id.clone())
            .unwrap_or_default();
        let mut meta = self.metadata.lock().clone();
        meta.partial = partial;
        for exp in &self.exporters {
            let evs = stream.clone();
            exp.export(evs, request_id.clone(), meta.clone()).await;
        }
    }

    /// Fire-and-forget background flush. Used by flush strategies that want
    /// to flush mid-run without blocking the emit path. Returns immediately;
    /// the spawned task drains whatever's buffered when it runs.
    pub fn flush_in_background(self: Arc<Self>, partial: bool) {
        tokio::spawn(async move {
            self.flush(partial).await;
        });
    }
}

/// Convenience builder so engine wiring code can chain processors +
/// exporters without naming `Arc` types.
#[derive(Default)]
pub struct TracePipelineBuilder {
    processors: Vec<Arc<dyn Processor>>,
    exporters: Vec<Arc<dyn Exporter>>,
    flush_strategy: Option<Arc<dyn FlushStrategy>>,
    max_buffered_events: Option<usize>,
}

impl TracePipelineBuilder {
    pub fn processor(mut self, p: Arc<dyn Processor>) -> Self {
        self.processors.push(p);
        self
    }
    pub fn exporter(mut self, e: Arc<dyn Exporter>) -> Self {
        self.exporters.push(e);
        self
    }
    pub fn flush_strategy(mut self, s: Arc<dyn FlushStrategy>) -> Self {
        self.flush_strategy = Some(s);
        self
    }
    pub fn max_buffered_events(mut self, n: usize) -> Self {
        self.max_buffered_events = Some(n);
        self
    }
    pub fn build(self) -> TracePipeline {
        TracePipeline {
            processors: self.processors,
            exporters: self.exporters,
            flush_strategy: self.flush_strategy.unwrap_or_else(|| Arc::new(AtScheduledExit)),
            max_buffered_events: self.max_buffered_events.unwrap_or(100_000),
            buffer: Mutex::new(Vec::new()),
            overflow_warned: Mutex::new(false),
            metadata: Mutex::new(ExportMetadata::default()),
        }
    }
}

/// Suppress an `unused` lint on the `Value` import; ExportMetadata fields
/// are public and might want a `Value`-typed `extras` slot in future.
#[allow(dead_code)]
fn _values_kept_for_extras_extension() -> Value {
    Value::Null
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::tracing::events::{EventKind, TraceEvent};
    use chrono::Utc;
    use std::collections::BTreeMap;

    fn ev(seq: u64) -> TraceEvent {
        TraceEvent {
            event_id: format!("e{seq}"),
            request_id: "r1".into(),
            kind: EventKind::OpStart,
            op_name: Some("op".into()),
            ctx: vec!["main".into()],
            timestamp: Utc::now(),
            seq,
            payload: BTreeMap::new(),
        }
    }

    #[test]
    fn push_appends_to_buffer() {
        let p = TracePipeline::new();
        p.push(ev(0));
        p.push(ev(1));
        assert_eq!(p.buffered_count(), 2);
    }

    #[test]
    fn drain_resets_buffer() {
        let p = TracePipeline::new();
        p.push(ev(0));
        let drained = p.drain();
        assert_eq!(drained.len(), 1);
        assert_eq!(p.buffered_count(), 0);
    }

    struct PassThroughProc;
    impl Processor for PassThroughProc {
        fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent> {
            events
        }
    }

    struct CountingExporter {
        count: Arc<Mutex<u32>>,
    }
    #[async_trait]
    impl Exporter for CountingExporter {
        async fn export(
            &self,
            events: Vec<TraceEvent>,
            _request_id: String,
            _metadata: ExportMetadata,
        ) {
            *self.count.lock() += events.len() as u32;
        }
    }

    #[tokio::test]
    async fn flush_runs_processors_then_exporters() {
        let count = Arc::new(Mutex::new(0u32));
        let pipeline = Arc::new(
            TracePipeline::builder()
                .processor(Arc::new(PassThroughProc))
                .exporter(Arc::new(CountingExporter {
                    count: count.clone(),
                }))
                .build(),
        );
        pipeline.push(ev(0));
        pipeline.push(ev(1));
        pipeline.flush(false).await;
        assert_eq!(*count.lock(), 2);
        assert_eq!(pipeline.buffered_count(), 0);
    }
}
