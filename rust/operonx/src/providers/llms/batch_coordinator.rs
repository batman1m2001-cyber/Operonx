//! OpenAI Batch API coordinator.
//!
//! Mirrors Python [`operonx/providers/llms/batch_coordinator.py`](../../../../../operonx/providers/llms/batch_coordinator.py).
//! Per plan §5b.9 the coordinator is **per-LLMOp**, not a global service —
//! each [`LLMOp`](crate::providers::ops::llm) with `batch_mode=true` owns its
//! own `Arc<BatchCoordinator>`.
//!
//! # Phase 5 scope
//! Struct + typed config shape only. Queue flushing, batch upload,
//! status polling, and result dispatch will land alongside the LLMOp
//! implementation in Phase 6.

use std::sync::Arc;
use std::time::Duration;

use super::base::{BaseLLM, BatchReq, ChatCompletion, LlmOpts};
use crate::core::exceptions::OperonError;

/// Controls for batched request submission.
#[derive(Debug, Clone)]
pub struct BatchSettings {
    pub batch_size: usize,
    pub flush_interval: Duration,
    pub poll_interval: Duration,
    pub timeout: Duration,
}

impl Default for BatchSettings {
    fn default() -> Self {
        Self {
            batch_size: 100,
            flush_interval: Duration::from_secs(5),
            poll_interval: Duration::from_secs(30),
            timeout: Duration::from_secs(3600),
        }
    }
}

/// One batched-mode coordinator. Phase 6 expands this into a real queue +
/// background flusher. For now the type merely exists so the LLMOp struct
/// can hold an `Arc<BatchCoordinator>` without compile errors.
pub struct BatchCoordinator {
    pub resource: String,
    pub llm: Arc<dyn BaseLLM>,
    pub settings: BatchSettings,
}

impl BatchCoordinator {
    pub fn new(
        resource: impl Into<String>,
        llm: Arc<dyn BaseLLM>,
        settings: BatchSettings,
    ) -> Self {
        Self {
            resource: resource.into(),
            llm,
            settings,
        }
    }

    /// Submit a batch. Phase 5 stub — returns an error.
    pub async fn submit(
        &self,
        _reqs: Vec<BatchReq>,
        _opts: &LlmOpts,
    ) -> Result<Vec<ChatCompletion>, OperonError> {
        Err(OperonError::Provider(format!(
            "BatchCoordinator::submit not yet implemented (Phase 6) — resource={}",
            self.resource
        )))
    }
}

impl std::fmt::Debug for BatchCoordinator {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BatchCoordinator")
            .field("resource", &self.resource)
            .field("settings", &self.settings)
            .finish()
    }
}
