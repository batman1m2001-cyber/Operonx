//! Embedding op — dispatches to appropriate backend.
//!
//! Mirrors hush-providers/hush/providers/ops/embedding.py.
//! Supports: OpenAI, Azure, vLLM (HTTP), ONNX (native Rust ort).

use serde_json::Value;

use crate::config::embedding::EmbeddingConfig;
use crate::http::ProviderResult;

/// Execute an embedding op.
///
/// Inputs: {"texts": [...]} or {"texts": "single string"}
/// Outputs: {"embeddings": [[float, ...], ...]}
pub async fn execute(inputs: Value, config: &EmbeddingConfig) -> ProviderResult<Value> {
    // Extract texts — handle both single string and list
    let texts: Vec<String> = match inputs.get("texts") {
        Some(Value::Array(arr)) => arr
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect(),
        Some(Value::String(s)) => vec![s.clone()],
        _ => Vec::new(),
    };

    if texts.is_empty() {
        return Ok(serde_json::json!({ "embeddings": [] }));
    }

    // Dispatch to backend (HTTP, PyO3, or native Rust)
    crate::embeddings::embed(config, &texts).await
}
