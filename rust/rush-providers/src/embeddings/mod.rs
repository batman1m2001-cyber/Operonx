//! Embedding providers — per-provider implementations mirroring hush-providers/embeddings/.
//!
//! Dispatches by api_type to the appropriate provider:
//! - OpenAI / vLLM / Azure → openai.rs (native HTTP)
//! - HuggingFace → huggingface.rs (stub — use ONNX instead)
//! - ONNX → onnx.rs (pure Rust: ort + tokenizers)

pub mod huggingface;
#[cfg(feature = "onnx")]
pub mod onnx;
pub mod openai;

use serde_json::Value;

use crate::config::embedding::EmbeddingConfig;
use crate::http::{ProviderError, ProviderResult};

/// Dispatch embedding to the appropriate provider based on api_type.
pub async fn embed(config: &EmbeddingConfig, texts: &[String]) -> ProviderResult<Value> {
    match config.api_type.as_str() {
        "openai" | "vllm" | "azure" => openai::embed(config, texts).await,
        "hf" => huggingface::embed(config, texts).await,
        #[cfg(feature = "onnx")]
        "onnx" => onnx::embed(config, texts).await,
        other => Err(ProviderError {
            message: format!(
                "Unsupported embedding api_type: '{}'. Supported: openai, vllm, azure, hf, onnx",
                other
            ),
            status_code: None,
            error_code: None,
        }),
    }
}
