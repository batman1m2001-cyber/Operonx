//! Pinecone reranker backend.
//!
//! Mirrors Python [`operonx/providers/rerankers/pinecone.py`](../../../../../operonx/providers/rerankers/pinecone.py).
//!
//! # Phase 5 scope
//! Struct + trait stub — Phase 5b adds the POST to `/rerank`.

use async_trait::async_trait;
use serde_json::Value;

use super::base::{BaseReranker, RerankOpts, RerankResult};
use super::config::RerankingConfig;
use crate::core::exceptions::OperonError;

pub struct PineconeReranker {
    pub config: RerankingConfig,
}

impl PineconeReranker {
    pub fn new(config: RerankingConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseReranker for PineconeReranker {
    async fn run(
        &self,
        _query: String,
        _texts: Vec<Value>,
        _top_k: usize,
        _opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError> {
        Err(OperonError::Provider(
            "PineconeReranker::run not yet implemented (Phase 5b)".into(),
        ))
    }
}
