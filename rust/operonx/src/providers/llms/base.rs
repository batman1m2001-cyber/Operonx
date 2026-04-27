//! [`BaseLLM`] trait + chat types.
//!
//! Mirrors Python [`operonx/providers/llms/base.py`](../../../../../operonx/providers/llms/base.py#L117).
//! Per plan §5b.2 the trait exposes `generate` / `stream` / `warmup` /
//! `generate_batch` — no `agenerate` / `astream`.
//!
//! Chat types use serde-derived structs here rather than the `openai` crate
//! types: keeps the Rust runtime independent of the Python SDK's type system
//! and makes the serialized wire shape explicit.

use std::collections::HashMap;
use std::time::Duration;

use async_trait::async_trait;
use futures::stream::BoxStream;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::exceptions::OperonError;

/// One chat message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: String,
    /// Content may be a string, a list of content parts, or null.
    #[serde(default)]
    pub content: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// Pass-through for provider-specific fields (`tool_calls`, etc.).
    #[serde(flatten)]
    pub extras: HashMap<String, Value>,
}

/// Generation options — mirrors Python's `generate()` / `stream()` kwargs.
///
/// `extras` carries any provider-specific knob not typed here (e.g., Anthropic
/// `thinking_budget_tokens`, Gemini `safety_settings`).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct LlmOpts {
    #[serde(default)]
    pub temperature: Option<f32>,
    #[serde(default)]
    pub top_p: Option<f32>,
    #[serde(default)]
    pub n: Option<u32>,
    #[serde(default)]
    pub stop: Option<Vec<String>>,
    #[serde(default)]
    pub max_tokens: Option<u32>,
    #[serde(default)]
    pub frequency_penalty: Option<f32>,
    #[serde(default)]
    pub presence_penalty: Option<f32>,
    #[serde(default)]
    pub response_format: Option<Value>,
    #[serde(default)]
    pub tools: Option<Value>,
    #[serde(default)]
    pub extras: HashMap<String, Value>,
}

/// Non-streaming completion. Serde-identical to the OpenAI Chat Completion
/// wire shape so telemetry and downstream ops can consume it unchanged.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatCompletion {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub object: String,
    #[serde(default)]
    pub created: i64,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub choices: Vec<CompletionChoice>,
    #[serde(default)]
    pub usage: Option<Usage>,
    #[serde(flatten)]
    pub extras: HashMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompletionChoice {
    #[serde(default)]
    pub index: u32,
    #[serde(default)]
    pub message: Option<Message>,
    #[serde(default)]
    pub finish_reason: Option<String>,
    #[serde(flatten)]
    pub extras: HashMap<String, Value>,
}

/// Streaming delta.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatCompletionChunk {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub object: String,
    #[serde(default)]
    pub created: i64,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub choices: Vec<ChunkChoice>,
    #[serde(flatten)]
    pub extras: HashMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChunkChoice {
    #[serde(default)]
    pub index: u32,
    #[serde(default)]
    pub delta: Value,
    #[serde(default)]
    pub finish_reason: Option<String>,
}

/// Token usage stats.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Usage {
    #[serde(default)]
    pub prompt_tokens: u32,
    #[serde(default)]
    pub completion_tokens: u32,
    #[serde(default)]
    pub total_tokens: u32,
    #[serde(default)]
    pub prompt_tokens_details: Option<Value>,
    #[serde(flatten)]
    pub extras: HashMap<String, Value>,
}

/// One request in a batched generation call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchReq {
    pub messages: Vec<Message>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub custom_id: Option<String>,
    #[serde(default)]
    pub extras: HashMap<String, Value>,
}

/// The trait every LLM backend (OpenAI, Azure, Gemini, Anthropic, vLLM …)
/// implements.
///
/// Matches Python's [`BaseLLM`](../../../../../operonx/providers/llms/base.py#L117)
/// — four entry points + shared image-handling helpers that live in
/// free functions rather than on the trait (see [`super::image`] in Phase 5b).
#[async_trait]
pub trait BaseLLM: Send + Sync {
    /// Non-streaming chat completion.
    async fn generate(
        &self,
        messages: Vec<Message>,
        opts: &LlmOpts,
    ) -> Result<ChatCompletion, OperonError>;

    /// Streaming chat completion.
    async fn stream(
        &self,
        messages: Vec<Message>,
        opts: &LlmOpts,
    ) -> Result<BoxStream<'static, Result<ChatCompletionChunk, OperonError>>, OperonError>;

    /// Warm the provider connection (and optionally seed prompt cache). The
    /// default implementation performs a cheap `generate()` with
    /// `max_tokens=1`.
    async fn warmup(&self, system_prompt: Option<String>) -> Result<(), OperonError> {
        let mut messages = Vec::new();
        if let Some(sys) = system_prompt {
            messages.push(Message {
                role: "system".into(),
                content: Value::from(sys),
                name: None,
                tool_call_id: None,
                extras: HashMap::new(),
            });
        }
        messages.push(Message {
            role: "user".into(),
            content: Value::from("warmup"),
            name: None,
            tool_call_id: None,
            extras: HashMap::new(),
        });
        let opts = LlmOpts {
            max_tokens: Some(1),
            ..LlmOpts::default()
        };
        self.generate(messages, &opts).await.map(|_| ())
    }

    /// Batched completion (OpenAI Batch API or its provider-specific
    /// equivalent). Default impl errors — override on providers that support
    /// it.
    async fn generate_batch(
        &self,
        _reqs: Vec<BatchReq>,
        _poll_interval: Duration,
        _timeout: Duration,
        _opts: &LlmOpts,
    ) -> Result<Vec<ChatCompletion>, OperonError> {
        Err(OperonError::Provider(
            "batch generation not supported by this LLM provider".into(),
        ))
    }
}
