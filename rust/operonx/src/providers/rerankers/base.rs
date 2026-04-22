//! [`BaseReranker`] trait.
//!
//! Mirrors Python [`operon/providers/rerankers/base.py`](../../../../../operon/providers/rerankers/base.py).
//! Per plan §5b.1 the canonical method is `run()` (not `rerank()`).

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::exceptions::OperonError;

/// One reranked item — score + original text (or structured doc).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RerankResult {
    /// Index of the document in the input array (matches Python).
    pub index: usize,
    /// Relevance score, higher = more relevant.
    pub score: f32,
    /// Original document — either the raw string or the structured
    /// object passed in.
    pub document: Value,
}

/// Reranker-call options (`threshold`, provider extras).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RerankOpts {
    /// Discard results with `score < threshold`.
    #[serde(default)]
    pub threshold: f32,
    #[serde(default)]
    pub extras: std::collections::HashMap<String, Value>,
}

/// The trait every reranking backend implements.
#[async_trait]
pub trait BaseReranker: Send + Sync {
    /// Rank `texts` by relevance to `query`; return the top `top_k` results.
    async fn run(
        &self,
        query: String,
        texts: Vec<Value>,
        top_k: usize,
        opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError>;
}
