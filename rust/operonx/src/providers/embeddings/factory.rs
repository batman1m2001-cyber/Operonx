//! `create_embedder` — dispatch an [`EmbeddingConfig`] to the matching backend.
//!
//! Mirrors Python [`operonx/providers/embeddings/factory.py`](../../../../../operonx/providers/embeddings/factory.py).

use std::sync::Arc;

use super::base::BaseEmbedder;
use super::config::{EmbeddingConfig, EmbeddingType};
use super::huggingface::HuggingFaceEmbedder;
use super::onnx::OnnxEmbedder;
use super::tei::TeiEmbedder;
use super::vllm::VllmEmbedder;
use crate::core::exceptions::OperonError;

/// Construct the embedder matching `config.api_type`.
pub fn create_embedder(config: EmbeddingConfig) -> Result<Arc<dyn BaseEmbedder>, OperonError> {
    let embedder: Arc<dyn BaseEmbedder> = match config.api_type {
        // OpenAI / Azure / Gemini / vLLM all share the OpenAI-flavored wire
        // shape — [`VllmEmbedder`] handles them.
        EmbeddingType::OpenAI
        | EmbeddingType::Azure
        | EmbeddingType::Gemini
        | EmbeddingType::Vllm => Arc::new(VllmEmbedder::new(config)),
        EmbeddingType::Tei => Arc::new(TeiEmbedder::new(config)),
        EmbeddingType::HuggingFace => Arc::new(HuggingFaceEmbedder::new(config)),
        EmbeddingType::Onnx => Arc::new(OnnxEmbedder::new(config)),
    };
    Ok(embedder)
}
