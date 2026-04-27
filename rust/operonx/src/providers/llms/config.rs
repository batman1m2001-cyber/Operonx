//! LLM provider configuration — mirrors Python [`operonx/providers/llms/config.py`](../../../../../operonx/providers/llms/config.py).
//!
//! Hierarchy mirrors Python's class tree:
//! - [`LLMConfig`] — discriminated union by `api_type`.
//! - [`OpenAIConfig`] — covers `openai`, `vllm`, generic OpenAI-compatible
//!   servers.
//! - [`AzureConfig`] — Azure OpenAI deployment.
//! - [`GeminiConfig`] — Google Vertex AI (service-account auth).
//! - [`AnthropicConfig`] — Claude direct API.

use serde::{Deserialize, Serialize};

/// Common cost/proxy fields shared by every LLM config.
///
/// Historically flattened into each concrete config via `#[serde(flatten)]`,
/// but serde can't combine `flatten` with `deny_unknown_fields` (the flatten
/// container catches every unknown key, defeating the deny). Since Phase 9
/// requires `deny_unknown_fields` on every config, the three fields are now
/// inlined into each concrete config and this struct is kept only for
/// ad-hoc construction in Rust-side code.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LLMBaseFields {
    #[serde(default)]
    pub proxy: Option<String>,
    #[serde(default)]
    pub cost_per_input_token: Option<f64>,
    #[serde(default)]
    pub cost_per_output_token: Option<f64>,
}

/// OpenAI-compatible configuration — the catch-all for OpenAI, vLLM, and
/// other OpenAI-API-compatible servers.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpenAIConfig {
    #[serde(default)]
    pub proxy: Option<String>,
    #[serde(default)]
    pub cost_per_input_token: Option<f64>,
    #[serde(default)]
    pub cost_per_output_token: Option<f64>,

    #[serde(default = "default_openai_api_type")]
    pub api_type: String,

    #[serde(default)]
    pub api_key: String,

    #[serde(default)]
    pub base_url: String,

    pub model: String,

    #[serde(default)]
    pub batch_size: usize,
    #[serde(default = "default_batch_flush")]
    pub batch_flush_interval: f64,
    #[serde(default = "default_batch_poll")]
    pub batch_poll_interval: f64,
    #[serde(default = "default_batch_timeout")]
    pub batch_timeout: f64,
}

fn default_openai_api_type() -> String {
    "openai".to_string()
}
fn default_batch_flush() -> f64 {
    5.0
}
fn default_batch_poll() -> f64 {
    30.0
}
fn default_batch_timeout() -> f64 {
    3600.0
}

/// Azure OpenAI deployment.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AzureConfig {
    #[serde(default)]
    pub proxy: Option<String>,
    #[serde(default)]
    pub cost_per_input_token: Option<f64>,
    #[serde(default)]
    pub cost_per_output_token: Option<f64>,

    /// Always `"azure"` — kept explicit so `deny_unknown_fields` tolerates
    /// the discriminator that Python/YAML writers include.
    #[serde(default = "default_azure_api_type")]
    pub api_type: String,

    pub api_key: String,
    pub api_version: String,
    pub azure_endpoint: String,
    pub model: String,
}

fn default_azure_api_type() -> String {
    "azure".into()
}

/// Google Vertex AI / Gemini (service-account auth).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GeminiConfig {
    #[serde(default)]
    pub proxy: Option<String>,
    #[serde(default)]
    pub cost_per_input_token: Option<f64>,
    #[serde(default)]
    pub cost_per_output_token: Option<f64>,

    #[serde(default = "default_gemini_api_type")]
    pub api_type: String,

    pub project_id: String,
    pub private_key_id: String,
    pub private_key: String,
    pub client_email: String,
    pub client_id: String,
    #[serde(default = "default_auth_uri")]
    pub auth_uri: String,
    #[serde(default = "default_token_uri")]
    pub token_uri: String,
    #[serde(default)]
    pub auth_provider_x509_cert_url: String,
    #[serde(default)]
    pub client_x509_cert_url: String,
    #[serde(default = "default_universe_domain")]
    pub universe_domain: String,
    pub location: String,
    pub model: String,
}

fn default_gemini_api_type() -> String {
    "gemini".into()
}

fn default_auth_uri() -> String {
    "https://accounts.google.com/o/oauth2/auth".into()
}
fn default_token_uri() -> String {
    "https://oauth2.googleapis.com/token".into()
}
fn default_universe_domain() -> String {
    "googleapis.com".into()
}

/// Anthropic Claude direct API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnthropicConfig {
    #[serde(default)]
    pub proxy: Option<String>,
    #[serde(default)]
    pub cost_per_input_token: Option<f64>,
    #[serde(default)]
    pub cost_per_output_token: Option<f64>,

    #[serde(default = "default_anthropic_api_type")]
    pub api_type: String,

    pub api_key: String,
    #[serde(default = "default_anthropic_url")]
    pub base_url: String,
    pub model: String,
    #[serde(default = "default_anthropic_version")]
    pub anthropic_version: String,
}

fn default_anthropic_api_type() -> String {
    "anthropic".into()
}

fn default_anthropic_url() -> String {
    "https://api.anthropic.com".into()
}
fn default_anthropic_version() -> String {
    "2023-06-01".into()
}

/// Discriminated union by `api_type`.
#[derive(Debug, Clone, Serialize)]
#[serde(untagged)]
pub enum LLMConfig {
    OpenAI(OpenAIConfig),
    Azure(AzureConfig),
    Gemini(GeminiConfig),
    Anthropic(AnthropicConfig),
}

impl LLMConfig {
    pub fn api_type(&self) -> &str {
        match self {
            LLMConfig::OpenAI(c) => c.api_type.as_str(),
            LLMConfig::Azure(_) => "azure",
            LLMConfig::Gemini(_) => "gemini",
            LLMConfig::Anthropic(_) => "anthropic",
        }
    }

    pub fn model(&self) -> &str {
        match self {
            LLMConfig::OpenAI(c) => &c.model,
            LLMConfig::Azure(c) => &c.model,
            LLMConfig::Gemini(c) => &c.model,
            LLMConfig::Anthropic(c) => &c.model,
        }
    }
}

impl<'de> Deserialize<'de> for LLMConfig {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let val = serde_json::Value::deserialize(deserializer)?;
        let api_type = val
            .get("api_type")
            .and_then(|v| v.as_str())
            .unwrap_or("openai")
            .to_string();
        match api_type.as_str() {
            "azure" => serde_json::from_value::<AzureConfig>(val)
                .map(LLMConfig::Azure)
                .map_err(serde::de::Error::custom),
            "gemini" => serde_json::from_value::<GeminiConfig>(val)
                .map(LLMConfig::Gemini)
                .map_err(serde::de::Error::custom),
            "anthropic" => serde_json::from_value::<AnthropicConfig>(val)
                .map(LLMConfig::Anthropic)
                .map_err(serde::de::Error::custom),
            _ => serde_json::from_value::<OpenAIConfig>(val)
                .map(LLMConfig::OpenAI)
                .map_err(serde::de::Error::custom),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn openai_config_roundtrip() {
        let src = r#"{"api_type": "openai", "api_key": "sk", "base_url": "https://api.openai.com/v1", "model": "gpt-4o"}"#;
        let cfg: LLMConfig = serde_json::from_str(src).unwrap();
        assert!(matches!(cfg, LLMConfig::OpenAI(_)));
        assert_eq!(cfg.model(), "gpt-4o");
        assert_eq!(cfg.api_type(), "openai");
    }

    #[test]
    fn vllm_is_openai_variant() {
        let src = r#"{"api_type": "vllm", "base_url": "http://x", "model": "Qwen"}"#;
        let cfg: LLMConfig = serde_json::from_str(src).unwrap();
        assert!(matches!(cfg, LLMConfig::OpenAI(_)));
        assert_eq!(cfg.api_type(), "vllm");
    }

    #[test]
    fn anthropic_config_parses_defaults() {
        let src = r#"{"api_type": "anthropic", "api_key": "sk-ant", "model": "claude-opus-4"}"#;
        let cfg: LLMConfig = serde_json::from_str(src).unwrap();
        if let LLMConfig::Anthropic(c) = cfg {
            assert_eq!(c.anthropic_version, "2023-06-01");
            assert_eq!(c.base_url, "https://api.anthropic.com");
        } else {
            panic!("expected Anthropic variant");
        }
    }
}
