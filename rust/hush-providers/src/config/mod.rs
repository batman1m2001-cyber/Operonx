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
/// The `ProviderConfig` is stored on `BaseOpConfig` and used by native HTTP ops.
pub enum ProviderConfig {
    LLM(LLMProviderConfig),
    Embedding(embedding::EmbeddingConfig),
    Reranking(reranking::RerankingConfig),
    Onnx(onnx::OnnxInferenceConfig),
}

/// Parse a ProviderConfig from opaque JSON (called by provider ops at execution time).
pub fn parse_provider_config(op_type: &str, val: &serde_json::Value) -> Result<ProviderConfig, String> {
    match op_type {
        "llm" => parse_llm_provider_config(val).map(ProviderConfig::LLM),
        "embedding" => parse_embedding_config(val).map(ProviderConfig::Embedding),
        "rerank" => parse_reranking_config(val).map(ProviderConfig::Reranking),
        "onnx" => parse_onnx_config(val).map(ProviderConfig::Onnx),
        _ => Err(format!("Unknown provider op type: '{}'", op_type)),
    }
}

/// Parse LLM provider config from JSON.
pub fn parse_llm_provider_config(val: &serde_json::Value) -> Result<LLMProviderConfig, String> {
    let mut configs = Vec::new();

    // Parse resource_configs array
    if let Some(arr) = val.get("resource_configs").and_then(|v| v.as_array()) {
        for cfg_val in arr {
            if let Ok(cfg) = llm::parse_llm_config_json(cfg_val) {
                configs.push(cfg);
            }
        }
    }
    // Fallback: try single config from top-level fields
    if configs.is_empty() {
        if let Ok(cfg) = llm::parse_llm_config_json(val) {
            configs.push(cfg);
        }
    }
    if configs.is_empty() {
        return Err("No valid LLM configs found".to_string());
    }

    let resources = val.get("resources")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default();
    let ratios = val.get("ratios")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_f64()).collect())
        .unwrap_or_default();
    let mut fallback_configs = Vec::new();
    if let Some(arr) = val.get("fallback_configs").and_then(|v| v.as_array()) {
        for cfg_val in arr {
            if let Ok(cfg) = llm::parse_llm_config_json(cfg_val) {
                fallback_configs.push(cfg);
            }
        }
    }
    let fallback = val.get("fallback")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default();
    let batch_mode = val.get("batch_mode").and_then(|v| v.as_bool()).unwrap_or(false);

    Ok(LLMProviderConfig { configs, resources, ratios, fallback_configs, fallback, batch_mode })
}

/// Parse embedding config from JSON.
pub fn parse_embedding_config(val: &serde_json::Value) -> Result<embedding::EmbeddingConfig, String> {
    let api_type = val.get("api_type").and_then(|v| v.as_str()).unwrap_or("openai").to_string();
    let model = val.get("model").and_then(|v| v.as_str()).map(String::from);
    let api_key = val.get("api_key").and_then(|v| v.as_str()).map(String::from);
    let base_url = val.get("base_url").and_then(|v| v.as_str()).map(String::from);
    let dimensions = val.get("dimensions").and_then(|v| v.as_u64()).map(|v| v as usize);
    let embed_batch_size = val.get("embed_batch_size").and_then(|v| v.as_u64()).map(|v| v as usize);
    Ok(embedding::EmbeddingConfig { api_type, model, api_key, base_url, dimensions, embed_batch_size })
}

/// Parse reranking config from JSON.
pub fn parse_reranking_config(val: &serde_json::Value) -> Result<reranking::RerankingConfig, String> {
    let api_type = val.get("api_type").and_then(|v| v.as_str()).unwrap_or("vllm").to_string();
    let model = val.get("model").and_then(|v| v.as_str()).map(String::from);
    let api_key = val.get("api_key").and_then(|v| v.as_str()).map(String::from);
    let api_version = val.get("api_version").and_then(|v| v.as_str()).map(String::from);
    let base_url = val.get("base_url").and_then(|v| v.as_str()).map(String::from);
    Ok(reranking::RerankingConfig { api_type, model, api_key, api_version, base_url })
}

/// Parse ONNX inference config from JSON.
pub fn parse_onnx_config(val: &serde_json::Value) -> Result<onnx::OnnxInferenceConfig, String> {
    let model_path = val.get("model_path").and_then(|v| v.as_str())
        .ok_or_else(|| "Missing model_path".to_string())?.to_string();
    let input_type = match val.get("input_type").and_then(|v| v.as_str()).unwrap_or("mlp") {
        "attention" => onnx::OnnxInputType::Attention,
        _ => onnx::OnnxInputType::Mlp,
    };
    let pool_size = val.get("pool_size").and_then(|v| v.as_u64()).unwrap_or(1) as usize;
    let intra_threads = val.get("intra_threads").and_then(|v| v.as_u64()).unwrap_or(1) as usize;
    Ok(onnx::OnnxInferenceConfig { model_path, input_type, pool_size, intra_threads })
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
