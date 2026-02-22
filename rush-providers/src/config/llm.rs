//! LLM provider configuration — mirrors hush-providers LLMConfig hierarchy.
//!
//! Hierarchy:
//!   LLMConfig (base — common fields)
//!   ├── OpenAIConfig  (api_type: openai | vllm)
//!   ├── AzureConfig   (api_type: azure)
//!   └── GeminiConfig  (api_type: gemini)
//!
//! Parsed from `config.model_dump(mode="json")` output.

use pyo3::prelude::*;
use pyo3::types::PyDict;

use super::{get_opt_f64, get_opt_string, get_opt_usize, get_string};

// =============================================================================
// LLMConfig enum — dispatches by api_type
// =============================================================================

/// LLM backend configuration — tagged enum mirroring Python's LLMConfig hierarchy.
pub enum LLMConfig {
    OpenAI(OpenAIConfig),
    Azure(AzureConfig),
    Gemini(GeminiConfig),
}

impl LLMConfig {
    /// Parse from a Python dict (output of `LLMConfig.model_dump(mode="json")`).
    /// Dispatches to the correct variant based on `api_type`.
    pub fn from_dict(_py: Python, dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        let api_type = get_string(dict, "api_type")?;

        match api_type.as_str() {
            "openai" | "vllm" => Ok(LLMConfig::OpenAI(OpenAIConfig::from_dict(dict, &api_type)?)),
            "azure" => Ok(LLMConfig::Azure(AzureConfig::from_dict(dict)?)),
            "gemini" => Ok(LLMConfig::Gemini(GeminiConfig::from_dict(dict)?)),
            _ => Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "Unsupported LLM api_type: '{}'",
                api_type
            ))),
        }
    }

    /// Get the api_type string.
    pub fn api_type(&self) -> &str {
        match self {
            LLMConfig::OpenAI(c) => &c.api_type,
            LLMConfig::Azure(_) => "azure",
            LLMConfig::Gemini(_) => "gemini",
        }
    }

    /// Get the model name (common across all variants).
    pub fn model(&self) -> Option<&str> {
        match self {
            LLMConfig::OpenAI(c) => Some(&c.model),
            LLMConfig::Azure(c) => Some(&c.model),
            LLMConfig::Gemini(c) => Some(&c.model),
        }
    }
}

// =============================================================================
// Common base fields (shared by all LLMConfig subclasses)
// =============================================================================

/// Fields shared by all LLM configs (mirrors Python's LLMConfig base).
pub struct LLMBaseFields {
    pub proxy: Option<String>,
    pub cost_per_input_token: Option<f64>,
    pub cost_per_output_token: Option<f64>,
}

fn parse_base_fields(dict: &Bound<'_, PyDict>) -> PyResult<LLMBaseFields> {
    Ok(LLMBaseFields {
        proxy: get_opt_string(dict, "proxy")?,
        cost_per_input_token: get_opt_f64(dict, "cost_per_input_token")?,
        cost_per_output_token: get_opt_f64(dict, "cost_per_output_token")?,
    })
}

// =============================================================================
// OpenAIConfig (also used by vLLM — OpenAI-compatible)
// =============================================================================

/// OpenAI / vLLM configuration.
/// Mirrors `hush.providers.llms.config.OpenAIConfig`.
pub struct OpenAIConfig {
    pub base: LLMBaseFields,
    /// "openai" or "vllm"
    pub api_type: String,
    pub api_key: String,
    pub base_url: String,
    pub model: String,
    // Batch API config
    pub batch_size: usize,
    pub batch_flush_interval: f64,
    pub batch_poll_interval: f64,
    pub batch_timeout: f64,
}

impl OpenAIConfig {
    fn from_dict(dict: &Bound<'_, PyDict>, api_type: &str) -> PyResult<Self> {
        Ok(OpenAIConfig {
            base: parse_base_fields(dict)?,
            api_type: api_type.to_string(),
            api_key: get_string(dict, "api_key")?,
            base_url: get_string(dict, "base_url")?,
            model: get_string(dict, "model")?,
            batch_size: get_opt_usize(dict, "batch_size")?.unwrap_or(50000),
            batch_flush_interval: get_opt_f64(dict, "batch_flush_interval")?.unwrap_or(60.0),
            batch_poll_interval: get_opt_f64(dict, "batch_poll_interval")?.unwrap_or(30.0),
            batch_timeout: get_opt_f64(dict, "batch_timeout")?.unwrap_or(86400.0),
        })
    }
}

// =============================================================================
// AzureConfig
// =============================================================================

/// Azure OpenAI configuration.
/// Mirrors `hush.providers.llms.config.AzureConfig`.
pub struct AzureConfig {
    pub base: LLMBaseFields,
    pub api_key: String,
    pub api_version: String,
    pub azure_endpoint: String,
    pub model: String,
}

impl AzureConfig {
    fn from_dict(dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        Ok(AzureConfig {
            base: parse_base_fields(dict)?,
            api_key: get_string(dict, "api_key")?,
            api_version: get_string(dict, "api_version")?,
            azure_endpoint: get_string(dict, "azure_endpoint")?,
            model: get_string(dict, "model")?,
        })
    }
}

// =============================================================================
// GeminiConfig
// =============================================================================

/// Google Gemini configuration (service account auth).
/// Mirrors `hush.providers.llms.config.GeminiConfig`.
pub struct GeminiConfig {
    pub base: LLMBaseFields,
    pub project_id: String,
    pub private_key_id: String,
    pub private_key: String,
    pub client_email: String,
    pub client_id: String,
    pub auth_uri: String,
    pub token_uri: String,
    pub auth_provider_x509_cert_url: String,
    pub client_x509_cert_url: String,
    pub universe_domain: String,
    pub location: String,
    pub model: String,
}

impl GeminiConfig {
    fn from_dict(dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        Ok(GeminiConfig {
            base: parse_base_fields(dict)?,
            project_id: get_string(dict, "project_id")?,
            private_key_id: get_string(dict, "private_key_id")?,
            private_key: get_string(dict, "private_key")?,
            client_email: get_string(dict, "client_email")?,
            client_id: get_string(dict, "client_id")?,
            auth_uri: get_opt_string(dict, "auth_uri")?
                .unwrap_or_else(|| "https://accounts.google.com/o/oauth2/auth".to_string()),
            token_uri: get_opt_string(dict, "token_uri")?
                .unwrap_or_else(|| "https://oauth2.googleapis.com/token".to_string()),
            auth_provider_x509_cert_url: get_opt_string(dict, "auth_provider_x509_cert_url")?
                .unwrap_or_else(|| "https://www.googleapis.com/oauth2/v1/certs".to_string()),
            client_x509_cert_url: get_string(dict, "client_x509_cert_url")?,
            universe_domain: get_opt_string(dict, "universe_domain")?
                .unwrap_or_else(|| "googleapis.com".to_string()),
            location: get_opt_string(dict, "location")?
                .unwrap_or_else(|| "us-central1".to_string()),
            model: get_opt_string(dict, "model")?
                .unwrap_or_else(|| "gemini-2.0-flash-001".to_string()),
        })
    }
}
