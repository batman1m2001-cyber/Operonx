//! Anthropic Claude LLM provider — chat completions + streaming.
//!
//! Uses Anthropic Messages API directly via reqwest (no SDK).
//! Converts between OpenAI-compatible format (used internally by Hush)
//! and Anthropic's format (system separated, different SSE events).

use std::sync::mpsc::Sender;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::config::llm::AnthropicConfig;
use crate::http::{get_client, ProviderError, ProviderResult};

// =============================================================================
// Provider struct — mirrors OpenAIProvider pattern
// =============================================================================

/// Anthropic provider struct — mirrors Python's AnthropicModel(BaseLLM).
pub struct AnthropicProvider<'a> {
    pub config: &'a AnthropicConfig,
}

impl<'a> AnthropicProvider<'a> {
    pub fn new(config: &'a AnthropicConfig) -> Self {
        AnthropicProvider { config }
    }
}

impl super::base::LlmProvider for AnthropicProvider<'_> {
    fn generate(
        &self,
        messages: &[Value],
        params: &Value,
    ) -> impl std::future::Future<Output = ProviderResult<Value>> + Send {
        let config = self.config;
        let inputs = json!({"messages": messages, "params": params});
        async move { chat_completion(config, &inputs).await }
    }

    fn stream(
        &self,
        messages: &[Value],
        params: &Value,
        chunk_tx: Sender<Value>,
    ) -> impl std::future::Future<Output = ProviderResult<Value>> + Send {
        let config = self.config;
        let inputs = json!({"messages": messages, "params": params});
        async move { chat_completion_stream(config, &inputs, chunk_tx).await }
    }
}

// =============================================================================
// Request types
// =============================================================================

#[derive(Serialize)]
struct AnthropicRequest {
    model: String,
    messages: Vec<Value>,
    max_tokens: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    system: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    temperature: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    top_p: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    stop_sequences: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    stream: Option<bool>,
}

// =============================================================================
// Response types
// =============================================================================

#[derive(Deserialize)]
struct AnthropicResponse {
    #[serde(default)]
    id: String,
    #[serde(default)]
    model: String,
    #[serde(default)]
    content: Vec<ContentBlock>,
    #[serde(default)]
    stop_reason: Option<String>,
    #[serde(default)]
    usage: AnthropicUsage,
}

#[derive(Deserialize)]
struct ContentBlock {
    #[serde(rename = "type", default)]
    block_type: String,
    #[serde(default)]
    text: Option<String>,
}

#[derive(Deserialize, Default)]
struct AnthropicUsage {
    #[serde(default)]
    input_tokens: i64,
    #[serde(default)]
    output_tokens: i64,
}

// =============================================================================
// Message conversion
// =============================================================================

/// Convert OpenAI-format messages to Anthropic format.
/// Extracts system message into separate field.
fn convert_messages(messages: &[Value]) -> (Option<String>, Vec<Value>) {
    let mut system = None;
    let mut converted = Vec::new();

    for msg in messages {
        let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("user");
        let content = msg.get("content").and_then(|v| v.as_str()).unwrap_or("").to_string();

        if role == "system" {
            system = Some(content);
        } else {
            converted.push(json!({"role": role, "content": content}));
        }
    }

    (system, converted)
}

fn map_stop_reason(reason: Option<&str>) -> &str {
    match reason {
        Some("end_turn") => "stop",
        Some("max_tokens") => "length",
        Some("stop_sequence") => "stop",
        Some("tool_use") => "tool_calls",
        _ => "stop",
    }
}

fn format_response(resp: AnthropicResponse) -> Value {
    let content: String = resp.content.iter()
        .filter(|b| b.block_type == "text")
        .filter_map(|b| b.text.as_deref())
        .collect::<Vec<_>>()
        .join("");

    json!({
        "content": content,
        "role": "assistant",
        "finish_reason": map_stop_reason(resp.stop_reason.as_deref()),
        "model_used": resp.model,
        "tokens_used": {
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
        },
        "tool_calls": [],
        "thinking_content": Value::Null,
        "refusal": "",
        "logprobs": Value::Null,
    })
}

// =============================================================================
// Build request
// =============================================================================

fn build_request(config: &AnthropicConfig, inputs: &Value, stream: bool) -> AnthropicRequest {
    let messages = inputs.get("messages").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let (system, anthropic_messages) = convert_messages(&messages);
    let max_tokens = inputs.get("max_tokens").and_then(|v| v.as_i64()).unwrap_or(4096);

    AnthropicRequest {
        model: config.model.clone(),
        messages: anthropic_messages,
        max_tokens,
        system,
        temperature: inputs.get("temperature").and_then(|v| v.as_f64()),
        top_p: inputs.get("top_p").and_then(|v| v.as_f64()),
        stop_sequences: inputs.get("stop").and_then(|v| {
            if let Some(arr) = v.as_array() {
                Some(arr.iter().filter_map(|s| s.as_str().map(String::from)).collect())
            } else if let Some(s) = v.as_str() {
                Some(vec![s.to_string()])
            } else {
                None
            }
        }),
        stream: if stream { Some(true) } else { None },
    }
}

// =============================================================================
// Non-streaming
// =============================================================================

pub async fn chat_completion(config: &AnthropicConfig, inputs: &Value) -> ProviderResult<Value> {
    let client = get_client();
    let url = format!("{}/v1/messages", config.base_url.trim_end_matches('/'));
    let request = build_request(config, inputs, false);

    let resp = client.post(&url)
        .header("Content-Type", "application/json")
        .header("x-api-key", &config.api_key)
        .header("anthropic-version", &config.anthropic_version)
        .json(&request)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Anthropic API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let body: AnthropicResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse Anthropic response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    Ok(format_response(body))
}

// =============================================================================
// Streaming
// =============================================================================

pub async fn chat_completion_stream(
    config: &AnthropicConfig,
    inputs: &Value,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let client = get_client();
    let url = format!("{}/v1/messages", config.base_url.trim_end_matches('/'));
    let request = build_request(config, inputs, true);

    let resp = client.post(&url)
        .header("Content-Type", "application/json")
        .header("x-api-key", &config.api_key)
        .header("anthropic-version", &config.anthropic_version)
        .json(&request)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Anthropic streaming API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    process_anthropic_sse_stream(resp, &config.model, chunk_tx).await
}

/// Process Anthropic SSE stream — different from OpenAI format.
///
/// Anthropic events:
/// - `event: content_block_delta` + `data: {"delta": {"type": "text_delta", "text": "..."}}`
/// - `event: message_delta` + `data: {"delta": {"stop_reason": "..."}, "usage": {...}}`
///
/// Each text delta is converted to OpenAI-compatible chunk format.
async fn process_anthropic_sse_stream(
    resp: reqwest::Response,
    model: &str,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let mut content = String::new();
    let mut finish_reason = "stop".to_string();
    let mut tokens_used = json!({});
    let mut model_used = model.to_string();
    let mut buffer = String::new();
    let mut current_event_type = String::new();
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

            if line.is_empty() { continue; }

            if let Some(event) = line.strip_prefix("event: ") {
                current_event_type = event.trim().to_string();
                continue;
            }

            if let Some(data) = line.strip_prefix("data: ") {
                let event_data: Value = match serde_json::from_str(data.trim()) {
                    Ok(v) => v,
                    Err(_) => continue,
                };

                match current_event_type.as_str() {
                    "message_start" => {
                        if let Some(msg) = event_data.get("message") {
                            if let Some(m) = msg.get("model").and_then(|v| v.as_str()) {
                                model_used = m.to_string();
                            }
                            if let Some(usage) = msg.get("usage") {
                                if let Some(input) = usage.get("input_tokens").and_then(|v| v.as_i64()) {
                                    tokens_used["prompt_tokens"] = json!(input);
                                }
                            }
                        }
                    }
                    "content_block_delta" => {
                        if let Some(delta) = event_data.get("delta") {
                            if let Some(text) = delta.get("text").and_then(|v| v.as_str()) {
                                content.push_str(text);
                                let chunk = json!({
                                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": Value::Null}]
                                });
                                let _ = chunk_tx.send(chunk);
                            }
                        }
                    }
                    "message_delta" => {
                        if let Some(delta) = event_data.get("delta") {
                            if let Some(sr) = delta.get("stop_reason").and_then(|v| v.as_str()) {
                                finish_reason = map_stop_reason(Some(sr)).to_string();
                            }
                        }
                        if let Some(usage) = event_data.get("usage") {
                            if let Some(output) = usage.get("output_tokens").and_then(|v| v.as_i64()) {
                                tokens_used["completion_tokens"] = json!(output);
                                let input = tokens_used.get("prompt_tokens").and_then(|v| v.as_i64()).unwrap_or(0);
                                tokens_used["total_tokens"] = json!(input + output);
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    Ok(json!({
        "content": content,
        "role": "assistant",
        "finish_reason": finish_reason,
        "model_used": model_used,
        "tokens_used": tokens_used,
        "tool_calls": [],
        "thinking_content": Value::Null,
        "refusal": "",
        "logprobs": Value::Null,
    }))
}

// =============================================================================
// Tests
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_convert_messages_basic() {
        let msgs = vec![json!({"role": "user", "content": "Hello"})];
        let (sys, converted) = convert_messages(&msgs);
        assert!(sys.is_none());
        assert_eq!(converted.len(), 1);
    }

    #[test]
    fn test_convert_messages_with_system() {
        let msgs = vec![
            json!({"role": "system", "content": "Be helpful."}),
            json!({"role": "user", "content": "Hi"}),
        ];
        let (sys, converted) = convert_messages(&msgs);
        assert_eq!(sys, Some("Be helpful.".to_string()));
        assert_eq!(converted.len(), 1);
    }

    #[test]
    fn test_map_stop_reason_variants() {
        assert_eq!(map_stop_reason(Some("end_turn")), "stop");
        assert_eq!(map_stop_reason(Some("max_tokens")), "length");
        assert_eq!(map_stop_reason(None), "stop");
    }

    #[test]
    fn test_format_response() {
        let resp = AnthropicResponse {
            id: "msg_1".to_string(),
            model: "claude-3-haiku".to_string(),
            content: vec![ContentBlock { block_type: "text".to_string(), text: Some("Hi!".to_string()) }],
            stop_reason: Some("end_turn".to_string()),
            usage: AnthropicUsage { input_tokens: 10, output_tokens: 5 },
        };
        let r = format_response(resp);
        assert_eq!(r["content"], "Hi!");
        assert_eq!(r["finish_reason"], "stop");
        assert_eq!(r["tokens_used"]["total_tokens"], 15);
    }
}
