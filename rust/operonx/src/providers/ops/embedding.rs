//! `EmbeddingOp` — texts → embedding vectors via ResourceHub.
//!
//! Mirrors Python [`operonx/providers/ops/embedding.py`](../../../../../operonx/providers/ops/embedding.py).

use serde_json::{json, Map, Value};

use super::utils::resolve_embedder;
use crate::core::configs::op_config::OpConfig;
use crate::core::exceptions::{OpError, OperonError};
use crate::providers::embeddings::EmbedOpts;

/// Execute the embedding op.
pub async fn execute(op: &OpConfig, inputs: Map<String, Value>) -> Result<Value, OperonError> {
    let resources = op.resource_keys();
    let key = resources.first().ok_or_else(|| {
        OperonError::Config(format!("EmbeddingOp '{}' missing `resource`", op.full_name))
    })?;

    let texts = parse_texts(inputs.get("texts"))?;
    let backend = resolve_embedder(key)?;
    let opts = EmbedOpts::default();

    match backend.run(texts.clone(), &opts).await {
        Ok(result) => Ok(json!({"embeddings": result.embeddings})),
        Err(e) => Err(OperonError::Op(OpError::Embedding(format!(
            "embedding backend '{}' failed for {} texts: {}",
            key,
            texts.len(),
            e
        )))),
    }
}

fn parse_texts(raw: Option<&Value>) -> Result<Vec<String>, OperonError> {
    match raw {
        Some(Value::String(s)) => Ok(vec![s.clone()]),
        Some(Value::Array(a)) => a
            .iter()
            .map(|v| {
                v.as_str().map(String::from).ok_or_else(|| {
                    OperonError::Config("EmbeddingOp: `texts` items must be strings".into())
                })
            })
            .collect(),
        _ => Err(OperonError::Config(
            "EmbeddingOp: `texts` must be a string or list of strings".into(),
        )),
    }
}
