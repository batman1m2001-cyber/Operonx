//! LLM HTTP clients — OpenAI, Azure, Gemini chat completions.
//!
//! Each function makes a single HTTP request to the provider API and returns
//! a parsed response. No GIL needed — these are pure async Rust.

use std::sync::mpsc::Sender;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::config::llm::{AzureConfig, GeminiConfig, LLMConfig, OpenAIConfig};
use crate::http::{get_client, ProviderError, ProviderResult};

// =============================================================================
// Request/Response types
// =============================================================================

/// OpenAI-compatible chat completion request.
#[derive(Serialize, Clone)]
struct ChatCompletionRequest {
    model: String,
    messages: Vec<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    temperature: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    top_p: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    max_tokens: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    stop: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    response_format: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    tools: Option<Vec<Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    tool_choice: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    seed: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    frequency_penalty: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    presence_penalty: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    n: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    logprobs: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    top_logprobs: Option<i64>,
}

/// Streaming variant — adds stream + stream_options fields.
#[derive(Serialize)]
struct StreamingChatCompletionRequest {
    #[serde(flatten)]
    base: ChatCompletionRequest,
    stream: bool,
    stream_options: Value,
}

/// OpenAI chat completion response.
#[derive(Deserialize)]
struct ChatCompletionResponse {
    choices: Vec<Choice>,
    usage: Option<Usage>,
    model: String,
}

#[derive(Deserialize)]
struct Choice {
    message: ResponseMessage,
    finish_reason: Option<String>,
    logprobs: Option<Value>,
}

#[derive(Deserialize)]
struct ResponseMessage {
    role: String,
    content: Option<String>,
    tool_calls: Option<Vec<Value>>,
    #[serde(alias = "reasoning_content")]
    thinking_content: Option<String>,
    refusal: Option<String>,
}

#[derive(Deserialize)]
struct Usage {
    prompt_tokens: i64,
    completion_tokens: i64,
    total_tokens: i64,
    #[serde(default)]
    cache_creation_input_tokens: Option<i64>,
    #[serde(default)]
    cache_read_input_tokens: Option<i64>,
}

// =============================================================================
// Dispatch by LLMConfig variant
// =============================================================================

/// Execute a chat completion request against the appropriate backend.
///
/// Dispatches based on LLMConfig variant (OpenAI/Azure/Gemini).
/// Returns a flat JSON object with all output fields.
pub async fn chat_completion(
    config: &LLMConfig,
    inputs: &Value,
    access_token: Option<&str>,
) -> ProviderResult<Value> {
    match config {
        LLMConfig::OpenAI(c) => openai_chat_completion(c, inputs, access_token).await,
        LLMConfig::Azure(c) => azure_chat_completion(c, inputs).await,
        LLMConfig::Gemini(c) => gemini_chat_completion(c, inputs, access_token).await,
    }
}

// =============================================================================
// OpenAI / vLLM
// =============================================================================

async fn openai_chat_completion(
    config: &OpenAIConfig,
    inputs: &Value,
    access_token: Option<&str>,
) -> ProviderResult<Value> {
    let client = get_client();
    let url = format!(
        "{}/chat/completions",
        config.base_url.trim_end_matches('/')
    );

    // Use access_token (keycloak) if provided, otherwise use config api_key
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
// Azure OpenAI
// =============================================================================

async fn azure_chat_completion(
    config: &AzureConfig,
    inputs: &Value,
) -> ProviderResult<Value> {
    let client = get_client();

    // Azure uses deployment-based URLs:
    // {azure_endpoint}/openai/deployments/{model}/chat/completions?api-version={version}
    let url = format!(
        "{}/openai/deployments/{}/chat/completions?api-version={}",
        config.azure_endpoint.trim_end_matches('/'),
        config.model,
        config.api_version
    );

    // Azure uses a subset of params — filter unsupported ones
    let mut request = build_chat_request(&config.model, inputs);
    // Azure doesn't support 'n' or 'stream_options'
    request.n = None;

    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .header("api-key", &config.api_key)
        .json(&request)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Azure API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let body: ChatCompletionResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse Azure response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    Ok(format_completion_response(body))
}

// =============================================================================
// Gemini (via OpenAI-compatible endpoint on Vertex AI)
// =============================================================================

async fn gemini_chat_completion(
    config: &GeminiConfig,
    inputs: &Value,
    access_token: Option<&str>,
) -> ProviderResult<Value> {
    let client = get_client();

    // Gemini on Vertex AI uses OpenAI-compatible endpoint:
    // https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/endpoints/openapi/chat/completions
    let url = format!(
        "https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/endpoints/openapi/chat/completions",
        location = config.location,
        project = config.project_id,
    );

    let token = access_token.ok_or_else(|| ProviderError {
        message: "Gemini requires an OAuth2 access token (from Google service account auth)"
            .to_string(),
        status_code: None,
        error_code: None,
    })?;

    let request = build_chat_request(&config.model, inputs);

    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {}", token))
        .json(&request)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Gemini API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let body: ChatCompletionResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse Gemini response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    Ok(format_completion_response(body))
}

// =============================================================================
// Helpers
// =============================================================================

/// Build a ChatCompletionRequest from op inputs.
fn build_chat_request(model: &str, inputs: &Value) -> ChatCompletionRequest {
    let messages = inputs
        .get("messages")
        .cloned()
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default();

    ChatCompletionRequest {
        model: model.to_string(),
        messages,
        temperature: inputs.get("temperature").and_then(|v| v.as_f64()),
        top_p: inputs.get("top_p").and_then(|v| v.as_f64()),
        max_tokens: inputs.get("max_tokens").and_then(|v| v.as_i64()),
        stop: inputs.get("stop").cloned(),
        response_format: inputs.get("response_format").cloned(),
        tools: inputs
            .get("tools")
            .and_then(|v| v.as_array().cloned()),
        tool_choice: inputs.get("tool_choice").cloned(),
        seed: inputs.get("seed").and_then(|v| v.as_i64()),
        frequency_penalty: inputs.get("frequency_penalty").and_then(|v| v.as_f64()),
        presence_penalty: inputs.get("presence_penalty").and_then(|v| v.as_f64()),
        n: inputs.get("n").and_then(|v| v.as_i64()),
        logprobs: inputs.get("logprobs").and_then(|v| v.as_bool()),
        top_logprobs: inputs.get("top_logprobs").and_then(|v| v.as_i64()),
    }
}

/// Format a ChatCompletionResponse into the flat output dict.
fn format_completion_response(resp: ChatCompletionResponse) -> Value {
    let choice = resp.choices.into_iter().next();

    let (content, role, finish_reason, tool_calls, thinking_content, refusal, logprobs) =
        match choice {
            Some(c) => (
                c.message.content.unwrap_or_default(),
                c.message.role,
                c.finish_reason.unwrap_or_else(|| "stop".to_string()),
                c.message.tool_calls.unwrap_or_default(),
                c.message.thinking_content.unwrap_or_default(),
                c.message.refusal.unwrap_or_default(),
                c.logprobs.unwrap_or(Value::Null),
            ),
            None => (
                String::new(),
                "assistant".to_string(),
                "stop".to_string(),
                Vec::new(),
                String::new(),
                String::new(),
                Value::Null,
            ),
        };

    let tokens_used = match resp.usage {
        Some(u) => {
            let mut t = json!({
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens,
            });
            if let Some(cache_create) = u.cache_creation_input_tokens {
                t["cache_creation_input_tokens"] = json!(cache_create);
            }
            if let Some(cache_read) = u.cache_read_input_tokens {
                t["cache_read_input_tokens"] = json!(cache_read);
            }
            t
        }
        None => json!({}),
    };

    json!({
        "content": content,
        "role": role,
        "finish_reason": finish_reason,
        "model_used": resp.model,
        "tokens_used": tokens_used,
        "tool_calls": tool_calls,
        "thinking_content": thinking_content,
        "refusal": refusal,
        "logprobs": logprobs,
    })
}

/// Format a batch result body (already a ChatCompletion JSON).
/// Used by batch coordinator to parse individual results.
pub fn format_batch_result(body: &Value) -> Value {
    // Try to parse as ChatCompletionResponse
    if let Ok(resp) = serde_json::from_value::<ChatCompletionResponse>(body.clone()) {
        return format_completion_response(resp);
    }
    // Fallback: return as-is with required fields
    let mut result = body.clone();
    if result.get("content").is_none() {
        result["content"] = json!("");
    }
    if result.get("role").is_none() {
        result["role"] = json!("assistant");
    }
    result
}

// =============================================================================
// Streaming chat completions
// =============================================================================

/// Execute a streaming chat completion request against the appropriate backend.
///
/// Sends each SSE chunk as a `serde_json::Value` through `chunk_tx`.
/// Returns the accumulated final response (same format as non-streaming).
pub async fn chat_completion_stream(
    config: &LLMConfig,
    inputs: &Value,
    access_token: Option<&str>,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    match config {
        LLMConfig::OpenAI(c) => openai_chat_completion_stream(c, inputs, access_token, chunk_tx).await,
        LLMConfig::Azure(c) => azure_chat_completion_stream(c, inputs, chunk_tx).await,
        LLMConfig::Gemini(c) => gemini_chat_completion_stream(c, inputs, access_token, chunk_tx).await,
    }
}

/// OpenAI / vLLM streaming chat completion.
async fn openai_chat_completion_stream(
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

/// Azure OpenAI streaming chat completion.
async fn azure_chat_completion_stream(
    config: &AzureConfig,
    inputs: &Value,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let client = get_client();

    let url = format!(
        "{}/openai/deployments/{}/chat/completions?api-version={}",
        config.azure_endpoint.trim_end_matches('/'),
        config.model,
        config.api_version
    );

    let mut base_request = build_chat_request(&config.model, inputs);
    base_request.n = None; // Azure doesn't support 'n' in streaming

    let request = StreamingChatCompletionRequest {
        base: base_request,
        stream: true,
        stream_options: json!({"include_usage": true}),
    };

    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .header("api-key", &config.api_key)
        .json(&request)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Azure streaming API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    process_sse_stream(resp, &config.model, chunk_tx).await
}

/// Gemini streaming chat completion (via Vertex AI OpenAI-compatible endpoint).
async fn gemini_chat_completion_stream(
    config: &GeminiConfig,
    inputs: &Value,
    access_token: Option<&str>,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let client = get_client();

    let url = format!(
        "https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/endpoints/openapi/chat/completions",
        location = config.location,
        project = config.project_id,
    );

    let token = access_token.ok_or_else(|| ProviderError {
        message: "Gemini streaming requires an OAuth2 access token".to_string(),
        status_code: None,
        error_code: None,
    })?;

    let request = build_streaming_request(&config.model, inputs);

    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {}", token))
        .json(&request)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Gemini streaming API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    process_sse_stream(resp, &config.model, chunk_tx).await
}

/// Build a streaming request from a base chat request.
fn build_streaming_request(model: &str, inputs: &Value) -> StreamingChatCompletionRequest {
    StreamingChatCompletionRequest {
        base: build_chat_request(model, inputs),
        stream: true,
        stream_options: json!({"include_usage": true}),
    }
}

/// Process an SSE (Server-Sent Events) stream from an OpenAI-compatible API.
///
/// Parses SSE `data: {...}` lines, sends each chunk to the channel,
/// and accumulates content, thinking_content, tool_calls, and usage.
/// Returns the accumulated final response.
async fn process_sse_stream(
    resp: reqwest::Response,
    model: &str,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    // Accumulation state
    let mut content = String::new();
    let mut thinking_content = String::new();
    let mut finish_reason = "stop".to_string();
    let mut tokens_used = json!({});
    let mut tool_calls: Vec<Value> = Vec::new();
    let mut refusal = String::new();

    // SSE line buffer (chunks from reqwest may contain partial lines)
    let mut buffer = String::new();

    // Read the byte stream
    let mut stream_body = resp;
    while let Some(bytes) = stream_body.chunk().await.map_err(|e| ProviderError {
        message: format!("Stream read error: {}", e),
        status_code: None,
        error_code: None,
    })? {
        buffer.push_str(&String::from_utf8_lossy(&bytes));

        // Process complete lines in the buffer
        while let Some(newline_pos) = buffer.find('\n') {
            let line = buffer[..newline_pos].trim_end_matches('\r').to_string();
            buffer = buffer[newline_pos + 1..].to_string();

            // Skip empty lines and comments
            if line.is_empty() || line.starts_with(':') {
                continue;
            }

            // Parse SSE data lines
            if let Some(data) = line.strip_prefix("data: ") {
                let data = data.trim();

                // [DONE] sentinel
                if data == "[DONE]" {
                    break;
                }

                // Parse JSON chunk
                let chunk: Value = match serde_json::from_str(data) {
                    Ok(v) => v,
                    Err(_) => continue, // Skip malformed chunks
                };

                // Extract and accumulate fields from chunk
                accumulate_chunk(
                    &chunk,
                    &mut content,
                    &mut thinking_content,
                    &mut finish_reason,
                    &mut tokens_used,
                    &mut tool_calls,
                    &mut refusal,
                );

                // Send chunk to caller (ignore error if receiver dropped)
                let _ = chunk_tx.send(chunk);
            }
        }
    }

    // Build accumulated response (same format as non-streaming)
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
///
/// Mirrors Python's `_handle_streaming()` chunk processing:
/// - content: delta.content → concatenate
/// - thinking_content: delta.reasoning_content → concatenate
/// - tool_calls: delta.tool_calls → extend list
/// - refusal: delta.refusal → concatenate
/// - usage: chunk.usage → overwrite (last chunk has final usage)
/// - finish_reason: choice.finish_reason → overwrite when set
fn accumulate_chunk(
    chunk: &Value,
    content: &mut String,
    thinking_content: &mut String,
    finish_reason: &mut String,
    tokens_used: &mut Value,
    tool_calls: &mut Vec<Value>,
    refusal: &mut String,
) {
    // Usage (may be null until final chunk)
    if let Some(usage) = chunk.get("usage") {
        if !usage.is_null() {
            *tokens_used = usage.clone();
        }
    }

    // Process choices[0].delta
    if let Some(choices) = chunk.get("choices").and_then(|c| c.as_array()) {
        if let Some(choice) = choices.first() {
            if let Some(delta) = choice.get("delta") {
                // Content
                if let Some(c) = delta.get("content").and_then(|v| v.as_str()) {
                    content.push_str(c);
                }

                // Reasoning/thinking content
                if let Some(tc) = delta.get("reasoning_content").and_then(|v| v.as_str()) {
                    thinking_content.push_str(tc);
                }

                // Tool calls (extend)
                if let Some(tcs) = delta.get("tool_calls").and_then(|v| v.as_array()) {
                    tool_calls.extend(tcs.iter().cloned());
                }

                // Refusal
                if let Some(r) = delta.get("refusal").and_then(|v| v.as_str()) {
                    refusal.push_str(r);
                }
            }

            // Finish reason
            if let Some(fr) = choice.get("finish_reason").and_then(|v| v.as_str()) {
                *finish_reason = fr.to_string();
            }
        }
    }
}
