//! `RerankOp` — rank documents by relevance to a query.
//!
//! Mirrors Python [`operonx/providers/ops/rerank.py`](../../../../../operonx/providers/ops/rerank.py).

use serde_json::{json, Map, Value};

use super::utils::resolve_reranker;
use crate::core::configs::op_config::OpConfig;
use crate::core::exceptions::{OpError, OperonError};
use crate::providers::rerankers::RerankOpts;

/// Execute the rerank op.
pub async fn execute(
    op: &OpConfig,
    inputs: Map<String, Value>,
) -> Result<Value, OperonError> {
    let resources = op.resource_keys();
    let key = resources.first().ok_or_else(|| {
        OperonError::Config(format!("RerankOp '{}' missing `resource`", op.full_name))
    })?;

    let query = inputs
        .get("query")
        .and_then(|v| v.as_str())
        .ok_or_else(|| OperonError::Config("RerankOp: `query` must be a string".into()))?
        .to_string();

    let documents = inputs.get("documents").cloned().unwrap_or(json!([]));
    let docs_arr = documents
        .as_array()
        .ok_or_else(|| OperonError::Config("RerankOp: `documents` must be a list".into()))?;
    if docs_arr.is_empty() {
        return Ok(json!({"reranks": []}));
    }

    // `documents` can be List[str] or List[dict] (with a `content` key). The
    // backend only needs the text — remember whether inputs were dicts so we
    // can merge scores back onto each original doc.
    let (texts, is_dict) = extract_texts(docs_arr)?;

    let top_k = inputs.get("top_k").and_then(|v| v.as_i64()).unwrap_or(-1);
    let threshold = inputs
        .get("threshold")
        .and_then(|v| v.as_f64())
        .map(|v| v as f32)
        .unwrap_or(0.0);

    let top_k_req = if top_k > 0 { top_k as usize } else { texts.len() };
    let opts = RerankOpts {
        threshold,
        extras: Default::default(),
    };

    let backend = resolve_reranker(key)?;
    let results = match backend
        .run(query.clone(), texts.iter().map(|t| Value::from(t.clone())).collect(), top_k_req, &opts)
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return Err(OperonError::Op(OpError::Rerank(format!(
                "rerank backend '{}' failed (query_len={}, docs={}): {}",
                key,
                query.len(),
                docs_arr.len(),
                e
            ))));
        }
    };

    let mut reranks = Vec::with_capacity(results.len());
    for r in &results {
        let idx = r.index.min(docs_arr.len().saturating_sub(1));
        let base = &docs_arr[idx];
        if is_dict {
            let mut obj = base
                .as_object()
                .cloned()
                .unwrap_or_default();
            obj.insert("score".into(), json!(r.score));
            reranks.push(Value::Object(obj));
        } else {
            reranks.push(json!({"content": base.as_str().unwrap_or_default(), "score": r.score}));
        }
    }
    Ok(json!({"reranks": reranks}))
}

fn extract_texts(docs: &[Value]) -> Result<(Vec<String>, bool), OperonError> {
    if docs.is_empty() {
        return Ok((Vec::new(), false));
    }
    if docs.iter().all(|v| v.is_string()) {
        let texts = docs.iter().map(|v| v.as_str().unwrap().to_string()).collect();
        return Ok((texts, false));
    }
    if docs.iter().all(|v| v.is_object()) {
        let texts = docs
            .iter()
            .map(|v| {
                v.get("content")
                    .and_then(|c| c.as_str())
                    .unwrap_or_default()
                    .to_string()
            })
            .collect();
        return Ok((texts, true));
    }
    Err(OperonError::Op(OpError::Rerank(format!(
        "RerankOp: documents must be all strings or all objects (with 'content' key); got mixed shapes"
    ))))
}
