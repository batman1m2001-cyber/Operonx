//! HuggingFace embedding provider — PyO3 bridge to Python's HFEmbedding.
//!
//! Calls Python's `hush.providers.embeddings.huggingface.HFEmbedding` via PyO3.
//! This allows Rust mode to support local HuggingFace transformer models
//! without needing torch/transformers as Rust dependencies.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde_json::Value;

use crate::config::embedding::EmbeddingConfig;
use crate::http::{ProviderError, ProviderResult};

/// Embed texts using HuggingFace transformers via Python bridge.
///
/// Acquires the GIL, creates an HFEmbedding instance with the config,
/// and calls `run_sync(texts)`. Uses `spawn_blocking` to avoid blocking
/// the async runtime.
pub async fn embed(config: &EmbeddingConfig, texts: &[String]) -> ProviderResult<Value> {
    let model = config.model.clone();
    let dimensions = config.dimensions;
    let texts = texts.to_vec();

    tokio::task::spawn_blocking(move || {
        Python::with_gil(|py| -> ProviderResult<Value> {
            // Build EmbeddingConfig
            let cfg_mod = py
                .import_bound("hush.providers.embeddings.config")
                .map_err(|e| import_error("hush.providers.embeddings.config", e))?;
            let cfg_cls = cfg_mod
                .getattr("EmbeddingConfig")
                .map_err(|e| import_error("EmbeddingConfig", e))?;

            let kwargs = PyDict::new_bound(py);
            kwargs.set_item("api_type", "hf").unwrap();
            if let Some(ref m) = model {
                kwargs.set_item("model", m).unwrap();
            }
            if let Some(d) = dimensions {
                kwargs.set_item("dimensions", d).unwrap();
            }

            let cfg = cfg_cls
                .call((), Some(&kwargs))
                .map_err(|e| py_call_error("EmbeddingConfig()", e))?;

            // Create HFEmbedding instance
            let hf_mod = py
                .import_bound("hush.providers.embeddings.huggingface")
                .map_err(|e| import_error("hush.providers.embeddings.huggingface", e))?;
            let instance = hf_mod
                .getattr("HFEmbedding")
                .map_err(|e| import_error("HFEmbedding", e))?
                .call1((&cfg,))
                .map_err(|e| py_call_error("HFEmbedding(config)", e))?;

            // Call run_sync(texts)
            let texts_list = PyList::new_bound(py, &texts);
            let result = instance
                .call_method1("run_sync", (&texts_list,))
                .map_err(|e| py_call_error("HFEmbedding.run_sync()", e))?;

            // Convert PyDict → serde_json::Value
            crate::py_serde::py_to_json(py, &result).map_err(|e| ProviderError {
                message: format!("Failed to convert HF embedding result: {}", e),
                status_code: None,
                error_code: None,
            })
        })
    })
    .await
    .map_err(|e| ProviderError {
        message: format!("HF embedding task failed: {}", e),
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
