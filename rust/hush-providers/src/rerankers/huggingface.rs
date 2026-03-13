//! HuggingFace reranker provider — stub.
//!
//! Previously used PyO3 bridge to call Python's HFReranker.
//! Without PyO3, HF rerankers require an ONNX export or a native Rust
//! implementation. Use api_type="onnx" for local model inference.

use serde_json::Value;

use crate::config::reranking::RerankingConfig;
use crate::http::{ProviderError, ProviderResult};

/// HuggingFace reranker is not available without Python.
///
/// Use api_type="onnx" with an exported ONNX model instead,
/// or use the Python backend (hush-providers) for HF support.
pub async fn rerank(
    _config: &RerankingConfig,
    _query: &str,
    _texts: &[String],
    _top_k: Option<usize>,
    _threshold: f64,
) -> ProviderResult<Value> {
    Err(ProviderError {
        message: "HuggingFace reranker requires Python (PyO3 removed). \
                  Use api_type=\"onnx\" for local model inference, \
                  or use the Python backend."
            .to_string(),
        status_code: None,
        error_code: None,
    })
}
