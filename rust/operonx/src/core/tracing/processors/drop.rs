//! Filtering processors — drop events from the stream.
//!
//! Mirrors Python `processors/drop.py` — four stateless one-pass filters:
//! `DropOps`, `KeepOps`, `DropKinds`, `DropEmpty`.

use std::collections::HashSet;

use crate::core::tracing::events::{EventKind, TraceEvent};
use crate::core::tracing::pipeline::Processor;

fn short_name(full: &str) -> &str {
    full.rsplit_once('.').map(|(_, s)| s).unwrap_or(full)
}

/// Drop events whose `op_name` is in the configured set (by either full
/// name like `"g.picker"` or bare name like `"picker"`).
pub struct DropOps {
    names: HashSet<String>,
}

impl DropOps {
    pub fn new(names: impl IntoIterator<Item = impl Into<String>>) -> Self {
        Self {
            names: names.into_iter().map(Into::into).collect(),
        }
    }
}

impl Processor for DropOps {
    fn name(&self) -> &'static str {
        "DropOps"
    }
    fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        events
            .into_iter()
            .filter(|e| {
                e.op_name
                    .as_ref()
                    .map(|n| !self.names.contains(n) && !self.names.contains(short_name(n)))
                    .unwrap_or(true)
            })
            .collect()
    }
}

/// Keep only events whose `op_name` is in the configured set; synthetic
/// events (`op_name = None` — group_start/group_end) always pass.
pub struct KeepOps {
    names: HashSet<String>,
}

impl KeepOps {
    pub fn new(names: impl IntoIterator<Item = impl Into<String>>) -> Self {
        Self {
            names: names.into_iter().map(Into::into).collect(),
        }
    }
}

impl Processor for KeepOps {
    fn name(&self) -> &'static str {
        "KeepOps"
    }
    fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        events
            .into_iter()
            .filter(|e| match &e.op_name {
                None => true,
                Some(n) => self.names.contains(n) || self.names.contains(short_name(n)),
            })
            .collect()
    }
}

/// Drop events whose `kind` is in the configured set.
pub struct DropKinds {
    kinds: HashSet<EventKind>,
}

impl DropKinds {
    pub fn new(kinds: impl IntoIterator<Item = EventKind>) -> Self {
        Self {
            kinds: kinds.into_iter().collect(),
        }
    }
}

impl Processor for DropKinds {
    fn name(&self) -> &'static str {
        "DropKinds"
    }
    fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        events
            .into_iter()
            .filter(|e| !self.kinds.contains(&e.kind))
            .collect()
    }
}

/// Drop `OpEnd` events whose outputs are empty or all-null. Pre-yields all
/// non-OpEnd events untouched.
pub struct DropEmpty;

impl Processor for DropEmpty {
    fn name(&self) -> &'static str {
        "DropEmpty"
    }
    fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        events
            .into_iter()
            .filter(|e| {
                if e.kind != EventKind::OpEnd {
                    return true;
                }
                let outputs = e.payload.get("outputs");
                match outputs.and_then(|v| v.as_object()) {
                    None => false, // missing or non-object → drop
                    Some(m) if m.is_empty() => false,
                    Some(m) => m.values().any(|v| !v.is_null()),
                }
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use serde_json::json;
    use std::collections::BTreeMap;

    fn ev(
        kind: EventKind,
        op_name: Option<&str>,
        payload: BTreeMap<String, serde_json::Value>,
    ) -> TraceEvent {
        TraceEvent {
            event_id: "e".into(),
            request_id: "r".into(),
            kind,
            op_name: op_name.map(String::from),
            ctx: vec![],
            timestamp: Utc::now(),
            seq: 0,
            payload,
        }
    }

    #[test]
    fn drop_ops_matches_full_and_short_name() {
        let p = DropOps::new(vec!["picker"]);
        let kept = p.process(vec![
            ev(EventKind::OpStart, Some("g.picker"), BTreeMap::new()),
            ev(EventKind::OpStart, Some("g.other"), BTreeMap::new()),
        ]);
        assert_eq!(kept.len(), 1);
        assert_eq!(kept[0].op_name.as_deref(), Some("g.other"));
    }

    #[test]
    fn keep_ops_passes_synthetic_events() {
        let p = KeepOps::new(vec!["a"]);
        let kept = p.process(vec![
            ev(EventKind::GroupStart, None, BTreeMap::new()),
            ev(EventKind::OpStart, Some("a"), BTreeMap::new()),
            ev(EventKind::OpStart, Some("b"), BTreeMap::new()),
        ]);
        assert_eq!(kept.len(), 2);
    }

    #[test]
    fn drop_kinds_filters_by_event_kind() {
        let p = DropKinds::new(vec![EventKind::OpYield]);
        let kept = p.process(vec![
            ev(EventKind::OpYield, None, BTreeMap::new()),
            ev(EventKind::OpStart, None, BTreeMap::new()),
        ]);
        assert_eq!(kept.len(), 1);
        assert_eq!(kept[0].kind, EventKind::OpStart);
    }

    #[test]
    fn drop_empty_drops_all_null_op_end() {
        let mut empty = BTreeMap::new();
        empty.insert("outputs".into(), json!({"a": null, "b": null}));
        let mut some = BTreeMap::new();
        some.insert("outputs".into(), json!({"a": 1}));
        let kept = DropEmpty.process(vec![
            ev(EventKind::OpEnd, None, empty),
            ev(EventKind::OpEnd, None, some),
        ]);
        assert_eq!(kept.len(), 1);
    }
}
