//! Google Vertex AI / Gemini chat completion backend.
//!
//! Mirrors Python [`operonx/providers/llms/gemini.py`](../../../../../operonx/providers/llms/gemini.py).
//! Uses a service-account JWT to mint a short-lived access token, then hits
//! the OpenAI-compatible endpoint at
//! `https://{region}-aiplatform.googleapis.com/v1/projects/…/chat/completions`.
//!
//! # Phase 5 scope
//! Struct + config attachment. Full JWT→token flow + request round-trip
//! lands in Phase 5b — blocks on the `jsonwebtoken` crate integration.

use async_trait::async_trait;
use futures::stream::BoxStream;

use super::base::{BaseLLM, ChatCompletion, ChatCompletionChunk, LlmOpts, Message};
use super::config::GeminiConfig;
use crate::core::exceptions::OperonError;

pub struct GeminiLlm {
    pub config: GeminiConfig,
}

impl GeminiLlm {
    pub fn new(config: GeminiConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseLLM for GeminiLlm {
    async fn generate(
        &self,
        _messages: Vec<Message>,
        _opts: &LlmOpts,
    ) -> Result<ChatCompletion, OperonError> {
        Err(OperonError::Provider(format!(
            "GeminiLlm::generate not yet implemented (Phase 5b) — project={}, model={}",
            self.config.project_id, self.config.model
        )))
    }

    async fn stream(
        &self,
        _messages: Vec<Message>,
        _opts: &LlmOpts,
    ) -> Result<BoxStream<'static, Result<ChatCompletionChunk, OperonError>>, OperonError> {
        Err(OperonError::Provider(
            "GeminiLlm::stream not yet implemented (Phase 5b)".into(),
        ))
    }
}
