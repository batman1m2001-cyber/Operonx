//! LLM providers — per-provider implementations mirroring hush-providers/llms/.
//!
//! `base.rs` defines the `LlmProvider` trait (mirrors Python's BaseLLM).
//!
//! Dispatches by LLMConfig variant to the appropriate provider:
//! - OpenAI / vLLM → openai.rs
//! - Azure → azure.rs
//! - Gemini (Vertex AI) → gemini.rs
//!
//! Shared request/response types live in types.rs.
//! Image encoding for multimodal support lives in image.rs.

pub mod base;
pub mod config;
pub mod azure;
pub mod gemini;
pub mod image;
pub mod openai;
pub mod types;

use std::sync::mpsc::Sender;

use serde_json::Value;

use crate::config::llm::LLMConfig;
use crate::http::ProviderResult;

/// Dispatch chat completion to the appropriate LLM provider.
///
/// Automatically resolves local image paths in messages to base64 data URLs.
pub async fn chat_completion(
    config: &LLMConfig,
    inputs: &Value,
    access_token: Option<&str>,
) -> ProviderResult<Value> {
    // Resolve local image paths before sending to provider
    let mut inputs = inputs.clone();
    if let Some(messages) = inputs.get_mut("messages") {
        image::resolve_image_paths(messages);
    }

    match config {
        LLMConfig::OpenAI(c) => openai::chat_completion(c, &inputs, access_token).await,
        LLMConfig::Azure(c) => azure::chat_completion(c, &inputs).await,
        LLMConfig::Gemini(c) => gemini::chat_completion(c, &inputs, access_token).await,
    }
}

/// Dispatch streaming chat completion to the appropriate LLM provider.
pub async fn chat_completion_stream(
    config: &LLMConfig,
    inputs: &Value,
    access_token: Option<&str>,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let mut inputs = inputs.clone();
    if let Some(messages) = inputs.get_mut("messages") {
        image::resolve_image_paths(messages);
    }

    match config {
        LLMConfig::OpenAI(c) => {
            openai::chat_completion_stream(c, &inputs, access_token, chunk_tx).await
        }
        LLMConfig::Azure(c) => azure::chat_completion_stream(c, &inputs, chunk_tx).await,
        LLMConfig::Gemini(c) => {
            gemini::chat_completion_stream(c, &inputs, access_token, chunk_tx).await
        }
    }
}
