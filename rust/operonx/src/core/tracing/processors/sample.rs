//! Sampling processor — keep a random subset of calls by request_id.
//!
//! Mirrors Python `processors/sample.py`. Decision is per-`request_id`:
//! events for a given call are all kept or all dropped together. Hashing
//! request_id to a stable [0, 1) float gives deterministic sampling —
//! useful for reproducing prod issues.

use std::collections::HashMap;

use crate::core::tracing::events::TraceEvent;
use crate::core::tracing::pipeline::Processor;

pub struct Sample {
    rate: f64,
}

impl Sample {
    pub fn new(rate: f64) -> Self {
        assert!(
            (0.0..=1.0).contains(&rate),
            "rate must be in [0.0, 1.0], got {rate}"
        );
        Self { rate }
    }
}

impl Processor for Sample {
    fn name(&self) -> &'static str {
        "Sample"
    }
    fn process(&self, events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        if self.rate >= 1.0 {
            return events;
        }
        if self.rate <= 0.0 {
            return Vec::new();
        }
        let mut decisions: HashMap<String, bool> = HashMap::new();
        events
            .into_iter()
            .filter(|e| {
                *decisions
                    .entry(e.request_id.clone())
                    .or_insert_with(|| hash_unit(&e.request_id) < self.rate)
            })
            .collect()
    }
}

/// Stable [0.0, 1.0) hash of a request_id. Uses the first 8 bytes of a
/// DefaultHasher result rather than MD5 — keeps the dep tree small; the
/// distribution is good enough for sampling (not a security boundary).
fn hash_unit(s: &str) -> f64 {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut h = DefaultHasher::new();
    s.hash(&mut h);
    let n = h.finish();
    (n as f64) / (u64::MAX as f64 + 1.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::tracing::events::EventKind;
    use chrono::Utc;
    use std::collections::BTreeMap;

    fn ev(req: &str) -> TraceEvent {
        TraceEvent {
            event_id: "e".into(),
            request_id: req.into(),
            kind: EventKind::OpStart,
            op_name: None,
            ctx: vec![],
            timestamp: Utc::now(),
            seq: 0,
            payload: BTreeMap::new(),
        }
    }

    #[test]
    fn rate_one_passes_everything() {
        let out = Sample::new(1.0).process(vec![ev("r1"), ev("r2")]);
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn rate_zero_drops_everything() {
        let out = Sample::new(0.0).process(vec![ev("r1"), ev("r2")]);
        assert_eq!(out.len(), 0);
    }

    #[test]
    fn same_request_id_decided_once() {
        // Sample at 0.001 — almost certain "r1" gets dropped. All events
        // sharing "r1" should land the same way (no per-event reroll).
        let s = Sample::new(0.001);
        let out = s.process(vec![ev("r1"), ev("r1"), ev("r1")]);
        assert!(out.is_empty() || out.len() == 3);
    }
}
