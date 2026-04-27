//! Reranker provider configuration.
//!
//! Mirrors Python [`operonx/providers/rerankers/config.py`](../../../../../operonx/providers/rerankers/config.py).

use serde::{Deserialize, Serialize};

/// Supported reranker backends.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RerankingType {
    #[serde(rename = "cohere")]
    Cohere,
    #[serde(rename = "tei")]
    Tei,
    #[serde(rename = "vllm")]
    Vllm,
    #[serde(rename = "pinecone")]
    Pinecone,
    #[serde(rename = "hf")]
    HuggingFace,
    #[serde(rename = "onnx")]
    Onnx,
}

impl Default for RerankingType {
    fn default() -> Self {
        RerankingType::Vllm
    }
}

/// Flat reranker config — one struct covers every backend variant.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RerankingConfig {
    #[serde(default)]
    pub api_type: RerankingType,
    #[serde(default)]
    pub api_key: Option<String>,
    #[serde(default)]
    pub api_version: Option<String>,
    #[serde(default)]
    pub base_url: Option<String>,
    #[serde(default)]
    pub model: Option<String>,

    // ONNX-specific (mirror Python inherited OnnxInferenceConfig).
    #[serde(default)]
    pub model_path: Option<String>,
    #[serde(default)]
    pub tokenizer_path: Option<String>,
    #[serde(default)]
    pub max_length: Option<usize>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_pinecone() {
        let src = r#"{"api_type": "pinecone", "api_key": "pc", "model": "bge-reranker-v2-m3"}"#;
        let cfg: RerankingConfig = serde_json::from_str(src).unwrap();
        assert_eq!(cfg.api_type, RerankingType::Pinecone);
    }

    #[test]
    fn parses_cohere() {
        let src = r#"{"api_type": "cohere", "api_key": "co", "model": "rerank-v3"}"#;
        let cfg: RerankingConfig = serde_json::from_str(src).unwrap();
        assert_eq!(cfg.api_type, RerankingType::Cohere);
    }
}
