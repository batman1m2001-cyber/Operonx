//! Provider configuration structs — parsed from Python config dicts.
//!
//! Each provider type has its own config module mirroring hush-providers:
//! - llm: LLMConfig (OpenAI, Azure, vLLM, Gemini)
//! - embedding: EmbeddingConfig (OpenAI, Azure, vLLM, TEI, HF, ONNX)
//! - reranking: RerankingConfig (Cohere, TEI, vLLM, Pinecone, HF, ONNX)

pub mod embedding;
pub mod llm;
pub mod reranking;

use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Provider-specific configuration, parsed from the serialized op dict.
///
/// Each variant wraps the corresponding provider config struct.
/// The `ProviderConfig` is stored on `OpConfig` and used by native HTTP ops.
pub enum ProviderConfig {
    LLM(LLMProviderConfig),
    Embedding(embedding::EmbeddingConfig),
    Reranking(reranking::RerankingConfig),
}

/// LLM provider config — wraps one or more LLMConfig instances (for load balancing).
pub struct LLMProviderConfig {
    /// Backend configs — one per resource (multiple for load balancing).
    pub configs: Vec<llm::LLMConfig>,
    /// Resource key(s) from ResourceHub.
    pub resources: Vec<String>,
    /// Weight ratios for load balancing (sum to 1.0).
    pub ratios: Vec<f64>,
    /// Fallback backend configs (tried in order if primary fails).
    pub fallback_configs: Vec<llm::LLMConfig>,
    /// Fallback resource keys.
    pub fallback: Vec<String>,
    /// Whether to use OpenAI Batch API mode.
    pub batch_mode: bool,
}

/// Parse a ProviderConfig from an op's serialized dict.
///
/// Detects provider type from `op_type` field and parses the appropriate config.
/// Returns `None` if the op type is not a provider op or has no resource config.
pub fn parse_provider_config(
    py: Python,
    op_type: &str,
    dict: &Bound<'_, PyDict>,
) -> PyResult<Option<ProviderConfig>> {
    match op_type {
        "llm" => parse_llm_provider(py, dict),
        "embedding" => parse_embedding_provider(py, dict),
        "rerank" => parse_reranking_provider(py, dict),
        _ => Ok(None),
    }
}

/// Parse LLM provider config from op dict.
fn parse_llm_provider(
    py: Python,
    dict: &Bound<'_, PyDict>,
) -> PyResult<Option<ProviderConfig>> {
    // resource_configs: list of backend config dicts
    let configs_obj = match dict.get_item("resource_configs")? {
        Some(v) if !v.is_none() => v,
        _ => return Ok(None),
    };

    let configs_list: &Bound<'_, pyo3::types::PyList> = configs_obj.downcast()?;
    let mut configs = Vec::with_capacity(configs_list.len());

    for item in configs_list.iter() {
        let cfg_dict: &Bound<'_, PyDict> = item.downcast()?;
        configs.push(llm::LLMConfig::from_dict(py, cfg_dict)?);
    }

    if configs.is_empty() {
        return Ok(None);
    }

    // Parse op-level fields
    let resources = parse_string_or_list(dict, "resource")?;
    let ratios = parse_float_list(dict, "ratios")?;
    let fallback = parse_string_list(dict, "fallback")?;
    let batch_mode = dict
        .get_item("batch_mode")?
        .map(|v| v.extract::<bool>().unwrap_or(false))
        .unwrap_or(false);

    // Parse fallback configs (tried in order if primary fails)
    let mut fallback_configs = Vec::new();
    if let Some(fb_obj) = dict.get_item("fallback_configs")? {
        if !fb_obj.is_none() {
            if let Ok(fb_list) = fb_obj.downcast::<pyo3::types::PyList>() {
                for item in fb_list.iter() {
                    if let Ok(cfg_dict) = item.downcast::<PyDict>() {
                        if let Ok(cfg) = llm::LLMConfig::from_dict(py, cfg_dict) {
                            fallback_configs.push(cfg);
                        }
                    }
                }
            }
        }
    }

    Ok(Some(ProviderConfig::LLM(LLMProviderConfig {
        configs,
        resources,
        ratios,
        fallback_configs,
        fallback,
        batch_mode,
    })))
}

/// Parse Embedding provider config from op dict.
fn parse_embedding_provider(
    py: Python,
    dict: &Bound<'_, PyDict>,
) -> PyResult<Option<ProviderConfig>> {
    let cfg_obj = match dict.get_item("resource_config")? {
        Some(v) if !v.is_none() => v,
        _ => return Ok(None),
    };

    let cfg_dict: &Bound<'_, PyDict> = cfg_obj.downcast()?;
    let config = embedding::EmbeddingConfig::from_dict(py, cfg_dict)?;

    Ok(Some(ProviderConfig::Embedding(config)))
}

/// Parse Reranking provider config from op dict.
fn parse_reranking_provider(
    py: Python,
    dict: &Bound<'_, PyDict>,
) -> PyResult<Option<ProviderConfig>> {
    let cfg_obj = match dict.get_item("resource_config")? {
        Some(v) if !v.is_none() => v,
        _ => return Ok(None),
    };

    let cfg_dict: &Bound<'_, PyDict> = cfg_obj.downcast()?;
    let config = reranking::RerankingConfig::from_dict(py, cfg_dict)?;

    Ok(Some(ProviderConfig::Reranking(config)))
}

// =============================================================================
// Parsing helpers
// =============================================================================

/// Extract a string or list of strings from a dict key.
/// Handles both `"value"` and `["value1", "value2"]`.
fn parse_string_or_list(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<String>> {
    match dict.get_item(key)? {
        Some(v) if !v.is_none() => {
            // Try as list first, then as single string
            if let Ok(list) = v.extract::<Vec<String>>() {
                Ok(list)
            } else if let Ok(s) = v.extract::<String>() {
                Ok(vec![s])
            } else {
                Ok(Vec::new())
            }
        }
        _ => Ok(Vec::new()),
    }
}

/// Extract a list of floats from a dict key.
fn parse_float_list(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<f64>> {
    match dict.get_item(key)? {
        Some(v) if !v.is_none() => v.extract::<Vec<f64>>().or(Ok(Vec::new())),
        _ => Ok(Vec::new()),
    }
}

/// Extract a list of strings from a dict key.
fn parse_string_list(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<String>> {
    match dict.get_item(key)? {
        Some(v) if !v.is_none() => v.extract::<Vec<String>>().or(Ok(Vec::new())),
        _ => Ok(Vec::new()),
    }
}

// =============================================================================
// Shared helpers for config parsing
// =============================================================================

/// Extract a required string from a dict.
pub(crate) fn get_string(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<String> {
    dict.get_item(key)?
        .ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("missing '{}'", key))
        })
        .and_then(|v| v.extract::<String>())
}

/// Extract an optional string from a dict.
pub(crate) fn get_opt_string(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<String>> {
    match dict.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract::<String>()?)),
        _ => Ok(None),
    }
}

/// Extract an optional f64 from a dict.
pub(crate) fn get_opt_f64(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<f64>> {
    match dict.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract::<f64>()?)),
        _ => Ok(None),
    }
}

/// Extract an optional usize from a dict.
pub(crate) fn get_opt_usize(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<usize>> {
    match dict.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract::<usize>()?)),
        _ => Ok(None),
    }
}

/// Extract an optional i64 from a dict.
pub(crate) fn get_opt_i64(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<i64>> {
    match dict.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract::<i64>()?)),
        _ => Ok(None),
    }
}
