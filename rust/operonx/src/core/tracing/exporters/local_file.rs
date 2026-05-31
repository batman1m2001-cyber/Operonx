//! JSON file exporter — writes events to `<dir>/<request_id>.json`.
//!
//! Mirrors Python `core/tracing/exporters/local_file.py`. Zero external
//! deps. Replaces the legacy `LocalTracer`. Partial flushes append
//! one-JSON-per-line; the final flush rewrites the file as a sorted JSON
//! array so the on-disk shape is stable for grep/jq.

use std::fs;
use std::io::Write;
use std::path::PathBuf;

use async_trait::async_trait;
use serde::Serialize;
use serde_json::Value;
use tracing::warn;

use crate::core::tracing::events::TraceEvent;
use crate::core::tracing::pipeline::{ExportMetadata, Exporter};

pub struct JsonFileExporter {
    pub directory: PathBuf,
}

impl JsonFileExporter {
    pub fn new(directory: impl Into<PathBuf>) -> Self {
        Self {
            directory: directory.into(),
        }
    }

    /// Default directory: `~/.operonx/traces/`. Matches the Python default.
    pub fn default_dir() -> Self {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        Self::new(PathBuf::from(home).join(".operonx").join("traces"))
    }
}

#[derive(Serialize)]
struct SerializedEvent<'a> {
    event_id: &'a str,
    request_id: &'a str,
    kind: &'a str,
    op_name: Option<&'a str>,
    ctx: &'a [String],
    timestamp: String,
    seq: u64,
    payload: &'a std::collections::BTreeMap<String, Value>,
}

fn serialize(e: &TraceEvent) -> SerializedEvent<'_> {
    SerializedEvent {
        event_id: &e.event_id,
        request_id: &e.request_id,
        kind: e.kind.as_str(),
        op_name: e.op_name.as_deref(),
        ctx: &e.ctx,
        timestamp: e
            .timestamp
            .to_rfc3339_opts(chrono::SecondsFormat::Micros, true),
        seq: e.seq,
        payload: &e.payload,
    }
}

#[async_trait]
impl Exporter for JsonFileExporter {
    fn name(&self) -> &'static str {
        "JsonFileExporter"
    }
    async fn export(&self, events: Vec<TraceEvent>, request_id: String, metadata: ExportMetadata) {
        if events.is_empty() {
            return;
        }
        if let Err(e) = fs::create_dir_all(&self.directory) {
            warn!("JsonFileExporter: mkdir failed: {}", e);
            return;
        }
        let path = self.directory.join(format!("{request_id}.json"));
        let serialized: Vec<_> = events.iter().map(serialize).collect();

        if metadata.partial {
            // Append mode — one JSON object per line.
            let mut f = match fs::OpenOptions::new().create(true).append(true).open(&path) {
                Ok(f) => f,
                Err(e) => {
                    warn!("JsonFileExporter: append-open failed: {}", e);
                    return;
                }
            };
            for entry in &serialized {
                let line = serde_json::to_string(entry).unwrap_or_default();
                if let Err(e) = writeln!(f, "{line}") {
                    warn!("JsonFileExporter: write failed: {}", e);
                    return;
                }
            }
            return;
        }

        // Final flush: read any partial appends, merge, write sorted array.
        let mut existing: Vec<Value> = Vec::new();
        if path.exists() {
            if let Ok(text) = fs::read_to_string(&path) {
                let trimmed = text.trim();
                if trimmed.starts_with('[') {
                    if let Ok(Value::Array(arr)) = serde_json::from_str::<Value>(trimmed) {
                        existing = arr;
                    }
                } else {
                    for line in trimmed.lines() {
                        let line = line.trim();
                        if line.is_empty() {
                            continue;
                        }
                        if let Ok(v) = serde_json::from_str::<Value>(line) {
                            existing.push(v);
                        }
                    }
                }
            }
        }

        let mut merged: Vec<Value> = existing;
        for entry in &serialized {
            merged.push(serde_json::to_value(entry).unwrap_or(Value::Null));
        }
        merged.sort_by(|a, b| {
            let ts_a = a.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
            let ts_b = b.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
            let seq_a = a.get("seq").and_then(|v| v.as_u64()).unwrap_or(0);
            let seq_b = b.get("seq").and_then(|v| v.as_u64()).unwrap_or(0);
            ts_a.cmp(ts_b).then(seq_a.cmp(&seq_b))
        });

        let text = serde_json::to_string_pretty(&merged).unwrap_or_else(|_| "[]".into());
        if let Err(e) = fs::write(&path, text) {
            warn!("JsonFileExporter: final-write failed: {}", e);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::tracing::events::EventKind;
    use chrono::Utc;
    use std::collections::BTreeMap;
    use tempfile::TempDir;

    fn ev_at(req: &str, seq: u64, ts: chrono::DateTime<Utc>) -> TraceEvent {
        TraceEvent {
            event_id: format!("{req}-{seq}"),
            request_id: req.into(),
            kind: EventKind::OpStart,
            op_name: Some("op".into()),
            ctx: vec!["main".into()],
            timestamp: ts,
            seq,
            payload: BTreeMap::new(),
        }
    }

    fn ev(req: &str, seq: u64) -> TraceEvent {
        ev_at(req, seq, Utc::now())
    }

    #[tokio::test]
    async fn final_flush_writes_sorted_array() {
        let tmp = TempDir::new().unwrap();
        let exporter = JsonFileExporter::new(tmp.path());
        // Same timestamp → seq is the tiebreak; out-of-order input should
        // come out sorted ascending.
        let ts = Utc::now();
        let events = vec![ev_at("r1", 1, ts), ev_at("r1", 0, ts)];
        exporter
            .export(events, "r1".into(), ExportMetadata::default())
            .await;
        let written = fs::read_to_string(tmp.path().join("r1.json")).unwrap();
        let arr: Vec<Value> = serde_json::from_str(&written).unwrap();
        assert_eq!(arr.len(), 2);
        assert_eq!(arr[0].get("seq").and_then(|v| v.as_u64()), Some(0));
        assert_eq!(arr[1].get("seq").and_then(|v| v.as_u64()), Some(1));
    }

    #[tokio::test]
    async fn partial_flush_appends_jsonl() {
        let tmp = TempDir::new().unwrap();
        let exporter = JsonFileExporter::new(tmp.path());
        let mut meta = ExportMetadata::default();
        meta.partial = true;
        exporter
            .export(vec![ev("r2", 0)], "r2".into(), meta.clone())
            .await;
        exporter.export(vec![ev("r2", 1)], "r2".into(), meta).await;
        let written = fs::read_to_string(tmp.path().join("r2.json")).unwrap();
        let lines: Vec<&str> = written.trim().lines().collect();
        assert_eq!(lines.len(), 2);
    }
}
