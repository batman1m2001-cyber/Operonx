//! Truncation processor — clip large strings in event payloads.
//!
//! Mirrors Python `processors/truncate.py`. Walks one level deep into
//! `inputs` / `outputs` / `yielded` / `value` payload entries; replaces
//! strings over `max_bytes` with `"<head>...[truncated N bytes]"`.

use serde_json::Value;

use crate::core::tracing::events::TraceEvent;
use crate::core::tracing::pipeline::Processor;

pub struct TruncateIO {
    max_bytes: usize,
}

impl TruncateIO {
    pub fn new(max_bytes: usize) -> Self {
        Self { max_bytes }
    }

    fn truncate_string(&self, s: &str) -> String {
        if s.len() <= self.max_bytes {
            return s.to_string();
        }
        // Find the largest byte boundary ≤ max_bytes — `&s[..max_bytes]`
        // would panic mid-char on multi-byte UTF-8.
        let mut end = self.max_bytes;
        while end > 0 && !s.is_char_boundary(end) {
            end -= 1;
        }
        let head = &s[..end];
        format!("{head}...[truncated {} bytes]", s.len() - end)
    }

    fn truncate_io(&self, value: &mut Value) {
        match value {
            Value::String(s) => {
                if s.len() > self.max_bytes {
                    *s = self.truncate_string(s);
                }
            }
            Value::Object(map) => {
                for (_k, v) in map.iter_mut() {
                    if let Value::String(s) = v {
                        if s.len() > self.max_bytes {
                            *v = Value::String(self.truncate_string(s));
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

impl Processor for TruncateIO {
    fn name(&self) -> &'static str {
        "TruncateIO"
    }
    fn process(&self, mut events: Vec<TraceEvent>) -> Vec<TraceEvent> {
        for e in events.iter_mut() {
            for io_key in ["inputs", "outputs", "yielded", "value"] {
                if let Some(v) = e.payload.get_mut(io_key) {
                    self.truncate_io(v);
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
    fn truncates_long_string_in_inputs() {
        let mut payload = BTreeMap::new();
        let long: String = "x".repeat(3000);
        payload.insert("inputs".into(), json!({"prompt": long}));
        let e = TraceEvent {
            event_id: "e".into(),
            request_id: "r".into(),
            kind: EventKind::OpStart,
            op_name: None,
            ctx: vec![],
            timestamp: Utc::now(),
            seq: 0,
            payload,
        };
        let p = TruncateIO::new(100);
        let out = p.process(vec![e]);
        let prompt = out[0].payload.get("inputs").unwrap().get("prompt").unwrap().as_str().unwrap();
        assert!(prompt.contains("...[truncated"));
        assert!(prompt.len() < 3000);
    }

    #[test]
    fn short_strings_pass_through() {
        let mut payload = BTreeMap::new();
        payload.insert("inputs".into(), json!({"prompt": "hi"}));
        let e = TraceEvent {
            event_id: "e".into(),
            request_id: "r".into(),
            kind: EventKind::OpStart,
            op_name: None,
            ctx: vec![],
            timestamp: Utc::now(),
            seq: 0,
            payload,
        };
        let p = TruncateIO::new(100);
        let out = p.process(vec![e]);
        assert_eq!(out[0].payload.get("inputs").unwrap().get("prompt"), Some(&json!("hi")));
    }
}
