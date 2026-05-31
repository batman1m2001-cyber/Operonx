//! Azure OpenAI chat completion backend.
//!
//! Mirrors Python [`operonx/providers/llms/azure.py`](../../../../../operonx/providers/llms/azure.py).
//! Same wire shape as OpenAI but:
//!   - URL is `<endpoint>/openai/deployments/<deployment>/chat/completions?api-version=<v>`
//!   - Auth via `api-key` header (not Bearer).
//!
//! Internally we reuse OpenAI's request-body builder + SSE parser since
//! Azure-hosted OpenAI returns identical chunk shapes.

use async_trait::async_trait;
use futures::stream::{BoxStream, StreamExt};
use serde_json::Value;

use super::base::{BaseLLM, ChatCompletion, ChatCompletionChunk, LlmOpts, Message};
use super::config::AzureConfig;
use super::openai::build_request_body;
use super::response::LLMGenerator;
use crate::core::exceptions::OperonError;
use crate::providers::http::{get_client, ProviderError};

pub struct AzureLlm {
    pub config: AzureConfig,
}

impl AzureLlm {
    pub fn new(config: AzureConfig) -> Self {
        Self { config }
    }

    fn completions_url(&self) -> String {
        let base = self.config.azure_endpoint.trim_end_matches('/');
        // `model` here doubles as the deployment name — Azure parlance.
        format!(
            "{}/openai/deployments/{}/chat/completions?api-version={}",
            base, self.config.model, self.config.api_version
        )
    }
}

#[async_trait]
impl BaseLLM for AzureLlm {
    async fn generate(
        &self,
        messages: Vec<Message>,
        opts: &LlmOpts,
    ) -> Result<ChatCompletion, OperonError> {
        let body = build_request_body(&self.config.model, &messages, opts, false);
        let client = get_client();
        let resp = client
            .post(self.completions_url())
            .header("api-key", &self.config.api_key)
            .json(&body)
            .send()
            .await
            .map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("azure: {}", text))
                .with_status(status.as_u16())
                .into());
        }
        let completion: ChatCompletion = resp.json().await.map_err(ProviderError::from)?;
        Ok(completion)
    }

    async fn stream(
        &self,
        messages: Vec<Message>,
        opts: &LlmOpts,
    ) -> Result<BoxStream<'static, Result<ChatCompletionChunk, OperonError>>, OperonError> {
        let body = build_request_body(&self.config.model, &messages, opts, true);
        let client = get_client();
        let resp = client
            .post(self.completions_url())
            .header("api-key", &self.config.api_key)
            .json(&body)
            .send()
            .await
            .map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("azure stream: {}", text))
                .with_status(status.as_u16())
                .into());
        }
        let bytes_stream = resp.bytes_stream();
        let parsed = async_stream::try_stream! {
            futures::pin_mut!(bytes_stream);
            let mut buf = String::new();
            while let Some(chunk) = bytes_stream.next().await {
                let bytes = chunk.map_err(|e| OperonError::from(ProviderError::from(e)))?;
                buf.push_str(&String::from_utf8_lossy(&bytes));
                while let Some(idx) = find_event_boundary(&buf) {
                    let (event, rest) = buf.split_at(idx);
                    let event_owned = event.to_string();
                    buf = rest[event_terminator_len(&buf[idx..])..].to_string();
                    for line in event_owned.lines() {
                        if let Some(parsed) = LLMGenerator::parse(line) {
                            yield parsed;
                        }
                    }
                }
            }
            if !buf.trim().is_empty() {
                for line in buf.lines() {
                    if let Some(parsed) = LLMGenerator::parse(line) {
                        yield parsed;
                    }
                }
            }
        };
        Ok(Box::pin(parsed))
    }
}

fn find_event_boundary(buf: &str) -> Option<usize> {
    if let Some(i) = buf.find("\r\n\r\n") {
        if let Some(j) = buf.find("\n\n") {
            if j < i {
                return Some(j);
            }
        }
        return Some(i);
    }
    buf.find("\n\n")
}

fn event_terminator_len(buf: &str) -> usize {
    if buf.starts_with("\r\n\r\n") {
        4
    } else {
        2
    }
}

#[allow(dead_code)]
fn _kept_value_ref() -> Value {
    Value::Null
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn azure_url_embeds_deployment_and_api_version() {
        let cfg = AzureConfig {
            proxy: None,
            cost_per_input_token: None,
            cost_per_output_token: None,
            api_type: "azure".into(),
            api_key: "k".into(),
            api_version: "2024-02-15".into(),
            azure_endpoint: "https://az.example.com/".into(),
            model: "my-deployment".into(),
        };
        let llm = AzureLlm::new(cfg);
        assert_eq!(
            llm.completions_url(),
            "https://az.example.com/openai/deployments/my-deployment/chat/completions?api-version=2024-02-15"
        );
    }
}
