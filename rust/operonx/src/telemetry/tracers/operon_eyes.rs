//! [`OperonEyesTracer`] — sends traces to the local `ui-operonx-eyes`
//! SQLite-backed server.
//!
//! Mirrors Python [`operonx/telemetry/tracers/operon_eyes.py`](../../../../../operonx/telemetry/tracers/operon_eyes.py).
//! Per plan §6a endpoint default: `127.0.0.1:8420/api/ingest`. Per §6b.13
//! `stream_trace_limit` is hardcoded to `None` (the UI handles large traces
//! locally).

use reqwest::blocking::Client;
use std::sync::OnceLock;
use std::time::Duration;
use tracing::{debug, warn};

use crate::core::exceptions::OperonError;
use crate::core::tracing::base::Tracer;
use crate::core::tracing::models::TraceData;
use crate::core::tracing::trace_filter::TraceFilter;

/// Default host:port for the local `ui-operonx-eyes` server.
pub const DEFAULT_HOST: &str = "127.0.0.1";
pub const DEFAULT_PORT: u16 = 8420;

pub struct OperonEyesTracer {
    host: String,
    port: u16,
    tags: Vec<String>,
    trace_filter: Option<TraceFilter>,
    http: OnceLock<Client>,
}

impl std::fmt::Debug for OperonEyesTracer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OperonEyesTracer")
            .field("url", &format!("http://{}:{}/api/ingest", self.host, self.port))
            .finish()
    }
}

impl OperonEyesTracer {
    pub fn new(host: Option<String>, port: Option<u16>, tags: Vec<String>) -> Self {
        Self {
            host: host.unwrap_or_else(|| DEFAULT_HOST.to_string()),
            port: port.unwrap_or(DEFAULT_PORT),
            tags,
            trace_filter: None,
            http: OnceLock::new(),
        }
    }

    pub fn with_trace_filter(mut self, filter: TraceFilter) -> Self {
        self.trace_filter = Some(filter);
        self
    }

    fn url(&self) -> String {
        format!("http://{}:{}/api/ingest", self.host, self.port)
    }

    fn client(&self) -> &Client {
        self.http.get_or_init(|| {
            Client::builder()
                .timeout(Duration::from_secs(5))
                .build()
                .expect("OperonEyesTracer: reqwest::blocking::Client build")
        })
    }
}

impl Tracer for OperonEyesTracer {
    fn name(&self) -> &str {
        "operon_eyes"
    }

    fn tags(&self) -> &[String] {
        &self.tags
    }

    /// Per §6b.13 — `None` limit, UI handles full trees.
    fn stream_trace_limit(&self) -> Option<usize> {
        None
    }

    fn trace_filter(&self) -> Option<&TraceFilter> {
        self.trace_filter.as_ref()
    }

    fn to_config_dict(&self) -> Option<serde_json::Value> {
        Some(serde_json::json!({
            "host": self.host,
            "port": self.port,
        }))
    }

    fn flush(&self, trace: &TraceData) -> Result<(), OperonError> {
        let url = self.url();
        let body = serde_json::to_vec(trace).map_err(|e| {
            OperonError::Runtime(format!(
                "OperonEyesTracer: serialize trace failed: {}",
                e
            ))
        })?;
        match self
            .client()
            .post(&url)
            .header("Content-Type", "application/json")
            .body(body)
            .send()
        {
            Ok(resp) => {
                if !resp.status().is_success() {
                    warn!(
                        "OperonEyesTracer: ui-operonx-eyes returned status {} at {}",
                        resp.status(),
                        url
                    );
                }
                Ok(())
            }
            Err(e) => {
                // Python logs this at DEBUG — server often isn't running.
                debug!(
                    "OperonEyesTracer: could not reach {} (server may not be running): {}",
                    url, e
                );
                Ok(())
            }
        }
    }
}
