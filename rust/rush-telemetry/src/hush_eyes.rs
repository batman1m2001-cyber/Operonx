//! HushEyesTracer — sends traces to ui-hush-eyes local server.
//!
//! Mirrors Python's `hush.telemetry.tracers.hush_eyes.HushEyesTracer`.
//! Uses reqwest::blocking::Client for HTTP POST (runs in spawn_blocking).

use serde_json::Value;

use rush_core::tracing::Tracer;

/// Tracer that sends traces to the ui-hush-eyes local server.
///
/// ui-hush-eyes is a lightweight Rust server that stores traces in SQLite
/// and serves a web UI for visualization.
///
/// Example:
/// ```no_run
/// use rush_telemetry::HushEyesTracer;
///
/// let tracer = HushEyesTracer::new(None, None, vec![]);
/// // Add to Rush engine via engine.add_tracer(tracer)
/// ```
pub struct HushEyesTracer {
    url: String,
    tags: Vec<String>,
    client: reqwest::blocking::Client,
}

impl HushEyesTracer {
    /// Create a new HushEyesTracer.
    ///
    /// - `host`: Server host (default: `"127.0.0.1"`)
    /// - `port`: Server port (default: `8420`)
    /// - `tags`: Static tags for this tracer
    pub fn new(host: Option<&str>, port: Option<u16>, tags: Vec<String>) -> Self {
        let host = host.unwrap_or("127.0.0.1");
        let port = port.unwrap_or(8420);
        HushEyesTracer {
            url: format!("http://{}:{}/api/ingest", host, port),
            tags,
            client: reqwest::blocking::Client::builder()
                .timeout(std::time::Duration::from_secs(5))
                .build()
                .expect("Failed to build HTTP client"),
        }
    }
}

impl std::fmt::Debug for HushEyesTracer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "<HushEyesTracer url={}>", self.url)
    }
}

impl Tracer for HushEyesTracer {
    fn flush(&self, trace_data: Value) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        match self.client.post(&self.url).json(&trace_data).send() {
            Ok(resp) => {
                if !resp.status().is_success() {
                    eprintln!(
                        "[rush-tracing] ui-hush-eyes returned status {}: {}",
                        resp.status(),
                        resp.text().unwrap_or_default()
                    );
                }
            }
            Err(_) => {
                // Server may not be running — silent (matches Python behavior)
            }
        }
        Ok(())
    }

    fn tags(&self) -> Vec<String> {
        self.tags.clone()
    }

    fn stream_trace_limit(&self) -> Option<usize> {
        None // Keep all traces for local visualization
    }
}
