//! Provider op implementations — mirrors hush-providers ops/.
//!
//! Each op module implements the high-level logic:
//! - LLM: load balancing, fallback chains, batch mode, keycloak auth
//! - Chain: prompt template formatting + LLM call
//! - Embedding: dispatch by api_type
//! - Reranking: dispatch by api_type, document handling
//! - Prompt: template formatting
//!
//! All ops are async and GIL-free — they operate on serde_json::Value
//! and are called via hush-icore's tokio integration.

pub mod chain;
pub mod embedding;
pub mod llm;
pub mod parser;
pub mod prompt;
pub mod rerank;

use std::sync::mpsc::Sender;

use serde_json::Value;

use crate::config::ProviderConfig;
use crate::http::{ProviderError, ProviderResult};

// =============================================================================
// Config extraction helpers
// =============================================================================

/// Extract LLM config from a ProviderConfig, or return a typed error.
fn expect_llm_config<'a>(config: &'a ProviderConfig, op_label: &str) -> ProviderResult<&'a crate::config::LLMProviderConfig> {
    match config {
        ProviderConfig::LLM(c) => Ok(c),
        _ => Err(ProviderError {
            message: format!("{} requires LLM provider config", op_label),
            status_code: None,
            error_code: None,
        }),
    }
}

fn expect_embedding_config(config: &ProviderConfig) -> ProviderResult<&crate::config::embedding::EmbeddingConfig> {
    match config {
        ProviderConfig::Embedding(c) => Ok(c),
        _ => Err(ProviderError {
            message: "Embedding op requires Embedding provider config".to_string(),
            status_code: None,
            error_code: None,
        }),
    }
}

fn expect_reranking_config(config: &ProviderConfig) -> ProviderResult<&crate::config::reranking::RerankingConfig> {
    match config {
        ProviderConfig::Reranking(c) => Ok(c),
        _ => Err(ProviderError {
            message: "Rerank op requires Reranking provider config".to_string(),
            status_code: None,
            error_code: None,
        }),
    }
}

// =============================================================================
// Dispatch
// =============================================================================

/// Execute a provider op by op_type.
pub async fn execute(
    op_type: &str,
    inputs: Value,
    config: &ProviderConfig,
) -> ProviderResult<Value> {
    match op_type {
        "llm" => llm::execute(inputs, expect_llm_config(config, "LLM op")?).await,
        "embedding" => embedding::execute(inputs, expect_embedding_config(config)?).await,
        "rerank" => rerank::execute(inputs, expect_reranking_config(config)?).await,
        "chain" => chain::execute(inputs, expect_llm_config(config, "Chain op")?).await,
        _ => Err(ProviderError {
            message: format!("Unknown provider op type: '{}'", op_type),
            status_code: None,
            error_code: None,
        }),
    }
}

/// Execute a provider op in streaming mode.
pub async fn execute_streaming(
    op_type: &str,
    inputs: Value,
    config: &ProviderConfig,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    match op_type {
        "llm" => {
            llm::execute_streaming(inputs, expect_llm_config(config, "LLM streaming op")?, chunk_tx).await
        }
        "chain" => {
            chain::execute_streaming(inputs, expect_llm_config(config, "Chain streaming op")?, chunk_tx).await
        }
        _ => Err(ProviderError {
            message: format!("Streaming not supported for op type: '{}'", op_type),
            status_code: None,
            error_code: None,
        }),
    }
}

// =============================================================================
// Native transform ops (GIL-free, no provider config)
// =============================================================================

/// Execute a native transform op (synchronous, no provider config needed).
///
/// These are pure CPU ops (prompt formatting, parsing) that have Rust
/// implementations and don't require any HTTP/API calls.
pub fn execute_transform(op_type: &str, inputs: Value) -> ProviderResult<Value> {
    match op_type {
        "prompt" => prompt::execute(inputs),
        "parser" => parser::execute(inputs),
        _ => Err(ProviderError {
            message: format!("Unknown transform op type: '{}'", op_type),
            status_code: None,
            error_code: None,
        }),
    }
}

/// Check if an op_type is a native transform op (pure CPU, no provider config).
pub fn is_native_transform_op(op_type: &str) -> bool {
    matches!(op_type, "prompt" | "parser")
}

/// Check if an api_type is a provider that we handle natively in Rust.
pub fn is_native_provider_op(api_type: &str) -> bool {
    matches!(
        api_type,
        "openai" | "vllm" | "azure" | "gemini" | "pinecone" | "cohere" | "hf" | "onnx"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_native_llm_providers() {
        assert!(is_native_provider_op("openai"));
        assert!(is_native_provider_op("vllm"));
        assert!(is_native_provider_op("azure"));
        assert!(is_native_provider_op("gemini"));
    }

    #[test]
    fn test_native_embedding_providers() {
        assert!(is_native_provider_op("openai"));
        assert!(is_native_provider_op("vllm"));
        assert!(is_native_provider_op("azure"));
        assert!(is_native_provider_op("hf"));
        assert!(is_native_provider_op("onnx"));
    }

    #[test]
    fn test_native_reranker_providers() {
        assert!(is_native_provider_op("cohere"));
        assert!(is_native_provider_op("pinecone"));
        assert!(is_native_provider_op("vllm"));
        assert!(is_native_provider_op("hf"));
        assert!(is_native_provider_op("onnx"));
    }

    #[test]
    fn test_non_native_providers() {
        assert!(!is_native_provider_op("tei"));
        assert!(!is_native_provider_op("custom"));
        assert!(!is_native_provider_op("unknown"));
        assert!(!is_native_provider_op(""));
        assert!(!is_native_provider_op("OPENAI"));
    }

    #[test]
    fn test_is_native_transform_op() {
        assert!(is_native_transform_op("prompt"));
        assert!(is_native_transform_op("parser"));
        assert!(!is_native_transform_op("llm"));
        assert!(!is_native_transform_op("embedding"));
        assert!(!is_native_transform_op("rerank"));
        assert!(!is_native_transform_op(""));
    }
}
