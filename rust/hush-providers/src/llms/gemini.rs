//! Gemini LLM provider — mirrors Python's `llms/gemini.py`.

use std::sync::mpsc::Sender;

use serde_json::{json, Value};

use crate::config::llm::GeminiConfig;
use crate::http::{get_client, ProviderError, ProviderResult};

/// Gemini (Vertex AI) provider struct.
pub struct GeminiProvider<'a> {
    pub config: &'a GeminiConfig,
    pub access_token: Option<String>,
}

impl<'a> GeminiProvider<'a> {
    pub fn new(config: &'a GeminiConfig, access_token: Option<String>) -> Self {
        GeminiProvider { config, access_token }
    }
}

impl super::base::LlmProvider for GeminiProvider<'_> {
    fn generate(&self, messages: &[Value], params: &Value) -> impl std::future::Future<Output = ProviderResult<Value>> + Send {
        let config = self.config;
        let token = self.access_token.as_deref();
        let inputs = json!({"messages": messages, "params": params});
        async move { chat_completion(config, &inputs, token).await }
    }
    fn stream(&self, messages: &[Value], params: &Value, chunk_tx: Sender<Value>) -> impl std::future::Future<Output = ProviderResult<Value>> + Send {
        let config = self.config;
        let token = self.access_token.as_deref();
        let inputs = json!({"messages": messages, "params": params});
        async move { chat_completion_stream(config, &inputs, token, chunk_tx).await }
    }
}
use crate::llms::types::{
    build_chat_request, build_streaming_request, format_completion_response,
    ChatCompletionResponse,
};

// =============================================================================
// Non-streaming
// =============================================================================

/// Gemini chat completion (via Vertex AI OpenAI-compatible endpoint).
pub async fn chat_completion(
    config: &GeminiConfig,
    inputs: &Value,
    access_token: Option<&str>,
) -> ProviderResult<Value> {
    let client = get_client();

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
// Streaming
// =============================================================================

/// Gemini streaming chat completion (via Vertex AI OpenAI-compatible endpoint).
pub async fn chat_completion_stream(
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

    super::openai::process_sse_stream(resp, &config.model, chunk_tx).await
}
