//! LLM provider configuration — mirrors hush-providers LLMConfig hierarchy.
//!
//! Hierarchy:
//!   LLMConfig (base — common fields)
//!   ├── OpenAIConfig  (api_type: openai | vllm)
//!   ├── AzureConfig   (api_type: azure)
//!   └── GeminiConfig  (api_type: gemini)

/// LLM backend configuration — tagged enum dispatching by api_type.
pub enum LLMConfig {
    OpenAI(OpenAIConfig),
    Azure(AzureConfig),
    Gemini(GeminiConfig),
    Anthropic(AnthropicConfig),
}

impl LLMConfig {
    /// Get the api_type string.
    pub fn api_type(&self) -> &str {
        match self {
            LLMConfig::OpenAI(c) => &c.api_type,
            LLMConfig::Azure(_) => "azure",
            LLMConfig::Gemini(_) => "gemini",
            LLMConfig::Anthropic(_) => "anthropic",
        }
    }

    /// Get the model name (common across all variants).
    pub fn model(&self) -> Option<&str> {
        match self {
            LLMConfig::OpenAI(c) => Some(&c.model),
            LLMConfig::Azure(c) => Some(&c.model),
            LLMConfig::Gemini(c) => Some(&c.model),
            LLMConfig::Anthropic(c) => Some(&c.model),
        }
    }
}

/// Fields shared by all LLM configs.
pub struct LLMBaseFields {
    pub proxy: Option<String>,
    pub cost_per_input_token: Option<f64>,
    pub cost_per_output_token: Option<f64>,
}

/// OpenAI / vLLM configuration.
pub struct OpenAIConfig {
    pub base: LLMBaseFields,
    /// "openai" or "vllm"
    pub api_type: String,
    pub api_key: String,
    pub base_url: String,
    pub model: String,
    pub batch_size: usize,
    pub batch_flush_interval: f64,
    pub batch_poll_interval: f64,
    pub batch_timeout: f64,
}

/// Azure OpenAI configuration.
pub struct AzureConfig {
    pub base: LLMBaseFields,
    pub api_key: String,
    pub api_version: String,
    pub azure_endpoint: String,
    pub model: String,
}

/// Parse a single LLMConfig from JSON.
pub fn parse_llm_config_json(val: &serde_json::Value) -> Result<LLMConfig, String> {
    let api_type = val.get("api_type").and_then(|v| v.as_str()).unwrap_or("openai");
    let get_str = |key: &str| val.get(key).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let get_opt = |key: &str| val.get(key).and_then(|v| v.as_str()).map(String::from);
    let get_f64 = |key: &str, def: f64| val.get(key).and_then(|v| v.as_f64()).unwrap_or(def);

    let base = LLMBaseFields {
        proxy: get_opt("proxy"),
        cost_per_input_token: val.get("cost_per_input_token").and_then(|v| v.as_f64()),
        cost_per_output_token: val.get("cost_per_output_token").and_then(|v| v.as_f64()),
    };

    match api_type {
        "azure" => Ok(LLMConfig::Azure(AzureConfig {
            base,
            api_key: get_str("api_key"),
            api_version: get_str("api_version"),
            azure_endpoint: get_str("azure_endpoint"),
            model: get_str("model"),
        })),
        "gemini" => Ok(LLMConfig::Gemini(GeminiConfig {
            base,
            project_id: get_str("project_id"),
            private_key_id: get_str("private_key_id"),
            private_key: get_str("private_key"),
            client_email: get_str("client_email"),
            client_id: get_str("client_id"),
            auth_uri: get_str("auth_uri"),
            token_uri: get_str("token_uri"),
            auth_provider_x509_cert_url: get_str("auth_provider_x509_cert_url"),
            client_x509_cert_url: get_str("client_x509_cert_url"),
            universe_domain: get_str("universe_domain"),
            location: get_str("location"),
            model: get_str("model"),
        })),
        "anthropic" => Ok(LLMConfig::Anthropic(AnthropicConfig {
            base,
            api_key: get_str("api_key"),
            base_url: {
                let url = get_str("base_url");
                if url.is_empty() { "https://api.anthropic.com".to_string() } else { url }
            },
            model: get_str("model"),
            anthropic_version: {
                let ver = get_str("anthropic_version");
                if ver.is_empty() { "2023-06-01".to_string() } else { ver }
            },
        })),
        _ => Ok(LLMConfig::OpenAI(OpenAIConfig {
            base,
            api_type: api_type.to_string(),
            api_key: get_str("api_key"),
            base_url: get_str("base_url"),
            model: get_str("model"),
            batch_size: val.get("batch_size").and_then(|v| v.as_u64()).unwrap_or(0) as usize,
            batch_flush_interval: get_f64("batch_flush_interval", 5.0),
            batch_poll_interval: get_f64("batch_poll_interval", 30.0),
            batch_timeout: get_f64("batch_timeout", 3600.0),
        })),
    }
}

/// Google Gemini configuration (service account auth).
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

/// Anthropic Claude configuration.
pub struct AnthropicConfig {
    pub base: LLMBaseFields,
    pub api_key: String,
    pub base_url: String,
    pub model: String,
    pub anthropic_version: String,
}
