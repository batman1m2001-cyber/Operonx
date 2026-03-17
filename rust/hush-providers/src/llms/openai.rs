//! OpenAI / vLLM LLM provider — chat completions + streaming.
//!
//! Mirrors Python's `llms/openai.py`.
//! Also contains the shared SSE stream parser used by Azure and Gemini.

use std::sync::mpsc::Sender;

use serde_json::{json, Value};

use crate::config::llm::OpenAIConfig;
use crate::http::{get_client, ProviderError, ProviderResult};
use crate::llms::types::{
    build_chat_request, build_streaming_request, format_completion_response,
    ChatCompletionResponse,
};

/// OpenAI / vLLM provider struct — mirrors Python's OpenAI(BaseLLM).
pub struct OpenAIProvider<'a> {
    pub config: &'a OpenAIConfig,
    pub access_token: Option<String>,
}

impl<'a> OpenAIProvider<'a> {
    pub fn new(config: &'a OpenAIConfig, access_token: Option<String>) -> Self {
        OpenAIProvider { config, access_token }
    }
}

impl super::base::LlmProvider for OpenAIProvider<'_> {
    fn generate(
        &self,
        messages: &[Value],
        params: &Value,
    ) -> impl std::future::Future<Output = ProviderResult<Value>> + Send {
        let config = self.config;
        let token = self.access_token.as_deref();
        let inputs = json!({"messages": messages, "params": params});
        async move { chat_completion(config, &inputs, token).await }
    }

    fn stream(
        &self,
        messages: &[Value],
        params: &Value,
        chunk_tx: Sender<Value>,
    ) -> impl std::future::Future<Output = ProviderResult<Value>> + Send {
        let config = self.config;
        let token = self.access_token.as_deref();
        let inputs = json!({"messages": messages, "params": params});
        async move { chat_completion_stream(config, &inputs, token, chunk_tx).await }
    }
}

// =============================================================================
// Non-streaming
// =============================================================================

/// OpenAI / vLLM chat completion.
pub async fn chat_completion(
    config: &OpenAIConfig,
    inputs: &Value,
    access_token: Option<&str>,
) -> ProviderResult<Value> {
    let client = get_client();
    let url = format!(
        "{}/chat/completions",
        config.base_url.trim_end_matches('/')
    );

    let api_key = access_token.unwrap_or(&config.api_key);
    let request = build_chat_request(&config.model, inputs);

    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&request)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("OpenAI API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let body: ChatCompletionResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    Ok(format_completion_response(body))
}

// =============================================================================
// Streaming
// =============================================================================

/// OpenAI / vLLM streaming chat completion.
pub async fn chat_completion_stream(
    config: &OpenAIConfig,
    inputs: &Value,
    access_token: Option<&str>,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let client = get_client();
    let url = format!(
        "{}/chat/completions",
        config.base_url.trim_end_matches('/')
    );

    let api_key = access_token.unwrap_or(&config.api_key);
    let request = build_streaming_request(&config.model, inputs);

    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&request)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("OpenAI streaming API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    process_sse_stream(resp, &config.model, chunk_tx).await
}

// =============================================================================
// Shared SSE stream parser (used by OpenAI, Azure, Gemini)
// =============================================================================

/// Process an SSE (Server-Sent Events) stream from an OpenAI-compatible API.
///
/// Parses SSE `data: {...}` lines, sends each chunk to the channel,
/// and accumulates content, thinking_content, tool_calls, and usage.
/// Returns the accumulated final response.
pub async fn process_sse_stream(
    resp: reqwest::Response,
    model: &str,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let mut content = String::new();
    let mut thinking_content = String::new();
    let mut finish_reason = "stop".to_string();
    let mut tokens_used = json!({});
    let mut tool_calls: Vec<Value> = Vec::new();
    let mut refusal = String::new();

    let mut buffer = String::new();
    let mut stream_body = resp;

    while let Some(bytes) = stream_body.chunk().await.map_err(|e| ProviderError {
        message: format!("Stream read error: {}", e),
        status_code: None,
        error_code: None,
    })? {
        buffer.push_str(&String::from_utf8_lossy(&bytes));

        while let Some(newline_pos) = buffer.find('\n') {
            let line = buffer[..newline_pos].trim_end_matches('\r').to_string();
            buffer = buffer[newline_pos + 1..].to_string();

            if line.is_empty() || line.starts_with(':') {
                continue;
            }

            if let Some(data) = line.strip_prefix("data: ") {
                let data = data.trim();

                if data == "[DONE]" {
                    break;
                }

                let chunk: Value = match serde_json::from_str(data) {
                    Ok(v) => v,
                    Err(_) => continue,
                };

                accumulate_chunk(
                    &chunk,
                    &mut content,
                    &mut thinking_content,
                    &mut finish_reason,
                    &mut tokens_used,
                    &mut tool_calls,
                    &mut refusal,
                );

                let _ = chunk_tx.send(chunk);
            }
        }
    }

    let mut result = json!({
        "content": content,
        "role": "assistant",
        "finish_reason": finish_reason,
        "model_used": model,
        "tokens_used": tokens_used,
        "tool_calls": tool_calls,
    });

    if !thinking_content.is_empty() {
        result["thinking_content"] = json!(thinking_content);
    } else {
        result["thinking_content"] = Value::Null;
    }

    if !refusal.is_empty() {
        result["refusal"] = json!(refusal);
    } else {
        result["refusal"] = json!("");
    }

    result["logprobs"] = Value::Null;

    Ok(result)
}

/// Extract and accumulate fields from an SSE chunk.
fn accumulate_chunk(
    chunk: &Value,
    content: &mut String,
    thinking_content: &mut String,
    finish_reason: &mut String,
    tokens_used: &mut Value,
    tool_calls: &mut Vec<Value>,
    refusal: &mut String,
) {
    if let Some(usage) = chunk.get("usage") {
        if !usage.is_null() {
            *tokens_used = usage.clone();
        }
    }

    if let Some(choices) = chunk.get("choices").and_then(|c| c.as_array()) {
        if let Some(choice) = choices.first() {
            if let Some(delta) = choice.get("delta") {
                if let Some(c) = delta.get("content").and_then(|v| v.as_str()) {
                    content.push_str(c);
                }
                if let Some(tc) = delta.get("reasoning_content").and_then(|v| v.as_str()) {
                    thinking_content.push_str(tc);
                }
                if let Some(tcs) = delta.get("tool_calls").and_then(|v| v.as_array()) {
                    tool_calls.extend(tcs.iter().cloned());
                }
                if let Some(r) = delta.get("refusal").and_then(|v| v.as_str()) {
                    refusal.push_str(r);
                }
            }

            if let Some(fr) = choice.get("finish_reason").and_then(|v| v.as_str()) {
                *finish_reason = fr.to_string();
            }
        }
    }
}
