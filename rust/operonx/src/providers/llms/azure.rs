//! Azure OpenAI chat completion backend.
//!
//! Mirrors Python [`operon/providers/llms/azure.py`](../../../../../operon/providers/llms/azure.py).
//! Same wire shape as OpenAI but auth goes through `api-key` header +
//! the URL embeds `deployment` + `api-version` query string.
//!
//! # Phase 5 scope
//! Struct + config attachment only; the concrete HTTP round-trip lands in
//! Phase 5b alongside Gemini / Anthropic. Trait methods error with a
//! clear "not yet implemented" message until then.

use async_trait::async_trait;
use futures::stream::BoxStream;

use super::base::{BaseLLM, ChatCompletion, ChatCompletionChunk, LlmOpts, Message};
use super::config::AzureConfig;
use crate::core::exceptions::OperonError;

pub struct AzureLlm {
    pub config: AzureConfig,
}

impl AzureLlm {
    pub fn new(config: AzureConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseLLM for AzureLlm {
    async fn generate(
        &self,
        _messages: Vec<Message>,
        _opts: &LlmOpts,
    ) -> Result<ChatCompletion, OperonError> {
        Err(OperonError::Provider(format!(
            "AzureLlm::generate not yet implemented (Phase 5b) — model={}, endpoint={}",
            self.config.model, self.config.azure_endpoint
        )))
    }

    async fn stream(
        &self,
        _messages: Vec<Message>,
        _opts: &LlmOpts,
    ) -> Result<BoxStream<'static, Result<ChatCompletionChunk, OperonError>>, OperonError> {
        Err(OperonError::Provider(
            "AzureLlm::stream not yet implemented (Phase 5b)".into(),
        ))
    }
}
