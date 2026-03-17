//! Provider configuration structs — parsed from JSON.
//!
//! Each provider type has its own config module:
//! - llm: LLMConfig (OpenAI, Azure, vLLM, Gemini)
//! - embedding: EmbeddingConfig (OpenAI, Azure, vLLM, ONNX)
//! - reranking: RerankingConfig (Cohere, vLLM, Pinecone, ONNX)

pub mod embedding;
pub mod llm;
pub mod onnx;
pub mod reranking;

/// Provider-specific configuration, parsed from the serialized op dict.
///
/// Each variant wraps the corresponding provider config struct.
/// The `ProviderConfig` is stored on `OpConfig` and used by native HTTP ops.
pub enum ProviderConfig {
    LLM(LLMProviderConfig),
    Embedding(embedding::EmbeddingConfig),
    Reranking(reranking::RerankingConfig),
    Onnx(onnx::OnnxInferenceConfig),
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
