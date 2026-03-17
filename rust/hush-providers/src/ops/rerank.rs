//! RerankOp — reranking provider calls.
//!
//! Mirrors Python's `providers/ops/rerank.py` (RerankOp).

use hush_icore::config::BaseOpConfig;
use hush_icore::error::RushError;
use hush_icore::ops::op_trait::{Op, OpContext};

pub struct RerankOp<'a> {
    pub config: &'a BaseOpConfig,
}

impl<'a> RerankOp<'a> {
    pub fn new(config: &'a BaseOpConfig) -> Self {
        RerankOp { config }
    }
}

impl Op for RerankOp<'_> {
    fn op_config(&self) -> &BaseOpConfig {
        self.config
    }

    fn execute_core(
        &self,
        inputs: serde_json::Map<String, serde_json::Value>,
        _ctx: &OpContext,
    ) -> Result<Option<serde_json::Value>, RushError> {
        let config_json = self.config.provider_config.as_ref().ok_or_else(|| {
            RushError::ProviderError(format!("RerankOp '{}' missing provider_config", self.config.full_name))
        })?;
        let result = hush_icore::runtime::block_on_async(async {
            crate::ops::execute_from_json("rerank", serde_json::Value::Object(inputs), config_json).await
        })
        .map_err(|e| RushError::ProviderError(format!("Rerank op error: {}", e)))?;
        Ok(Some(result))
    }
}

// --- Internal execution logic ---
// Handles both List[str] and List[Dict] document inputs.
// Supports: vLLM, Pinecone, Cohere (HTTP), ONNX (native Rust ort).

use serde_json::{json, Value};

use crate::config::reranking::RerankingConfig;
use crate::http::ProviderResult;

/// Execute a reranking op.
///
/// Inputs: {"query": str, "documents": [...], "top_k": int, "threshold": float}
/// Outputs: {"reranks": [{"content": str|dict, "score": float}, ...]}
pub async fn execute(inputs: Value, config: &RerankingConfig) -> ProviderResult<Value> {
    let query = inputs
        .get("query")
        .and_then(|v| v.as_str())
        .unwrap_or_default();

    let documents = inputs
        .get("documents")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    if documents.is_empty() {
        return Ok(json!({ "reranks": [] }));
    }

    let top_k = inputs
        .get("top_k")
        .and_then(|v| v.as_i64())
        .map(|k| if k <= 0 { None } else { Some(k as usize) })
        .unwrap_or(None);

    let threshold = inputs
        .get("threshold")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);

    // Detect document format: List[str] vs List[Dict]
    let is_dict = documents
        .first()
        .map(|d| d.is_object())
        .unwrap_or(false);

    // Extract text strings for the reranker
    let texts: Vec<String> = if is_dict {
        documents
            .iter()
            .map(|d| {
                d.get("content")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default()
                    .to_string()
            })
            .collect()
    } else {
        documents
            .iter()
            .filter_map(|d| d.as_str().map(|s| s.to_string()))
            .collect()
    };

    // Call reranker backend (HTTP, PyO3, or native Rust)
    let result = crate::rerankers::rerank(config, query, &texts, top_k, threshold).await?;

    // Build reranked documents with original data
    let ranked_results = result
        .get("results")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let reranks: Vec<Value> = ranked_results
        .into_iter()
        .filter_map(|r| {
            let idx = r.get("index")?.as_u64()? as usize;
            let score = r.get("score")?.as_f64()?;

            if is_dict {
                // Merge score into original dict
                let mut doc = documents.get(idx)?.clone();
                if let Some(obj) = doc.as_object_mut() {
                    obj.insert("score".to_string(), json!(score));
                }
                Some(doc)
            } else {
                // Wrap string with content + score
                let content = documents.get(idx)?;
                Some(json!({
                    "content": content,
                    "score": score,
                }))
            }
        })
        .collect();

    Ok(json!({ "reranks": reranks }))
}
