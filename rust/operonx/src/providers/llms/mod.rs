//! LLM providers.
//!
//! Mirrors Python `operon/providers/llms/`.

pub mod anthropic;
pub mod azure;
pub mod base;
pub mod batch_coordinator;
pub mod config;
pub mod factory;
pub mod gemini;
pub mod openai;
pub mod response;

pub use base::{
    BaseLLM, BatchReq, ChatCompletion, ChatCompletionChunk, ChunkChoice, CompletionChoice, LlmOpts,
    Message, Usage,
};
pub use config::{
    AnthropicConfig, AzureConfig, GeminiConfig, LLMBaseFields, LLMConfig, OpenAIConfig,
};
pub use factory::create_llm;
pub use response::LLMGenerator;
