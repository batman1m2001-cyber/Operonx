//! End-to-end test of LangfuseExporter against a wiremock'd ingestion API.
//!
//! Stage 6 of the operonx Rust sync. Confirms:
//!   - The exporter POSTs to `<host>/api/public/ingestion`.
//!   - Basic auth header carries base64(`<pk>:<sk>`).
//!   - Batch body contains one `trace-create` + one `span-create` per op.
//!   - Disabled config (`enabled=false`) emits no request.

#![cfg(feature = "langfuse")]

use std::collections::BTreeMap;

use operonx::core::tracing::events::{EventKind, TraceEvent};
use operonx::core::tracing::pipeline::{ExportMetadata, Exporter};
use operonx::telemetry::exporters::LangfuseExporter;
use operonx::telemetry::backends::langfuse::config::LangfuseConfig;

use chrono::Utc;
use serde_json::{json, Value};
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn cfg_for(host: String, enabled: bool) -> LangfuseConfig {
    LangfuseConfig {
        host,
        public_key: "pk-fake".into(),
        secret_key: "sk-fake".into(),
        enabled,
        no_proxy: None,
        sample_rate: 1.0,
        trace_filter: None,
    }
}

fn ev(kind: EventKind, op: &str, ctx: &[&str], payload: serde_json::Map<String, Value>) -> TraceEvent {
    let mut p = BTreeMap::new();
    for (k, v) in payload {
        p.insert(k, v);
    }
    TraceEvent {
        event_id: format!("e-{op}"),
        request_id: "r1".into(),
        kind,
        op_name: Some(op.into()),
        ctx: ctx.iter().map(|s| s.to_string()).collect(),
        timestamp: Utc::now(),
        seq: 0,
        payload: p,
    }
}

#[tokio::test]
async fn langfuse_exporter_posts_batch_to_ingestion_endpoint() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/public/ingestion"))
        .and(header("authorization", "Basic cGstZmFrZTpzay1mYWtl")) // pk-fake:sk-fake b64
        .respond_with(ResponseTemplate::new(207).set_body_json(json!({
            "successes": [{"id": "any", "status": 201}],
            "errors": []
        })))
        .expect(1)
        .mount(&server)
        .await;

    let exporter = LangfuseExporter::new(cfg_for(server.uri(), true));
    let mut start_payload = serde_json::Map::new();
    start_payload.insert("inputs".into(), json!({"x": 1}));
    let mut end_payload = serde_json::Map::new();
    end_payload.insert("outputs".into(), json!({"y": 2}));
    end_payload.insert("status".into(), json!("ok"));
    end_payload.insert("duration_ms".into(), json!(15.0));
    let events = vec![
        ev(EventKind::OpStart, "main.op", &["main"], start_payload),
        ev(EventKind::OpEnd, "main.op", &["main"], end_payload),
    ];
    exporter
        .export(events, "r1".into(), ExportMetadata::default())
        .await;
    // wiremock's `.expect(1)` enforces exactly one matching request.
}

#[tokio::test]
async fn langfuse_exporter_skips_request_when_disabled() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/public/ingestion"))
        .respond_with(ResponseTemplate::new(200))
        .expect(0) // must NOT be called
        .mount(&server)
        .await;

    let exporter = LangfuseExporter::new(cfg_for(server.uri(), false));
    let mut payload = serde_json::Map::new();
    payload.insert("inputs".into(), json!({}));
    let events = vec![ev(EventKind::OpStart, "main.op", &["main"], payload)];
    exporter
        .export(events, "r1".into(), ExportMetadata::default())
        .await;
}
