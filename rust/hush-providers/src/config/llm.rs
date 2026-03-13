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
}

impl LLMConfig {
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
