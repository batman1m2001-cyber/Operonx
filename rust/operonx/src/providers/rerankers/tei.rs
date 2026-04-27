//! HuggingFace Text Embeddings Inference (TEI) reranker backend.
//!
//! Mirrors Python [`operonx/providers/rerankers/tei.py`](../../../../../operonx/providers/rerankers/tei.py).
//! Net-new Rust port (plan §5a marks this file as 🆕).

use async_trait::async_trait;
use serde_json::Value;

use super::base::{BaseReranker, RerankOpts, RerankResult};
use super::config::RerankingConfig;
use crate::core::exceptions::OperonError;

pub struct TeiReranker {
    pub config: RerankingConfig,
}

impl TeiReranker {
    pub fn new(config: RerankingConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseReranker for TeiReranker {
    async fn run(
        &self,
        _query: String,
        _texts: Vec<Value>,
        _top_k: usize,
        _opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError> {
        Err(OperonError::Provider(
            "TeiReranker::run not yet implemented (Phase 5b)".into(),
        ))
    }
}
