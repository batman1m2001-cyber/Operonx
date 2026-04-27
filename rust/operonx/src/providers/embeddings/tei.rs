//! HuggingFace Text Embeddings Inference (TEI) embedding backend.
//!
//! Mirrors Python [`operonx/providers/embeddings/tei.py`](../../../../../operonx/providers/embeddings/tei.py).
//! Net-new Rust port (plan §5a marks this file as 🆕).
//!
//! # Phase 5 scope
//! Stub — struct + config. The HTTP POST lands in Phase 5b; the TEI API is
//! OpenAI-compatible shape-wise, so the implementation can reuse
//! [`super::vllm::VllmEmbedder`]'s body serialization once extracted.

use async_trait::async_trait;

use super::base::{BaseEmbedder, EmbedOpts, EmbedResult};
use super::config::EmbeddingConfig;
use crate::core::exceptions::OperonError;

pub struct TeiEmbedder {
    pub config: EmbeddingConfig,
}

impl TeiEmbedder {
    pub fn new(config: EmbeddingConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseEmbedder for TeiEmbedder {
    async fn run(
        &self,
        _texts: Vec<String>,
        _opts: &EmbedOpts,
    ) -> Result<EmbedResult, OperonError> {
        Err(OperonError::Provider(
            "TeiEmbedder::run not yet implemented (Phase 5b)".into(),
        ))
    }

    fn output_dim(&self) -> usize {
        self.config.dimensions.unwrap_or(0)
    }
}
