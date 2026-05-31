//! Group / aggregate processors.
//!
//! Mirrors Python `processors/group.py`. Two related processors:
//!
//! - **`GroupBy`** — synthesises `GroupStart` / `GroupEnd` boundaries
//!   based on a `(op_name, payload-key)` predicate. Useful for callbot
//!   per-turn grouping where the turn boundary is implicit in the event
//!   stream (a `decider`-op annotation with `turn_id` changing).
//!
//! - **`Aggregate`** — collapses runs of same-kind, same-op events into a
//!   single summary event. Used to keep Langfuse observation count
//!   bounded when an op yields many frames.
//!
//! Both are stateful — they hold an in-flight group / aggregation across
//! a `process()` call. The Python impl is feature-complete; the Rust port
//! ships the public API + a no-op implementation for both, with a TODO to
//! flesh out the grouping logic when the callbot's Langfuse exporter port
//! exercises them. The default callbot pipeline doesn't compose these
//! processors so we're not gating Phase 2 on them.

use crate::core::tracing::events::TraceEvent;
use crate::core::tracing::pipeline::Processor;

/// Synthesise group boundary events based on `(op_name, payload-key)` shifts.
///
/// Phase-1 status: stub. The default callbot Langfuse exporter does its
/// own grouping; this processor isn't on the critical path. Port deferred
/// until a graph that needs it shows up.
pub struct GroupBy {
    pub op_name: String,
    pub key: String,
}

impl GroupBy {
    pub fn new(op_name: impl Into<String>, key: impl Into<String>) -> Self {
        Self {
            op_name: op_name.into(),
            key: key.into(),
        }
    }
}

impl Processor for GroupBy {
    fn name(&self) -> &'static str {
        "GroupBy"
    }
    fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        events
    }
}

/// Collapse runs of repeated same-kind same-op events.
///
/// Phase-1 status: stub (same rationale as `GroupBy`).
pub struct Aggregate {
    pub kinds: Vec<String>,
}

impl Aggregate {
    pub fn new(kinds: impl IntoIterator<Item = impl Into<String>>) -> Self {
        Self {
            kinds: kinds.into_iter().map(Into::into).collect(),
        }
    }
}

impl Processor for Aggregate {
    fn name(&self) -> &'static str {
        "Aggregate"
    }
    fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        events
    }
}
