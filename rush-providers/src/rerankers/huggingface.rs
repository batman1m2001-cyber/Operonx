//! HuggingFace reranker provider — PyO3 bridge to Python's HFReranker.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde_json::Value;

use crate::config::reranking::RerankingConfig;
use crate::http::{ProviderError, ProviderResult};

/// Rerank using HuggingFace sentence-transformers via Python bridge.
pub async fn rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    let model = config.model.clone();
    let query = query.to_string();
    let texts = texts.to_vec();

    tokio::task::spawn_blocking(move || {
        Python::with_gil(|py| -> ProviderResult<Value> {
            // Build RerankingConfig
            let cfg_mod = py
                .import_bound("hush.providers.rerankers.config")
                .map_err(|e| import_error("hush.providers.rerankers.config", e))?;
            let cfg_cls = cfg_mod
                .getattr("RerankingConfig")
                .map_err(|e| import_error("RerankingConfig", e))?;

            let kwargs = PyDict::new_bound(py);
            kwargs.set_item("api_type", "hf").unwrap();
            if let Some(ref m) = model {
                kwargs.set_item("model", m).unwrap();
            }

            let cfg = cfg_cls
                .call((), Some(&kwargs))
                .map_err(|e| py_call_error("RerankingConfig()", e))?;

            // Create HFReranker instance
            let hf_mod = py
                .import_bound("hush.providers.rerankers.huggingface")
                .map_err(|e| import_error("hush.providers.rerankers.huggingface", e))?;
            let instance = hf_mod
                .getattr("HFReranker")
                .map_err(|e| import_error("HFReranker", e))?
                .call1((&cfg,))
                .map_err(|e| py_call_error("HFReranker(config)", e))?;

            // Call run_sync(query, texts, top_k, threshold)
            let texts_list = PyList::new_bound(py, &texts);
            let top_k_py: PyObject = match top_k {
                Some(k) => k.to_object(py),
                None => py.None(),
            };

            let result = instance
                .call_method1("run_sync", (&query, &texts_list, top_k_py, threshold))
                .map_err(|e| py_call_error("HFReranker.run_sync()", e))?;

            // Convert result list → {"results": [...]}
            let json_result =
                crate::py_serde::py_to_json(py, &result).map_err(|e| ProviderError {
                    message: format!("Failed to convert HF reranker result: {}", e),
                    status_code: None,
                    error_code: None,
                })?;

            // Python returns [{"index": i, "score": s}, ...] directly
            Ok(serde_json::json!({ "results": json_result }))
        })
    })
    .await
    .map_err(|e| ProviderError {
        message: format!("HF reranker task failed: {}", e),
        status_code: None,
        error_code: None,
    })?
}

fn import_error(module: &str, e: PyErr) -> ProviderError {
    ProviderError {
        message: format!(
            "Failed to import '{}': {}. Ensure hush-providers is installed with HF dependencies.",
            module, e
        ),
        status_code: None,
        error_code: None,
    }
}

fn py_call_error(call: &str, e: PyErr) -> ProviderError {
    ProviderError {
        message: format!("Python call '{}' failed: {}", call, e),
        status_code: None,
        error_code: None,
    }
}
