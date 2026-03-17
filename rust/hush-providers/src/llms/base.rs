//! LlmProvider trait — mirrors Python's `llms/base.py` (BaseLLM).
//!
//! All LLM backends (OpenAI, Azure, Gemini, vLLM) implement this trait.

use serde_json::Value;

use crate::http::ProviderResult;

/// Base trait for LLM providers.
///
/// Mirrors Python's BaseLLM with `generate()` and `stream()` methods.
pub trait LlmProvider: Send + Sync {
    /// Generate a chat completion (non-streaming).
    fn generate(
        &self,
        messages: &[Value],
        params: &Value,
    ) -> impl std::future::Future<Output = ProviderResult<Value>> + Send;

    /// Generate a streaming chat completion.
    fn stream(
        &self,
        messages: &[Value],
        params: &Value,
        chunk_tx: std::sync::mpsc::Sender<Value>,
    ) -> impl std::future::Future<Output = ProviderResult<Value>> + Send;
}
