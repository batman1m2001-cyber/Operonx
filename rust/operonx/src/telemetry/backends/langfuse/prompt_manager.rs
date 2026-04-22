//! `LangfusePromptManager` — versioned prompt retrieval + caching.
//!
//! Mirrors Python [`operon/telemetry/backends/langfuse/prompt_manager.py`](../../../../../../operon/telemetry/backends/langfuse/prompt_manager.py).
//!
//! # Phase 7 scope
//! Stub. Per §6b.8 the Python side lazy-imports the `langfuse` SDK here —
//! the Rust port hits `{host}/api/public/v2/prompts/{name}` directly when
//! implemented in Phase 7b.

use std::sync::Arc;

use dashmap::DashMap;
use serde_json::Value;

use super::config::LangfuseConfig;
use crate::core::exceptions::OperonError;

/// Cached, versioned prompts keyed by `"{name}:{version}"`.
pub struct LangfusePromptManager {
    pub config: LangfuseConfig,
    cache: DashMap<String, Arc<Value>>,
}

impl std::fmt::Debug for LangfusePromptManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LangfusePromptManager")
            .field("host", &self.config.host)
            .field("cached", &self.cache.len())
            .finish()
    }
}

impl LangfusePromptManager {
    pub fn new(config: LangfuseConfig) -> Self {
        Self {
            config,
            cache: DashMap::new(),
        }
    }

    /// Retrieve a named prompt (optionally at a specific version). Returns the
    /// cached value when present; otherwise fetches from the backend.
    pub async fn get(
        &self,
        name: &str,
        version: Option<&str>,
    ) -> Result<Arc<Value>, OperonError> {
        let key = match version {
            Some(v) => format!("{}:{}", name, v),
            None => format!("{}:latest", name),
        };
        if let Some(cached) = self.cache.get(&key) {
            return Ok(cached.clone());
        }
        Err(OperonError::Provider(format!(
            "LangfusePromptManager::get not yet implemented (Phase 7b) — asked for '{}'",
            key
        )))
    }

    /// Number of cached prompts.
    pub fn cache_len(&self) -> usize {
        self.cache.len()
    }
}
