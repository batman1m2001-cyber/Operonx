//! `create_llm` — dispatch an [`LLMConfig`] to the matching backend.
//!
//! Mirrors Python [`operonx/providers/llms/factory.py`](../../../../../operonx/providers/llms/factory.py).

use std::sync::Arc;

use super::anthropic::AnthropicLlm;
use super::azure::AzureLlm;
use super::base::BaseLLM;
use super::config::LLMConfig;
use super::gemini::GeminiLlm;
use super::openai::OpenAILlm;
use crate::core::exceptions::OperonError;

/// Construct the backend matching `config`'s variant.
pub fn create_llm(config: LLMConfig) -> Result<Arc<dyn BaseLLM>, OperonError> {
    let llm: Arc<dyn BaseLLM> = match config {
        LLMConfig::OpenAI(c) => Arc::new(OpenAILlm::new(c)),
        LLMConfig::Azure(c) => Arc::new(AzureLlm::new(c)),
        LLMConfig::Gemini(c) => Arc::new(GeminiLlm::new(c)),
        LLMConfig::Anthropic(c) => Arc::new(AnthropicLlm::new(c)),
    };
    Ok(llm)
}
