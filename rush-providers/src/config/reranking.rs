//! Reranking provider configuration — mirrors hush-providers RerankingConfig.
//!
//! Supports: Cohere, vLLM, Pinecone, HF, ONNX.
//! Parsed from `RerankingConfig.model_dump(mode="json")` output.

use pyo3::prelude::*;
use pyo3::types::PyDict;

use super::{get_opt_string, get_string};

/// Reranking backend configuration.
pub struct RerankingConfig {
    /// API type: "cohere", "tei", "vllm", "pinecone", "hf", "onnx"
    pub api_type: String,
    pub api_key: Option<String>,
    pub api_version: Option<String>,
    pub base_url: Option<String>,
    pub model: Option<String>,
}

impl RerankingConfig {
    /// Parse from a Python dict (output of `RerankingConfig.model_dump(mode="json")`).
    pub fn from_dict(_py: Python, dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        Ok(RerankingConfig {
            api_type: get_string(dict, "api_type")?,
            api_key: get_opt_string(dict, "api_key")?,
            api_version: get_opt_string(dict, "api_version")?,
            base_url: get_opt_string(dict, "base_url")?,
            model: get_opt_string(dict, "model")?,
        })
    }
}
