//! Embedding provider configuration — mirrors hush-providers EmbeddingConfig.
//!
//! Supports: OpenAI, Azure, vLLM, ONNX.

/// Embedding backend configuration.
pub struct EmbeddingConfig {
    /// API type: "openai", "azure", "gemini", "tei", "vllm", "onnx"
    pub api_type: String,
    pub api_key: Option<String>,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub embed_batch_size: Option<usize>,
    pub dimensions: Option<usize>,
}
