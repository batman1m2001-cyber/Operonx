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
//! and are called via rush-core's tokio integration.

pub mod chain;
pub mod embedding;
pub mod llm;
pub mod prompt;
pub mod rerank;

use std::sync::mpsc::Sender;

use serde_json::Value;

use crate::config::ProviderConfig;
use crate::http::{ProviderError, ProviderResult};

/// Execute a provider op by op_type.
///
/// This is the main entry point called by rush-core.
/// Dispatches to the appropriate op module based on op_type.
///
/// Args:
///   op_type: "llm", "embedding", "rerank", "prompt"
///   inputs: Resolved input dict as JSON
///   config: Provider config (parsed from serialized op dict)
///
/// Returns:
///   Output dict as JSON (to be converted back to PyDict)
pub async fn execute(
    op_type: &str,
    inputs: Value,
    config: &ProviderConfig,
) -> ProviderResult<Value> {
    match op_type {
        "llm" => {
            let llm_config = match config {
                ProviderConfig::LLM(c) => c,
                _ => {
                    return Err(ProviderError {
                        message: "LLM op requires LLM provider config".to_string(),
                        status_code: None,
                        error_code: None,
                    })
                }
            };
            llm::execute(inputs, llm_config).await
        }
        "embedding" => {
            let emb_config = match config {
                ProviderConfig::Embedding(c) => c,
                _ => {
                    return Err(ProviderError {
                        message: "Embedding op requires Embedding provider config".to_string(),
                        status_code: None,
                        error_code: None,
                    })
                }
            };
            embedding::execute(inputs, emb_config).await
        }
        "rerank" => {
            let rerank_config = match config {
                ProviderConfig::Reranking(c) => c,
                _ => {
                    return Err(ProviderError {
                        message: "Rerank op requires Reranking provider config".to_string(),
                        status_code: None,
                        error_code: None,
                    })
                }
            };
            rerank::execute(inputs, rerank_config).await
        }
        "chain" => {
            let llm_config = match config {
                ProviderConfig::LLM(c) => c,
                _ => {
                    return Err(ProviderError {
                        message: "Chain op requires LLM provider config".to_string(),
                        status_code: None,
                        error_code: None,
                    })
                }
            };
            chain::execute(inputs, llm_config).await
        }
        _ => Err(ProviderError {
            message: format!("Unknown provider op type: '{}'", op_type),
            status_code: None,
            error_code: None,
        }),
    }
}

/// Execute a provider op in streaming mode.
///
/// Only supported for LLM ops. Sends each SSE chunk through `chunk_tx`.
/// Returns the accumulated final response.
pub async fn execute_streaming(
    op_type: &str,
    inputs: Value,
    config: &ProviderConfig,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    match op_type {
        "llm" => {
            let llm_config = match config {
                ProviderConfig::LLM(c) => c,
                _ => {
                    return Err(ProviderError {
                        message: "LLM streaming op requires LLM provider config".to_string(),
                        status_code: None,
                        error_code: None,
                    })
                }
            };
            llm::execute_streaming(inputs, llm_config, chunk_tx).await
        }
        "chain" => {
            let llm_config = match config {
                ProviderConfig::LLM(c) => c,
                _ => {
                    return Err(ProviderError {
                        message: "Chain streaming op requires LLM provider config".to_string(),
                        status_code: None,
                        error_code: None,
                    })
                }
            };
            chain::execute_streaming(inputs, llm_config, chunk_tx).await
        }
        _ => Err(ProviderError {
            message: format!("Streaming not supported for op type: '{}'", op_type),
            status_code: None,
            error_code: None,
        }),
    }
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

    // =========================================================================
    // is_native_provider_op tests
    // =========================================================================

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
        assert!(!is_native_provider_op("tei")); // Removed
        assert!(!is_native_provider_op("custom"));
        assert!(!is_native_provider_op("unknown"));
        assert!(!is_native_provider_op(""));
        assert!(!is_native_provider_op("OPENAI")); // Case-sensitive
    }
}
