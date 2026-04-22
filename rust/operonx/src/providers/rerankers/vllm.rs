//! vLLM reranker (cross-encoder served via vLLM) — OpenAI-compatible shape.
//!
//! Mirrors Python [`operon/providers/rerankers/vllm.py`](../../../../../operon/providers/rerankers/vllm.py).
//!
//! # Phase 5 scope
//! Struct + trait stub. The HTTP POST round-trip lands in Phase 5b alongside
//! the Cohere / Pinecone backends.

use async_trait::async_trait;
use serde_json::Value;

use super::base::{BaseReranker, RerankOpts, RerankResult};
use super::config::RerankingConfig;
use crate::core::exceptions::OperonError;

pub struct VllmReranker {
    pub config: RerankingConfig,
}

impl VllmReranker {
    pub fn new(config: RerankingConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseReranker for VllmReranker {
    async fn run(
        &self,
        _query: String,
        _texts: Vec<Value>,
        _top_k: usize,
        _opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError> {
        Err(OperonError::Provider(
            "VllmReranker::run not yet implemented (Phase 5b)".into(),
        ))
    }
}
