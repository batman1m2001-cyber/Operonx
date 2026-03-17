//! EmbedderProvider trait — mirrors Python's `embeddings/base.py` (BaseEmbedding).
//!
//! All embedding backends (OpenAI, vLLM, ONNX) implement this trait.

use crate::http::ProviderResult;

/// Base trait for embedding providers.
///
/// Mirrors Python's BaseEmbedding with `embed()` method.
pub trait EmbedderProvider: Send + Sync {
    /// Generate embeddings for a list of texts.
    fn embed(
        &self,
        texts: &[String],
    ) -> impl std::future::Future<Output = ProviderResult<Vec<Vec<f32>>>> + Send;
}
