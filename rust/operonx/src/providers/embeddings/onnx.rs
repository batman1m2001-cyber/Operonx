//! ONNX Runtime embedding backend.
//!
//! Mirrors Python [`operon/providers/embeddings/onnx.py`](../../../../../operon/providers/embeddings/onnx.py).
//! Feature-gated behind `onnx` — requires `ort` + `tokenizers` crates (see
//! `Cargo.toml` `[features] onnx = ["dep:ort", "dep:tokenizers"]`).
//!
//! # Phase 5 scope
//! Struct + trait impl stub. Session loading + inference will land as part
//! of the shared [`super::super::onnx::backend`] extraction in Phase 5b.

use async_trait::async_trait;

use super::base::{BaseEmbedder, EmbedOpts, EmbedResult};
use super::config::EmbeddingConfig;
use crate::core::exceptions::OperonError;

pub struct OnnxEmbedder {
    pub config: EmbeddingConfig,
}

impl OnnxEmbedder {
    pub fn new(config: EmbeddingConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseEmbedder for OnnxEmbedder {
    async fn run(
        &self,
        _texts: Vec<String>,
        _opts: &EmbedOpts,
    ) -> Result<EmbedResult, OperonError> {
        #[cfg(feature = "onnx")]
        {
            return Err(OperonError::Provider(
                "OnnxEmbedder::run not yet implemented (Phase 5b — shared ONNX backend)".into(),
            ));
        }
        #[cfg(not(feature = "onnx"))]
        {
            Err(OperonError::Provider(
                "operonx built without the `onnx` feature — rebuild with --features onnx".into(),
            ))
        }
    }

    fn output_dim(&self) -> usize {
        self.config.dimensions.unwrap_or(0)
    }
}
