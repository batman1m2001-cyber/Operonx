//! EmbeddingOp — embedding provider calls.
//!
//! Mirrors Python's `providers/ops/embedding.py` (EmbeddingOp).
//! Supports: OpenAI, Azure, vLLM (HTTP), ONNX (native Rust ort).

use hush_icore::config::BaseOpConfig;
use hush_icore::error::RushError;
use hush_icore::ops::op_trait::{Op, OpContext};

pub struct EmbeddingOp<'a> {
    pub config: &'a BaseOpConfig,
}

impl<'a> EmbeddingOp<'a> {
    pub fn new(config: &'a BaseOpConfig) -> Self {
        EmbeddingOp { config }
    }
}

impl Op for EmbeddingOp<'_> {
    fn op_config(&self) -> &BaseOpConfig {
        self.config
    }

    fn execute_core(
        &self,
        inputs: serde_json::Map<String, serde_json::Value>,
        _ctx: &OpContext,
    ) -> Result<Option<serde_json::Value>, RushError> {
        let config_json = self.config.provider_config.as_ref().ok_or_else(|| {
            RushError::ProviderError(format!("EmbeddingOp '{}' missing provider_config", self.config.full_name))
        })?;
        let result = hush_icore::runtime::block_on_async(async {
            crate::ops::execute_from_json("embedding", serde_json::Value::Object(inputs), config_json).await
        })
        .map_err(|e| RushError::ProviderError(format!("Embedding op error: {}", e)))?;
        Ok(Some(result))
    }
}

// === Internal execution logic ===

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
