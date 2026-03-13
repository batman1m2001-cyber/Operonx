//! OpenTelemetry tracer — vendor-neutral trace export.
//!
//! Exports traces to any OTLP-compatible backend (Jaeger, Zipkin,
//! Datadog, New Relic, Grafana Tempo, etc.).
//!
//! Feature-gated behind `otel`.

pub mod client;
pub mod config;

use std::collections::HashMap;

use opentelemetry::trace::{SpanKind, TraceContextExt, Tracer as OtelTracerApi};
use opentelemetry::Context;
use serde_json::Value;

use hush_icore::tracing::Tracer;

use self::client::OtelClient;
use self::config::OtelConfig;

/// Tracer that sends workflow traces via OpenTelemetry.
pub struct OtelTracer {
    config: OtelConfig,
    tags: Vec<String>,
    stream_trace_limit: Option<usize>,
}

impl OtelTracer {
    pub fn new(config: OtelConfig, stream_trace_limit: Option<usize>) -> Self {
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

impl std::fmt::Debug for OtelTracer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "<OtelTracer endpoint={}>", self.config.endpoint)
    }
}

impl Tracer for OtelTracer {
    fn flush(&self, trace_data: Value) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let client = OtelClient::new(&self.config)?;
        let otel_tracer = client.tracer();

        let workflow_name = trace_data["workflow_name"].as_str().unwrap_or("unknown");
        let req_id = trace_data["request_id"].as_str().unwrap_or("unknown");
        let user_id = trace_data.get("user_id").and_then(|v| v.as_str());
        let session_id = trace_data.get("session_id").and_then(|v| v.as_str());
        let tags: Vec<&str> = trace_data
            .get("tags")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
            .unwrap_or_default();

        let nodes = match trace_data.get("nodes").and_then(|v| v.as_array()) {
            Some(arr) => arr,
            None => return Ok(()),
        };

        let mut spans: HashMap<String, Context> = HashMap::new();
        let mut span_contexts: Vec<(String, Context)> = Vec::new();

        for node in nodes {
            let key = node["trace_key"].as_str().unwrap_or("");
            let parent_key = node.get("parent_trace_key").and_then(|v| v.as_str());
            let node_type = node.get("node_type").and_then(|v| v.as_str()).unwrap_or("span");
            let kind = node.get("kind").and_then(|v| v.as_str()).unwrap_or("batch");
            let display_name = node["display_name"].as_str().unwrap_or(key);

            // Build attributes
            let mut attrs = vec![
                opentelemetry::KeyValue::new("workflow.name", workflow_name.to_string()),
                opentelemetry::KeyValue::new("workflow.request_id", req_id.to_string()),
            ];

            if let Some(op_name) = node.get("op_name").and_then(|v| v.as_str()) {
                attrs.push(opentelemetry::KeyValue::new("op.name", op_name.to_string()));
            }
            if let Some(uid) = user_id {
                attrs.push(opentelemetry::KeyValue::new("user.id", uid.to_string()));
            }
            if let Some(sid) = session_id {
                attrs.push(opentelemetry::KeyValue::new("session.id", sid.to_string()));
            }
            if !tags.is_empty() {
                attrs.push(opentelemetry::KeyValue::new(
                    "workflow.tags",
                    tags.join(","),
                ));
            }
            if kind != "batch" {
                attrs.push(opentelemetry::KeyValue::new("op.kind", kind.to_string()));
            }

            // LLM-specific attributes
            if node_type == "generation" {
                attrs.push(opentelemetry::KeyValue::new("llm.request.type", "generation"));
                if let Some(model) = node.get("model").and_then(|v| v.as_str()) {
                    attrs.push(opentelemetry::KeyValue::new("llm.model", model.to_string()));
                }
                if let Some(usage) = node.get("usage") {
                    if let Some(v) = usage.get("prompt_tokens").and_then(|v| v.as_i64()) {
                        attrs.push(opentelemetry::KeyValue::new("llm.usage.prompt_tokens", v));
                    }
                    if let Some(v) = usage.get("completion_tokens").and_then(|v| v.as_i64()) {
                        attrs.push(opentelemetry::KeyValue::new("llm.usage.completion_tokens", v));
                    }
                    if let Some(v) = usage.get("total_tokens").and_then(|v| v.as_i64()) {
                        attrs.push(opentelemetry::KeyValue::new("llm.usage.total_tokens", v));
                    }
                }
            }

            // Serialize I/O as attributes (capped at 10KB)
            if let Some(inputs) = node.get("inputs") {
                if !inputs.is_null() {
                    if let Ok(s) = serde_json::to_string(inputs) {
                        if s.len() < 10000 {
                            attrs.push(opentelemetry::KeyValue::new("input", s));
                        }
                    }
                }
            }
            if let Some(outputs) = node.get("outputs") {
                if !outputs.is_null() {
                    if let Ok(s) = serde_json::to_string(outputs) {
                        if s.len() < 10000 {
                            attrs.push(opentelemetry::KeyValue::new("output", s));
                        }
                    }
                }
            }

            // Start span with parent context
            let span_builder = otel_tracer
                .span_builder(display_name.to_string())
                .with_kind(SpanKind::Internal)
                .with_attributes(attrs);

            let cx = if let Some(pk) = parent_key {
                if let Some(parent_cx) = spans.get(pk) {
                    parent_cx.clone()
                } else {
                    Context::current()
                }
            } else {
                Context::current()
            };

            let span = otel_tracer.build_with_context(span_builder, &cx);
            let new_cx = cx.with_span(span);

            spans.insert(key.to_string(), new_cx.clone());
            span_contexts.push((key.to_string(), new_cx));
        }

        // End all spans in reverse order (children first)
        // We drop contexts which ends spans automatically
        for (_, cx) in span_contexts.into_iter().rev() {
            drop(cx);
        }

        client.flush()?;
        Ok(())
    }

    fn tags(&self) -> Vec<String> {
        self.tags.clone()
    }

    fn stream_trace_limit(&self) -> Option<usize> {
        self.stream_trace_limit
    }
}
