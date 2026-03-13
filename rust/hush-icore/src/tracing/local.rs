//! LocalTracer — writes trace data to local JSON files.
//!
//! Mirrors Python's `hush.core.tracing.local.LocalTracer`.
//! Zero dependencies, zero setup. One JSON file per request.
//! Default path: ~/.hush/traces/ (configurable via path or HUSH_TRACES_DIR env var).

use std::fmt;
use std::fs;
use std::path::PathBuf;

use serde_json::Value;

use crate::tracing::tracer::Tracer;

/// Get home directory using standard env vars (no external crate).
fn home_dir() -> PathBuf {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

/// Tracer that writes trace data to local JSON files.
///
/// Each engine.run() produces one file: `{path}/{request_id}.json`
/// containing the trace data as pretty-printed JSON.
pub struct LocalTracer {
    path: PathBuf,
    tags: Vec<String>,
}

impl LocalTracer {
    /// Create a new LocalTracer.
    ///
    /// `path`: Directory for trace files. Defaults to `~/.hush/traces/`
    /// or the `HUSH_TRACES_DIR` environment variable.
    pub fn new(path: Option<&str>, tags: Vec<String>) -> Self {
        let dir = path
            .map(PathBuf::from)
            .or_else(|| std::env::var("HUSH_TRACES_DIR").ok().map(PathBuf::from))
            .unwrap_or_else(|| {
                home_dir().join(".hush").join("traces")
            });
        LocalTracer { path: dir, tags }
    }
}

impl fmt::Debug for LocalTracer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "<LocalTracer path={}>", self.path.display())
    }
}

impl Tracer for LocalTracer {
    fn flush(&self, trace_data: Value) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        fs::create_dir_all(&self.path)?;
        let request_id = trace_data
            .get("request_id")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");
        let filepath = self.path.join(format!("{request_id}.json"));
        let json_str = serde_json::to_string_pretty(&trace_data)?;
        fs::write(&filepath, json_str)?;
        eprintln!("[hush-tracing] Trace written to {}", filepath.display());
        Ok(())
    }

    fn tags(&self) -> Vec<String> {
        self.tags.clone()
    }

    fn stream_trace_limit(&self) -> Option<usize> {
        None // Keep all traces for local debugging
    }
}
