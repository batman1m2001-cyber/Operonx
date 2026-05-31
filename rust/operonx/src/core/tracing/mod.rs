//! Tracing — 0.8.0 event-stream pipeline + legacy collector during migration.
//!
//! Mirrors Python `operonx/core/tracing/`.
//!
//! The 0.8.0 redesign added `events`, `emitter`, `pipeline`, `processors`,
//! `legacy`, and `exporters/local_file` to replace the old `collector` +
//! `flush_worker` + `labels` shape. During the Rust sync we keep the legacy
//! modules compiled until engine.rs / telemetry/tracers/* migrate off them
//! — call sites switch one by one.

pub mod base;
pub mod collector;
pub mod emitter;
pub mod events;
pub mod exporters;
pub mod flush_worker;
pub mod labels;
pub mod legacy;
pub mod local;
pub mod models;
pub mod pipeline;
pub mod processors;
pub mod trace_filter;

pub use base::Tracer;
pub use collector::TraceCollector;
pub use emitter::{EventEmitter, CURRENT_OP};
pub use events::{EventCtx, EventKind, TraceEvent};
pub use flush_worker::FlushWorker;
pub use labels::{label, LabelsStore};
pub use local::LocalTracer;
pub use models::{TraceData, TraceNode, TraceSummary};
pub use pipeline::{
    AtScheduledExit, ExportMetadata, Exporter, FlushOnSize, FlushStrategy, Processor,
    TracePipeline, TracePipelineBuilder,
};
pub use trace_filter::TraceFilter;
