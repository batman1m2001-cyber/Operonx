//! Embedding provider configuration.
//!
//! Mirrors Python [`operonx/providers/embeddings/config.py`](../../../../../operonx/providers/embeddings/config.py).
//! Python uses a flat `EmbeddingConfig` class with an `api_type` enum — we
//! match that shape exactly here.

use serde::{Deserialize, Serialize};

/// Supported embedding backends.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum EmbeddingType {
    #[serde(rename = "openai")]
    OpenAI,
    #[serde(rename = "azure")]
    Azure,
    #[serde(rename = "gemini")]
    Gemini,
    #[serde(rename = "tei")]
    Tei,
    #[serde(rename = "vllm")]
    Vllm,
    #[serde(rename = "hf")]
    HuggingFace,
    #[serde(rename = "onnx")]
    Onnx,
}

impl Default for EmbeddingType {
    fn default() -> Self {
        EmbeddingType::Vllm
    }
}

/// Flat embedding config — one struct covers every backend variant.
/// The factory branches on `api_type`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EmbeddingConfig {
    #[serde(default)]
    pub api_type: EmbeddingType,
    #[serde(default)]
    pub api_key: Option<String>,
    #[serde(default)]
    pub base_url: Option<String>,
    #[serde(default)]
    pub api_version: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub embed_batch_size: Option<usize>,
    #[serde(default)]
    pub dimensions: Option<usize>,

    // ONNX-specific fields (mirrors Python `OnnxInferenceConfig` inherited
    // fields when `api_type=onnx`).
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
    fn parses_openai_embedding() {
        let src = r#"{"api_type": "openai", "api_key": "sk", "model": "text-embedding-3-small", "dimensions": 1536}"#;
        let cfg: EmbeddingConfig = serde_json::from_str(src).unwrap();
        assert_eq!(cfg.api_type, EmbeddingType::OpenAI);
        assert_eq!(cfg.dimensions, Some(1536));
    }

    #[test]
    fn parses_onnx_embedding() {
        let src =
            r#"{"api_type": "onnx", "model_path": "/tmp/m.onnx", "tokenizer_path": "/tmp/t"}"#;
        let cfg: EmbeddingConfig = serde_json::from_str(src).unwrap();
        assert_eq!(cfg.api_type, EmbeddingType::Onnx);
        assert_eq!(cfg.model_path.as_deref(), Some("/tmp/m.onnx"));
    }
}
