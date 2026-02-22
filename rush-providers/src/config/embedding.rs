//! Embedding provider configuration — mirrors hush-providers EmbeddingConfig.
//!
//! Supports: OpenAI, Azure, Gemini, TEI, vLLM, HF, ONNX.
//! Parsed from `EmbeddingConfig.model_dump(mode="json")` output.

use pyo3::prelude::*;
use pyo3::types::PyDict;

use super::{get_opt_string, get_opt_usize, get_string};

/// Embedding backend configuration.
pub struct EmbeddingConfig {
    /// API type: "openai", "azure", "gemini", "tei", "vllm", "hf", "onnx"
    pub api_type: String,
    pub api_key: Option<String>,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub embed_batch_size: Option<usize>,
    pub dimensions: Option<usize>,
}

impl EmbeddingConfig {
    /// Parse from a Python dict (output of `EmbeddingConfig.model_dump(mode="json")`).
    pub fn from_dict(_py: Python, dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        Ok(EmbeddingConfig {
            api_type: get_string(dict, "api_type")?,
            api_key: get_opt_string(dict, "api_key")?,
            base_url: get_opt_string(dict, "base_url")?,
            model: get_opt_string(dict, "model")?,
            embed_batch_size: get_opt_usize(dict, "embed_batch_size")?,
            dimensions: get_opt_usize(dict, "dimensions")?,
        })
    }
}
