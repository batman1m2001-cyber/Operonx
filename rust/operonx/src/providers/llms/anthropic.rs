//! Anthropic Claude chat completion backend.
//!
//! Mirrors Python [`operon/providers/llms/anthropic.py`](../../../../../operon/providers/llms/anthropic.py).
//! Uses the `/v1/messages` endpoint + translates OpenAI-shaped messages into
//! Anthropic's `messages` + `system` split. Prompt caching uses
//! `cache_control: {type: "ephemeral"}` on eligible blocks (see
//! `anthropic_cache_min_tokens` in Python base).
//!
//! # Phase 5 scope
//! Struct + config attachment only. Full request round-trip (incl. cache
//! metrics extraction) lands in Phase 5b.

use async_trait::async_trait;
use futures::stream::BoxStream;

use super::base::{BaseLLM, ChatCompletion, ChatCompletionChunk, LlmOpts, Message};
use super::config::AnthropicConfig;
use crate::core::exceptions::OperonError;

pub struct AnthropicLlm {
    pub config: AnthropicConfig,
}

impl AnthropicLlm {
    pub fn new(config: AnthropicConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseLLM for AnthropicLlm {
    async fn generate(
        &self,
        _messages: Vec<Message>,
        _opts: &LlmOpts,
    ) -> Result<ChatCompletion, OperonError> {
        Err(OperonError::Provider(format!(
            "AnthropicLlm::generate not yet implemented (Phase 5b) — model={}",
            self.config.model
        )))
    }

    async fn stream(
        &self,
        _messages: Vec<Message>,
        _opts: &LlmOpts,
    ) -> Result<BoxStream<'static, Result<ChatCompletionChunk, OperonError>>, OperonError> {
        Err(OperonError::Provider(
            "AnthropicLlm::stream not yet implemented (Phase 5b)".into(),
        ))
    }
}
