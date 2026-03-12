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

        let batch = build_batch(&trace_data);
        if batch.is_empty() {
            return Ok(());
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

/// Build Langfuse ingestion batch events from trace_data.
///
/// Converts TraceNodes into trace-create, generation-create, and span-create events.
/// Handles monotonic child timestamp ordering, parent observation linking, and LLM fields.
///
/// Separated from `flush()` for testability — this is pure data transformation, no I/O.
pub(crate) fn build_batch(trace_data: &Value) -> Vec<Value> {
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
        None => return vec![],
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
                    "environment": "default",
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

    batch
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Build minimal trace_data for testing build_batch.
    fn make_trace_data(nodes: Vec<Value>) -> Value {
        json!({
            "request_id": "test-req-001",
            "workflow_name": "test-workflow",
            "tags": ["test", "unit"],
            "nodes": nodes,
            "summary": {
                "total_ops": nodes.len(),
                "total_records": nodes.len(),
                "total_duration_ms": 100.0,
                "stream_count": 0,
                "total_yields": 0,
                "loop_iterations": 0,
                "error_count": 0
            }
        })
    }

    fn root_node() -> Value {
        json!({
            "trace_key": "wf:main",
            "parent_trace_key": null,
            "op_name": "wf",
            "display_name": "test-workflow",
            "node_type": "trace",
            "kind": "batch",
            "inputs": {"text": "hello"},
            "outputs": {"result": 42},
            "start_time": "2026-03-12T10:00:00.000Z",
            "end_time": "2026-03-12T10:00:01.000Z",
            "duration_ms": 1000.0,
            "metadata": {},
            "model": null,
            "usage": null,
            "cost": null
        })
    }

    fn span_node(name: &str, parent_key: &str) -> Value {
        json!({
            "trace_key": format!("wf.{}:main", name),
            "parent_trace_key": parent_key,
            "op_name": format!("wf.{}", name),
            "display_name": name,
            "node_type": "span",
            "kind": "batch",
            "inputs": {"x": 5},
            "outputs": {"result": 10},
            "start_time": "2026-03-12T10:00:00.100Z",
            "end_time": "2026-03-12T10:00:00.200Z",
            "duration_ms": 100.0,
            "metadata": {},
            "model": null,
            "usage": null,
            "cost": null
        })
    }

    fn generation_node(name: &str, parent_key: &str) -> Value {
        json!({
            "trace_key": format!("wf.{}:main", name),
            "parent_trace_key": parent_key,
            "op_name": format!("wf.{}", name),
            "display_name": name,
            "node_type": "generation",
            "kind": "batch",
            "inputs": {"messages": [{"role": "user", "content": "hi"}]},
            "outputs": {"content": "Hello!", "model_used": "gpt-4o", "tokens_used": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
            "start_time": "2026-03-12T10:00:00.100Z",
            "end_time": "2026-03-12T10:00:00.500Z",
            "duration_ms": 400.0,
            "metadata": {},
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "cost": 0.00035
        })
    }

    // =========================================================================
    // trace-create event tests
    // =========================================================================

    #[test]
    fn test_trace_create_has_environment_default() {
        // THE BUG: Langfuse v3+ filters by environment. Without "environment": "default",
        // traces don't appear in the dashboard even though the API accepts them.
        let data = make_trace_data(vec![root_node()]);
        let batch = build_batch(&data);

        assert_eq!(batch.len(), 1);
        let event = &batch[0];
        assert_eq!(event["type"], "trace-create");

        let body = &event["body"];
        assert_eq!(
            body["environment"], "default",
            "trace-create MUST have environment: 'default' for Langfuse v3+ dashboard visibility"
        );
    }

    #[test]
    fn test_trace_create_has_required_fields() {
        let data = make_trace_data(vec![root_node()]);
        let batch = build_batch(&data);

        let body = &batch[0]["body"];
        assert_eq!(body["id"], "test-req-001", "trace id = request_id");
        assert_eq!(body["name"], "test-workflow");
        assert!(body["timestamp"].is_string());
        assert_eq!(body["environment"], "default");
        assert!(!body["input"].is_null());
        assert!(!body["output"].is_null());
    }

    #[test]
    fn test_trace_create_includes_tags() {
        let data = make_trace_data(vec![root_node()]);
        let batch = build_batch(&data);

        let body = &batch[0]["body"];
        let tags = body["tags"].as_array().unwrap();
        assert!(tags.contains(&json!("test")));
        assert!(tags.contains(&json!("unit")));
    }

    #[test]
    fn test_trace_create_includes_user_and_session() {
        let mut data = make_trace_data(vec![root_node()]);
        data["user_id"] = json!("user-42");
        data["session_id"] = json!("sess-abc");

        let batch = build_batch(&data);
        let body = &batch[0]["body"];
        assert_eq!(body["userId"], "user-42");
        assert_eq!(body["sessionId"], "sess-abc");
    }

    // =========================================================================
    // span-create event tests
    // =========================================================================

    #[test]
    fn test_span_create_no_environment_field() {
        // environment should ONLY be on trace-create, NOT on spans
        let data = make_trace_data(vec![root_node(), span_node("step", "wf:main")]);
        let batch = build_batch(&data);

        assert_eq!(batch.len(), 2);
        let span_event = &batch[1];
        assert_eq!(span_event["type"], "span-create");

        let body = &span_event["body"];
        assert!(
            body.get("environment").is_none() || body["environment"].is_null(),
            "span-create must NOT have environment field"
        );
    }

    #[test]
    fn test_span_has_trace_id_and_parent() {
        let data = make_trace_data(vec![root_node(), span_node("step", "wf:main")]);
        let batch = build_batch(&data);

        let span_body = &batch[1]["body"];
        assert_eq!(span_body["traceId"], "test-req-001");
        assert_eq!(span_body["name"], "step");
        assert!(span_body["startTime"].is_string());
        // Parent is the trace itself, so no parentObservationId (direct child of trace)
        assert!(
            span_body.get("parentObservationId").is_none()
                || span_body["parentObservationId"].is_null(),
            "Direct child of trace should not have parentObservationId"
        );
    }

    #[test]
    fn test_nested_span_has_parent_observation_id() {
        // Root → span1 → span2 (nested)
        let mut span2 = span_node("step2", "wf.step1:main");
        span2["trace_key"] = json!("wf.step2:main");

        let data = make_trace_data(vec![
            root_node(),
            span_node("step1", "wf:main"),
            span2,
        ]);
        let batch = build_batch(&data);

        assert_eq!(batch.len(), 3);
        let span2_body = &batch[2]["body"];
        // step2's parent is step1, which has an obs_id (not the trace_id)
        assert!(
            span2_body["parentObservationId"].is_string(),
            "Nested span should have parentObservationId"
        );
        // parentObservationId should be step1's obs_id, not the trace_id
        let parent_obs = span2_body["parentObservationId"].as_str().unwrap();
        assert_ne!(parent_obs, "test-req-001", "parent should not be trace_id for nested span");
    }

    // =========================================================================
    // generation-create event tests
    // =========================================================================

    #[test]
    fn test_generation_create_no_environment_field() {
        let data = make_trace_data(vec![root_node(), generation_node("llm", "wf:main")]);
        let batch = build_batch(&data);

        let gen_event = &batch[1];
        assert_eq!(gen_event["type"], "generation-create");

        let body = &gen_event["body"];
        assert!(
            body.get("environment").is_none() || body["environment"].is_null(),
            "generation-create must NOT have environment field"
        );
    }

    #[test]
    fn test_generation_has_llm_fields() {
        let data = make_trace_data(vec![root_node(), generation_node("llm", "wf:main")]);
        let batch = build_batch(&data);

        let body = &batch[1]["body"];
        assert_eq!(body["model"], "gpt-4o");
        assert_eq!(body["usageDetails"]["input"], 10);
        assert_eq!(body["usageDetails"]["output"], 5);
        assert_eq!(body["usageDetails"]["total"], 15);
        assert_eq!(body["costDetails"]["total"], 0.00035);
    }

    #[test]
    fn test_generation_has_trace_id() {
        let data = make_trace_data(vec![root_node(), generation_node("llm", "wf:main")]);
        let batch = build_batch(&data);

        let body = &batch[1]["body"];
        assert_eq!(body["traceId"], "test-req-001");
    }

    // =========================================================================
    // Batch structure tests
    // =========================================================================

    #[test]
    fn test_empty_nodes_returns_empty_batch() {
        let data = make_trace_data(vec![]);
        let batch = build_batch(&data);
        assert!(batch.is_empty());
    }

    #[test]
    fn test_no_nodes_key_returns_empty_batch() {
        let data = json!({"request_id": "x", "workflow_name": "w"});
        let batch = build_batch(&data);
        assert!(batch.is_empty());
    }

    #[test]
    fn test_batch_event_order_matches_node_order() {
        let data = make_trace_data(vec![
            root_node(),
            span_node("step1", "wf:main"),
            span_node("step2", "wf:main"),
        ]);
        let batch = build_batch(&data);

        assert_eq!(batch.len(), 3);
        assert_eq!(batch[0]["type"], "trace-create");
        assert_eq!(batch[1]["type"], "span-create");
        assert_eq!(batch[2]["type"], "span-create");
        assert_eq!(batch[1]["body"]["name"], "step1");
        assert_eq!(batch[2]["body"]["name"], "step2");
    }

    #[test]
    fn test_all_events_have_id_and_timestamp() {
        let data = make_trace_data(vec![
            root_node(),
            span_node("step", "wf:main"),
            generation_node("llm", "wf:main"),
        ]);
        let batch = build_batch(&data);

        for event in &batch {
            assert!(event["id"].is_string(), "Event must have id");
            assert!(event["timestamp"].is_string(), "Event must have timestamp");
            assert!(event["type"].is_string(), "Event must have type");
            assert!(event["body"].is_object(), "Event must have body");
        }
    }

    #[test]
    fn test_monotonic_child_timestamps() {
        // Children of the same parent should get incrementally bumped start times
        let mut step1 = span_node("step1", "wf:main");
        let mut step2 = span_node("step2", "wf:main");
        // Give them same start_time
        step1["start_time"] = json!("2026-03-12T10:00:00.100Z");
        step2["start_time"] = json!("2026-03-12T10:00:00.100Z");
        step2["trace_key"] = json!("wf.step2:main");

        let data = make_trace_data(vec![root_node(), step1, step2]);
        let batch = build_batch(&data);

        // Both children should have adjusted start times based on parent's start
        let st1 = batch[1]["body"]["startTime"].as_str().unwrap();
        let st2 = batch[2]["body"]["startTime"].as_str().unwrap();

        // st2 should be 1ms after st1 (monotonic ordering)
        assert_ne!(st1, st2, "Children should have different adjusted timestamps");
        assert!(st2 > st1, "Second child should have later timestamp");
    }

    // =========================================================================
    // parse_iso tests
    // =========================================================================

    #[test]
    fn test_parse_iso_rfc3339() {
        let dt = parse_iso("2026-03-12T10:00:00.000+00:00");
        assert!(dt.is_some());
    }

    #[test]
    fn test_parse_iso_z_suffix() {
        let dt = parse_iso("2026-03-12T10:00:00.000Z");
        assert!(dt.is_some());
    }

    #[test]
    fn test_parse_iso_no_fractional() {
        let dt = parse_iso("2026-03-12T10:00:00Z");
        assert!(dt.is_some());
    }

    #[test]
    fn test_parse_iso_invalid() {
        let dt = parse_iso("not-a-date");
        assert!(dt.is_none());
    }
}
