//! HuggingFace Transformers embedding backend.
//!
//! Mirrors Python [`operon/providers/embeddings/huggingface.py`](../../../../../operon/providers/embeddings/huggingface.py).
//! Per plan §5a recommendation, the Rust equivalent stays a **stub** in
//! v0.6 — the Python HF backend exists for authoring; Rust prod paths use
//! ONNX exports ([`super::onnx::OnnxEmbedder`]).

use async_trait::async_trait;

use super::base::{BaseEmbedder, EmbedOpts, EmbedResult};
use super::config::EmbeddingConfig;
use crate::core::exceptions::OperonError;

pub struct HuggingFaceEmbedder {
    pub config: EmbeddingConfig,
}

impl HuggingFaceEmbedder {
    pub fn new(config: EmbeddingConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseEmbedder for HuggingFaceEmbedder {
    async fn run(
        &self,
        _texts: Vec<String>,
        _opts: &EmbedOpts,
    ) -> Result<EmbedResult, OperonError> {
        Err(OperonError::Provider(
            "HuggingFaceEmbedder not supported in Rust v0.6 — convert your model to ONNX and use `api_type: onnx` instead".into(),
        ))
    }

    fn output_dim(&self) -> usize {
        self.config.dimensions.unwrap_or(0)
    }
}
