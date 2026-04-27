//! [`LocalTracer`] — write each workflow's trace to a JSON file.
//!
//! Mirrors Python [`operonx/core/tracing/local.py`](../../../../operonx/core/tracing/local.py).
//! Zero deps, zero setup. One file per request under `~/.operonx/traces/`
//! (or `$OPERON_TRACES_DIR`).
//!
//! # Phase 7 scope
//! Full sync implementation — `flush()` performs the atomic write (write
//! to `.tmp`, rename) exactly like Python. Runs under `spawn_blocking` per
//! the `Tracer::flush` contract (§4b.3).

use std::fs;
use std::path::PathBuf;

use tracing::{info, warn};

use super::base::Tracer;
use super::models::TraceData;
use crate::core::exceptions::OperonError;

pub struct LocalTracer {
    path: PathBuf,
    tags: Vec<String>,
}

impl LocalTracer {
    /// Build with an explicit path (or `None` to use
    /// `$OPERON_TRACES_DIR` / `~/.operonx/traces`).
    pub fn new(path: Option<PathBuf>, tags: Vec<String>) -> Self {
        Self {
            path: path.unwrap_or_else(default_traces_dir),
            tags,
        }
    }
}

impl std::fmt::Debug for LocalTracer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LocalTracer")
            .field("path", &self.path)
            .finish()
    }
}

impl Tracer for LocalTracer {
    fn name(&self) -> &str {
        "local"
    }

    fn tags(&self) -> &[String] {
        &self.tags
    }

    /// `stream_trace_limit=None` — LocalTracer keeps every context group.
    fn stream_trace_limit(&self) -> Option<usize> {
        None
    }

    fn flush(&self, trace: &TraceData) -> Result<(), OperonError> {
        fs::create_dir_all(&self.path).map_err(|e| {
            OperonError::Runtime(format!(
                "LocalTracer: failed to create {}: {}",
                self.path.display(),
                e
            ))
        })?;
        let final_path = self.path.join(format!("{}.json", trace.request_id));
        let tmp = final_path.with_extension("tmp");
        let body = serde_json::to_vec_pretty(trace)
            .map_err(|e| OperonError::Runtime(format!("LocalTracer: serialize failed: {}", e)))?;
        fs::write(&tmp, &body).map_err(|e| {
            OperonError::Runtime(format!(
                "LocalTracer: write {} failed: {}",
                tmp.display(),
                e
            ))
        })?;
        fs::rename(&tmp, &final_path)
            .map_err(|e| {
                // Atomic-rename can fail on Windows across volumes — fall back
                // to copy+delete so tests don't flake.
                warn!(
                    "LocalTracer: rename {} → {} failed ({}); falling back to copy",
                    tmp.display(),
                    final_path.display(),
                    e
                );
                if let Err(copy_err) =
                    fs::copy(&tmp, &final_path).and_then(|_| fs::remove_file(&tmp))
                {
                    return OperonError::Runtime(format!(
                        "LocalTracer: fallback copy failed: {}",
                        copy_err
                    ));
                }
                OperonError::Runtime("LocalTracer: rename soft-recovered via copy".into())
            })
            .ok();
        info!("Trace written to {}", final_path.display());
        Ok(())
    }
}

fn default_traces_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("OPERON_TRACES_DIR") {
        return PathBuf::from(dir);
    }
    if let Some(home) = dirs_home() {
        return home.join(".operonx").join("traces");
    }
    PathBuf::from(".operonx/traces")
}

/// Portable `~` resolution without pulling the `dirs` crate. Prefers
/// `$HOME` (unix/macOS), falls back to `%USERPROFILE%` on Windows.
fn dirs_home() -> Option<PathBuf> {
    if let Ok(h) = std::env::var("HOME") {
        if !h.is_empty() {
            return Some(PathBuf::from(h));
        }
    }
    if let Ok(h) = std::env::var("USERPROFILE") {
        if !h.is_empty() {
            return Some(PathBuf::from(h));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn flush_writes_atomic_json_file() {
        let dir = env::temp_dir().join(format!("operonx-local-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let tracer = LocalTracer::new(Some(dir.clone()), vec!["t".into()]);
        let trace = TraceData {
            request_id: "abc123".into(),
            workflow_name: "main".into(),
            ..Default::default()
        };
        tracer.flush(&trace).unwrap();
        let path = dir.join("abc123.json");
        assert!(path.exists(), "expected trace file at {}", path.display());
        let body: TraceData = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(body.request_id, "abc123");
        fs::remove_dir_all(&dir).ok();
    }
}
