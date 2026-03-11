//! Tracing system — collect execution traces and flush to backends.
//!
//! Mirrors Python's `hush.core.tracing` package:
//! - `tracer.rs` — Tracer trait (base interface)
//! - `models.rs` — TraceNode, TraceSummary
//! - `collector.rs` — TraceCollector (builds trace tree from EngineState)
//! - `flush_worker.rs` — FlushWorker (background flush via tokio)
//! - `local.rs` — LocalTracer (JSON file output)

pub mod collector;
pub mod flush_worker;
pub mod local;
pub mod models;
pub mod tracer;

pub use collector::TraceCollector;
pub use flush_worker::FlushWorker;
pub use local::LocalTracer;
pub use models::{TraceNode, TraceSummary};
pub use tracer::Tracer;
