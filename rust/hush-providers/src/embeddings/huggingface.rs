//! HuggingFace embedding provider — stub.
//!
//! Previously used PyO3 bridge to call Python's HFEmbedding.
//! Without PyO3, HF embeddings require an ONNX export or a native Rust
//! implementation. Use api_type="onnx" for local model inference.

use serde_json::Value;

use crate::config::embedding::EmbeddingConfig;
use crate::http::{ProviderError, ProviderResult};

/// HuggingFace embedding is not available without Python.
///
/// Use api_type="onnx" with an exported ONNX model instead,
/// or use the Python backend (hush-providers) for HF support.
pub async fn embed(_config: &EmbeddingConfig, _texts: &[String]) -> ProviderResult<Value> {
    Err(ProviderError {
        message: "HuggingFace embedding requires Python (PyO3 removed). \
                  Use api_type=\"onnx\" for local model inference, \
                  or use the Python backend."
            .to_string(),
        status_code: None,
        error_code: None,
    })
}
