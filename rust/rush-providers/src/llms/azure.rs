//! Azure OpenAI LLM provider — chat completions + streaming.

use std::sync::mpsc::Sender;

use serde_json::{json, Value};

use crate::config::llm::AzureConfig;
use crate::http::{get_client, ProviderError, ProviderResult};
use crate::llms::types::{
    build_chat_request, format_completion_response, ChatCompletionResponse,
    StreamingChatCompletionRequest,
};

// =============================================================================
// Non-streaming
// =============================================================================

/// Azure OpenAI chat completion.
pub async fn chat_completion(
    config: &AzureConfig,
    inputs: &Value,
) -> ProviderResult<Value> {
    let client = get_client();

    let url = format!(
        "{}/openai/deployments/{}/chat/completions?api-version={}",
        config.azure_endpoint.trim_end_matches('/'),
        config.model,
        config.api_version
    );

    let mut request = build_chat_request(&config.model, inputs);
    request.n = None; // Azure doesn't support 'n'

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
// Streaming
// =============================================================================

/// Azure OpenAI streaming chat completion.
pub async fn chat_completion_stream(
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
    base_request.n = None;

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

    super::openai::process_sse_stream(resp, &config.model, chunk_tx).await
}
