//! Built-in processors for the trace pipeline.
//!
//! Mirrors Python `operonx/core/tracing/processors/`. Each processor takes
//! `Vec<TraceEvent>` and returns a (possibly smaller / mutated) `Vec<TraceEvent>`.
//! Compose by ordering them in `TracePipeline::builder().processor(...)` —
//! execution is left-to-right, every event through every processor.

pub mod drop;
pub mod group;
pub mod redact;
pub mod sample;
pub mod truncate;

pub use drop::{DropEmpty, DropKinds, DropOps, KeepOps};
pub use group::{Aggregate, GroupBy};
pub use redact::RedactKeys;
pub use sample::Sample;
pub use truncate::TruncateIO;
