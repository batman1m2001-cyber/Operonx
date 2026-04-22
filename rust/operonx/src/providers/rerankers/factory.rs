//! `create_reranker` — dispatch a [`RerankingConfig`] to the matching backend.
//!
//! Mirrors Python [`operon/providers/rerankers/factory.py`](../../../../../operon/providers/rerankers/factory.py).

use std::sync::Arc;

use super::base::BaseReranker;
use super::config::{RerankingConfig, RerankingType};
use super::huggingface::HuggingFaceReranker;
use super::onnx::OnnxReranker;
use super::pinecone::PineconeReranker;
use super::tei::TeiReranker;
use super::vllm::VllmReranker;
use crate::core::exceptions::OperonError;

/// Construct the reranker matching `config.api_type`.
pub fn create_reranker(config: RerankingConfig) -> Result<Arc<dyn BaseReranker>, OperonError> {
    let reranker: Arc<dyn BaseReranker> = match config.api_type {
        // Cohere's wire shape is close enough to vLLM's that — per plan §5 —
        // the Rust-side cohere path is kept internal to `VllmReranker` for
        // now; Phase 5b splits it into its own `cohere.rs` if needed.
        RerankingType::Cohere | RerankingType::Vllm => Arc::new(VllmReranker::new(config)),
        RerankingType::Pinecone => Arc::new(PineconeReranker::new(config)),
        RerankingType::Tei => Arc::new(TeiReranker::new(config)),
        RerankingType::HuggingFace => Arc::new(HuggingFaceReranker::new(config)),
        RerankingType::Onnx => Arc::new(OnnxReranker::new(config)),
    };
    Ok(reranker)
}
