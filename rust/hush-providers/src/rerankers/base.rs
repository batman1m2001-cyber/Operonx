//! RerankerProvider trait — mirrors Python's `rerankers/base.py` (BaseReranker).
//!
//! All reranking backends (vLLM, Pinecone, Cohere, ONNX) implement this trait.

use serde_json::Value;

use crate::http::ProviderResult;

/// Base trait for reranking providers.
///
/// Mirrors Python's BaseReranker with `rerank()` method.
pub trait RerankerProvider: Send + Sync {
    /// Rerank documents by relevance to a query.
    fn rerank(
        &self,
        query: &str,
        documents: &[Value],
        top_k: usize,
    ) -> impl std::future::Future<Output = ProviderResult<Vec<Value>>> + Send;
}
