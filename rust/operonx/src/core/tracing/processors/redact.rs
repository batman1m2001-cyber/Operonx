//! PII / secrets redaction processor.
//!
//! Mirrors Python `processors/redact.py` — replaces values for matching
//! keys with `<redacted>` (or custom marker). Walks one level deep into
//! `inputs` / `outputs` / `yielded` / `value` payload entries.

use std::collections::HashSet;

use serde_json::Value;

use crate::core::tracing::events::TraceEvent;
use crate::core::tracing::pipeline::Processor;

pub struct RedactKeys {
    keys: HashSet<String>,
    marker: String,
}

impl RedactKeys {
    pub fn new(keys: impl IntoIterator<Item = impl Into<String>>) -> Self {
        Self {
            keys: keys.into_iter().map(Into::into).collect(),
            marker: "<redacted>".into(),
        }
    }

    pub fn with_marker(mut self, marker: impl Into<String>) -> Self {
        self.marker = marker.into();
        self
    }

    fn redact_io(&self, value: &mut Value) {
        let Value::Object(map) = value else { return };
        for k in self.keys.iter() {
            if map.contains_key(k) {
                map.insert(k.clone(), Value::String(self.marker.clone()));
            }
        }
    }
}

impl Processor for RedactKeys {
    fn name(&self) -> &'static str {
        "RedactKeys"
    }
    fn process(&self, mut events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        for e in events.iter_mut() {
            for io_key in ["inputs", "outputs", "yielded", "value"] {
                if let Some(v) = e.payload.get_mut(io_key) {
                    self.redact_io(v);
                }
            }
        }
        events
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::tracing::events::EventKind;
    use chrono::Utc;
    use serde_json::json;
    use std::collections::BTreeMap;

    #[test]
    fn redacts_matching_keys_in_inputs() {
        let mut payload = BTreeMap::new();
        payload.insert(
            "inputs".into(),
            json!({"api_key": "sk-secret", "model": "gpt-4o"}),
        );
        let e = TraceEvent {
            event_id: "e".into(),
            request_id: "r".into(),
            kind: EventKind::OpStart,
            op_name: Some("op".into()),
            ctx: vec![],
            timestamp: Utc::now(),
            seq: 0,
            payload,
        };
        let p = RedactKeys::new(vec!["api_key"]);
        let out = p.process(vec![e]);
        let inputs = out[0].payload.get("inputs").unwrap();
        assert_eq!(inputs.get("api_key"), Some(&json!("<redacted>")));
        assert_eq!(inputs.get("model"), Some(&json!("gpt-4o")));
    }

    #[test]
    fn custom_marker() {
        let mut payload = BTreeMap::new();
        payload.insert("outputs".into(), json!({"token": "abc"}));
        let e = TraceEvent {
            event_id: "e".into(),
            request_id: "r".into(),
            kind: EventKind::OpEnd,
            op_name: None,
            ctx: vec![],
            timestamp: Utc::now(),
            seq: 0,
            payload,
        };
        let p = RedactKeys::new(vec!["token"]).with_marker("***");
        let out = p.process(vec![e]);
        assert_eq!(
            out[0].payload.get("outputs").unwrap().get("token"),
            Some(&json!("***"))
        );
    }
}
