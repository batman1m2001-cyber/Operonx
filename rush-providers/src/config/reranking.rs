//! Reranking provider configuration — mirrors hush-providers RerankingConfig.
//!
//! Supports: Cohere, vLLM, Pinecone, ONNX.

/// Reranking backend configuration.
pub struct RerankingConfig {
    /// API type: "cohere", "tei", "vllm", "pinecone", "onnx"
    pub api_type: String,
    pub api_key: Option<String>,
    pub api_version: Option<String>,
    pub base_url: Option<String>,
    pub model: Option<String>,
}
