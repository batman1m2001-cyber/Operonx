//! Langfuse tracer — sends workflow traces via Langfuse public REST API.
//!
//! No SDK dependency. Builds a batch of ingestion events from the pre-computed
//! TraceNode tree and POSTs to /api/public/ingestion.

pub mod client;
pub mod config;

use std::collections::HashMap;

use chrono::{DateTime, Duration, Utc};
use serde_json::Value;
use uuid::Uuid;

use rush_core::tracing::Tracer;

use self::client::LangfuseClient;
use self::config::LangfuseConfig;

/// Tracer that sends workflow traces to Langfuse via public REST API.
///
/// Example:
/// ```no_run
/// use rush_telemetry::langfuse::{LangfuseTracer, config::LangfuseConfig};
///
/// let config = LangfuseConfig::from_env().unwrap();
/// let tracer = LangfuseTracer::new(config, None);
/// ```
pub struct LangfuseTracer {
    config: LangfuseConfig,
    tags: Vec<String>,
    stream_trace_limit: Option<usize>,
}

impl LangfuseTracer {
    pub fn new(config: LangfuseConfig, stream_trace_limit: Option<usize>) -> Self {
        Self {
            config,
            tags: vec![],
            stream_trace_limit,
        }
    }

    pub fn with_tags(mut self, tags: Vec<String>) -> Self {
        self.tags = tags;
        self
    }
}

impl std::fmt::Debug for LangfuseTracer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "<LangfuseTracer host={}>", self.config.host)
    }
}

impl Tracer for LangfuseTracer {
    fn flush(&self, trace_data: Value) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let client = LangfuseClient::new(self.config.clone());

        let workflow_name = trace_data["workflow_name"].as_str().unwrap_or("unknown");
        let trace_id = trace_data["request_id"].as_str().unwrap_or("unknown");
        let user_id = trace_data.get("user_id").and_then(|v| v.as_str());
        let session_id = trace_data.get("session_id").and_then(|v| v.as_str());
        let tags: Vec<&str> = trace_data
            .get("tags")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
            .unwrap_or_default();

        let now_iso = Utc::now().format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string();

        let nodes = match trace_data.get("nodes").and_then(|v| v.as_array()) {
            Some(arr) => arr.clone(),
            None => return Ok(()),
        };

        // Pre-process: collect parent start times for monotonic child ordering
        let mut node_start: HashMap<&str, &str> = HashMap::new();
        for node in &nodes {
            if let (Some(key), Some(st)) = (
                node.get("trace_key").and_then(|v| v.as_str()),
                node.get("start_time").and_then(|v| v.as_str()),
            ) {
                node_start.insert(key, st);
            }
        }

        // Assign monotonically increasing start_times per parent (parent_start + idx*1ms)
        let mut parent_child_count: HashMap<&str, i64> = HashMap::new();
        let mut adjusted_starts: Vec<Option<String>> = Vec::with_capacity(nodes.len());

        for node in &nodes {
            let pk = node.get("parent_trace_key").and_then(|v| v.as_str());
            if let Some(pk) = pk {
                let idx = parent_child_count.entry(pk).or_insert(0);
                let base_iso = node_start
                    .get(pk)
                    .copied()
                    .or_else(|| node.get("start_time").and_then(|v| v.as_str()))
                    .unwrap_or(&now_iso);

                let bumped = parse_iso(base_iso)
                    .map(|dt| dt + Duration::milliseconds(*idx))
                    .map(|dt| dt.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string());

                *idx += 1;
                adjusted_starts.push(bumped);
            } else {
                adjusted_starts.push(None);
            }
        }

        // Build batch events
        let mut batch: Vec<Value> = Vec::new();
        let mut obs_ids: HashMap<String, String> = HashMap::new();

        for (i, node) in nodes.iter().enumerate() {
            let key = node["trace_key"].as_str().unwrap_or("");
            let parent_key = node.get("parent_trace_key").and_then(|v| v.as_str());
            let node_type = node.get("node_type").and_then(|v| v.as_str()).unwrap_or("span");
            let metadata = node.get("metadata").cloned().unwrap_or(Value::Null);
            let event_id = Uuid::new_v4().to_string();

            // Use adjusted start time if available, else original
            let start_time = adjusted_starts[i]
                .as_deref()
                .or_else(|| node.get("start_time").and_then(|v| v.as_str()))
                .unwrap_or(&now_iso);

            match node_type {
                "trace" => {
                    let mut body = serde_json::json!({
                        "id": trace_id,
                        "name": node["display_name"],
                        "input": null_if_empty(node.get("inputs")),
                        "output": null_if_empty(node.get("outputs")),
                        "metadata": null_if_null(&metadata),
                        "timestamp": start_time,
                    });

                    if !tags.is_empty() {
                        body["tags"] = serde_json::json!(tags);
                    }
                    if let Some(uid) = user_id {
                        body["userId"] = Value::String(uid.to_string());
                    }
                    if let Some(sid) = session_id {
                        body["sessionId"] = Value::String(sid.to_string());
                    }

                    batch.push(serde_json::json!({
                        "id": event_id,
                        "type": "trace-create",
                        "timestamp": start_time,
                        "body": body,
                    }));
                    obs_ids.insert(key.to_string(), trace_id.to_string());
                }
                "generation" => {
                    let obs_id = Uuid::new_v4().to_string();
                    obs_ids.insert(key.to_string(), obs_id.clone());

                    let mut body = serde_json::json!({
                        "id": obs_id,
                        "traceId": trace_id,
                        "name": node["display_name"],
                        "startTime": start_time,
                        "endTime": node.get("end_time"),
                        "input": null_if_empty(node.get("inputs")),
                        "output": null_if_empty(node.get("outputs")),
                        "metadata": null_if_null(&metadata),
                    });

                    // Parent observation linking
                    if let Some(pk) = parent_key {
                        if let Some(parent_obs) = obs_ids.get(pk) {
                            if parent_obs != trace_id {
                                body["parentObservationId"] = Value::String(parent_obs.clone());
                            }
                        }
                    }

                    // LLM-specific fields
                    if let Some(model) = node.get("model").and_then(|v| v.as_str()) {
                        body["model"] = Value::String(model.to_string());
                    }
                    if let Some(usage) = node.get("usage") {
                        let mut usage_details = serde_json::Map::new();
                        if let Some(v) = usage.get("prompt_tokens") {
                            usage_details.insert("input".into(), v.clone());
                        }
                        if let Some(v) = usage.get("completion_tokens") {
                            usage_details.insert("output".into(), v.clone());
                        }
                        if let Some(v) = usage.get("total_tokens") {
                            usage_details.insert("total".into(), v.clone());
                        }
                        if !usage_details.is_empty() {
                            body["usageDetails"] = Value::Object(usage_details);
                        }
                    }
                    if let Some(cost) = node.get("cost").and_then(|v| v.as_f64()) {
                        body["costDetails"] = serde_json::json!({"total": cost});
                    }

                    batch.push(serde_json::json!({
                        "id": event_id,
                        "type": "generation-create",
                        "timestamp": start_time,
                        "body": body,
                    }));
                }
                _ => {
                    // Span (batch, generator, loop_iter, stream_context, graph)
                    let obs_id = Uuid::new_v4().to_string();
                    obs_ids.insert(key.to_string(), obs_id.clone());

                    let mut body = serde_json::json!({
                        "id": obs_id,
                        "traceId": trace_id,
                        "name": node["display_name"],
                        "startTime": start_time,
                        "endTime": node.get("end_time"),
                        "input": null_if_empty(node.get("inputs")),
                        "output": null_if_empty(node.get("outputs")),
                        "metadata": null_if_null(&metadata),
                    });

                    if let Some(pk) = parent_key {
                        if let Some(parent_obs) = obs_ids.get(pk) {
                            if parent_obs != trace_id {
                                body["parentObservationId"] = Value::String(parent_obs.clone());
                            }
                        }
                    }

                    batch.push(serde_json::json!({
                        "id": event_id,
                        "type": "span-create",
                        "timestamp": start_time,
                        "body": body,
                    }));
                }
            }
        }

        // Send batch
        let result = client.ingest(batch)?;

        // Check for errors
        if let Some(errors) = result.get("errors").and_then(|v| v.as_array()) {
            if !errors.is_empty() {
                return Err(format!(
                    "Langfuse ingestion had {} error(s) for workflow '{}': {:?}",
                    errors.len(),
                    workflow_name,
                    &errors[..errors.len().min(5)]
                )
                .into());
            }
        }

        Ok(())
    }

    fn tags(&self) -> Vec<String> {
        self.tags.clone()
    }

    fn stream_trace_limit(&self) -> Option<usize> {
        self.stream_trace_limit
    }
}

/// Parse ISO 8601 timestamp (with "Z" suffix).
fn parse_iso(s: &str) -> Option<DateTime<Utc>> {
    // Try parsing with chrono's flexible parser
    DateTime::parse_from_rfc3339(s)
        .map(|dt| dt.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            // Handle "Z" suffix without fractional seconds
            let s = s.replace("Z", "+00:00");
            DateTime::parse_from_rfc3339(&s)
                .map(|dt| dt.with_timezone(&Utc))
                .ok()
        })
}

/// Return Value::Null for empty/null values.
fn null_if_empty(v: Option<&Value>) -> Value {
    match v {
        Some(val) if !val.is_null() => val.clone(),
        _ => Value::Null,
    }
}

fn null_if_null(v: &Value) -> Value {
    if v.is_null() {
        Value::Null
    } else {
        v.clone()
    }
}
