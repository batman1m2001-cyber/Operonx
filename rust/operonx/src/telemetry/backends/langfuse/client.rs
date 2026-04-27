//! `LangfuseClient` — stdlib-only HTTP client for Langfuse ingestion.
//!
//! Mirrors Python [`operonx/telemetry/backends/langfuse/client.py`](../../../../../../operonx/telemetry/backends/langfuse/client.py).
//! Per §6b.8 no SDK dependency — just `reqwest::blocking` + Basic auth.
//!
//! # Phase 7 scope
//! Shape only — `ingest()` accepts a `TraceData` payload and returns
//! success. Phase 7b adds the real HTTP body (batch POST to
//! `/api/public/ingestion`) + media upload path (§6b.12).

use serde::Serialize;
use tracing::debug;

use super::config::LangfuseConfig;
use crate::core::exceptions::OperonError;
use crate::core::tracing::models::TraceData;

pub struct LangfuseClient {
    pub config: LangfuseConfig,
}

impl std::fmt::Debug for LangfuseClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LangfuseClient")
            .field("host", &self.config.host)
            .field("enabled", &self.config.enabled)
            .finish()
    }
}

impl LangfuseClient {
    pub fn new(config: LangfuseConfig) -> Self {
        Self { config }
    }

    /// Push one batch of traces. Phase 7 stub — returns `Ok(())` without
    /// hitting the network so tests and smoke runs don't depend on external
    /// services.
    pub fn ingest(&self, trace: &TraceData) -> Result<(), OperonError> {
        if !self.config.enabled {
            debug!(
                "LangfuseClient: tracing disabled; dropping {}",
                trace.request_id
            );
            return Ok(());
        }
        debug!(
            "LangfuseClient: staged ingest for '{}' ({} nodes; Phase 7 stub)",
            trace.workflow_name,
            trace.nodes.len()
        );
        Ok(())
    }

    /// Base64-encoded Basic auth header payload for `Authorization`.
    #[allow(dead_code)]
    pub(crate) fn auth_header(&self) -> String {
        use base64::{engine::general_purpose::STANDARD, Engine as _};
        STANDARD.encode(format!(
            "{}:{}",
            self.config.public_key, self.config.secret_key
        ))
    }
}

/// Wire shape Phase 7b targets for batch ingestion.
#[derive(Debug, Clone, Serialize)]
#[allow(dead_code)]
struct IngestionBatch<'a> {
    batch: &'a [serde_json::Value],
    metadata: &'a serde_json::Value,
}
