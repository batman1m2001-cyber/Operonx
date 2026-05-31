//! `TraceEvent` — atomic record of one thing that happened during workflow execution.
//!
//! Mirrors Python [`operonx/core/tracing/events.py`](../../../../../operonx/core/tracing/events.py).
//!
//! Flat, immutable, no parent pointers. Tree/timeline/grouped shapes are
//! reconstructed by exporters from `(op_name, ctx)` tuples and `GroupStart`/
//! `GroupEnd` markers in the stream.
//!
//! Part of the 0.8.0 event-stream tracing redesign — replaces the legacy
//! `TraceCollector` + `flush_worker` shape entirely.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The kinds of events ops + scheduler can emit.
///
/// String values so events serialize cleanly to JSON for cross-runtime parity.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    /// Fired before op body runs. Payload: `{inputs: dict}`.
    OpStart,
    /// Fired after op finishes (any reason).
    /// Payload: `{outputs: dict, status: str, duration_ms: float, yield_count: int}`.
    /// Status is one of: `"ok"`, `"error"`, `"cancelled"`.
    OpEnd,
    /// Fired per-yield from a generator op (gated by `@op(emit_yields=N)`).
    /// Payload: `{yielded: dict, idx: int}`. `idx` is the absolute yield index.
    OpYield,
    /// User-attached metadata, scoped to the currently executing `(op, ctx)`
    /// via the `current_op_var` thread-local. Payload: `{key: str, value: Any}`.
    Annotation,
    /// Synthetic — emitted by the `GroupBy` processor at boundary detection,
    /// or by `EventEmitter::group()` scope. Payload: `{name: str, ...metadata}`.
    GroupStart,
    /// Closes a `GroupStart`. Payload: `{name: str, status: str}`.
    /// Status is one of: `"ok"`, `"truncated"` (pipeline shutdown closed
    /// open groups).
    GroupEnd,
    /// Token / cost report from an LLM op. MUST be emitted before the
    /// matching `OpEnd` for the same op.
    /// Payload: `{model: str, prompt_tokens: int, completion_tokens: int,
    /// total_tokens: int, cost_usd: float}`.
    LlmUsage,
    /// Reference to a binary blob stored in the pipeline's MediaStore.
    /// Bytes live in the store, not the event. Payload: `{handle: str,
    /// mime: str, size_bytes: int}`.
    MediaRef,
}

impl EventKind {
    /// Match Python's `EventKind.value` strings exactly — used for JSON
    /// wire-format parity.
    pub fn as_str(&self) -> &'static str {
        match self {
            EventKind::OpStart => "op_start",
            EventKind::OpEnd => "op_end",
            EventKind::OpYield => "op_yield",
            EventKind::Annotation => "annotation",
            EventKind::GroupStart => "group_start",
            EventKind::GroupEnd => "group_end",
            EventKind::LlmUsage => "llm_usage",
            EventKind::MediaRef => "media_ref",
        }
    }
}

/// Context tuple — matches the scheduler's context tuple semantics:
/// `["main"]` is the root, `["main", "yield_0"]` is the first item of a
/// generator, etc. Exporters that build trees walk the prefix.
pub type EventCtx = Vec<String>;

/// One atomic record of what happened.
///
/// Immutable post-construction. Sortable by `(timestamp, seq)` — `seq`
/// breaks ties when many events land in the same microsecond.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceEvent {
    /// UUID — opaque, for dedup and idempotent partial flushes.
    pub event_id: String,

    /// Per-`engine.start()` call. Same as today's `request_id`.
    pub request_id: String,

    pub kind: EventKind,

    /// `None` for synthetic events (group_start, group_end).
    #[serde(default)]
    pub op_name: Option<String>,

    /// Scheduler context tuple. `[]` for events not bound to a specific op.
    #[serde(default)]
    pub ctx: EventCtx,

    /// UTC.
    pub timestamp: DateTime<Utc>,

    /// Monotonic sequence number per emitter, for tiebreak when many events
    /// share a timestamp.
    pub seq: u64,

    /// Kind-specific. See `EventKind` docs for the schema per kind.
    #[serde(default)]
    pub payload: BTreeMap<String, Value>,
}

impl TraceEvent {
    /// Order by `(timestamp, seq)` so sorted iteration is stable across
    /// events with identical timestamps.
    pub fn sort_key(&self) -> (DateTime<Utc>, u64) {
        (self.timestamp, self.seq)
    }
}

impl PartialEq for TraceEvent {
    fn eq(&self, other: &Self) -> bool {
        self.event_id == other.event_id
    }
}

impl Eq for TraceEvent {}

impl PartialOrd for TraceEvent {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for TraceEvent {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.sort_key().cmp(&other.sort_key())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn evt(seq: u64, ts: DateTime<Utc>) -> TraceEvent {
        TraceEvent {
            event_id: format!("e{seq}"),
            request_id: "req-1".into(),
            kind: EventKind::OpStart,
            op_name: Some("main.x".into()),
            ctx: vec!["main".into()],
            timestamp: ts,
            seq,
            payload: BTreeMap::new(),
        }
    }

    #[test]
    fn event_kind_strings_match_python() {
        assert_eq!(EventKind::OpStart.as_str(), "op_start");
        assert_eq!(EventKind::OpEnd.as_str(), "op_end");
        assert_eq!(EventKind::OpYield.as_str(), "op_yield");
        assert_eq!(EventKind::Annotation.as_str(), "annotation");
        assert_eq!(EventKind::GroupStart.as_str(), "group_start");
        assert_eq!(EventKind::GroupEnd.as_str(), "group_end");
        assert_eq!(EventKind::LlmUsage.as_str(), "llm_usage");
        assert_eq!(EventKind::MediaRef.as_str(), "media_ref");
    }

    #[test]
    fn event_kind_json_serializes_as_snake_case_string() {
        let s = serde_json::to_string(&EventKind::OpStart).unwrap();
        assert_eq!(s, "\"op_start\"");
        let s = serde_json::to_string(&EventKind::LlmUsage).unwrap();
        assert_eq!(s, "\"llm_usage\"");
    }

    #[test]
    fn ordering_uses_timestamp_then_seq() {
        let t0 = Utc::now();
        let t1 = t0 + chrono::Duration::milliseconds(1);
        let a = evt(0, t0);
        let b = evt(0, t1);
        let c = evt(1, t0);
        assert!(a < b);
        assert!(a < c); // same ts, smaller seq wins
        assert!(c < b); // later ts wins over earlier ts + bigger seq
    }

    #[test]
    fn trace_event_round_trips_through_json() {
        let mut payload = BTreeMap::new();
        payload.insert("inputs".into(), json!({"x": 1}));
        let e = TraceEvent {
            event_id: "e1".into(),
            request_id: "r1".into(),
            kind: EventKind::OpStart,
            op_name: Some("main.x".into()),
            ctx: vec!["main".into()],
            timestamp: Utc::now(),
            seq: 0,
            payload,
        };
        let s = serde_json::to_string(&e).unwrap();
        let back: TraceEvent = serde_json::from_str(&s).unwrap();
        assert_eq!(back.event_id, e.event_id);
        assert_eq!(back.kind, EventKind::OpStart);
        assert_eq!(back.payload.get("inputs"), e.payload.get("inputs"));
    }
}
