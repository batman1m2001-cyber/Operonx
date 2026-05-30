//! OpenAI (and OpenAI-compatible, e.g. vLLM) chat completion backend.
//!
//! Mirrors Python [`operonx/providers/llms/openai.py`](../../../../../operonx/providers/llms/openai.py).
//! Handles both `api_type="openai"` and `api_type="vllm"` (same wire shape).
//!
//! Both `generate()` (one-shot completion) and `stream()` (SSE chunks) are
//! implemented. The streaming path uses [`LLMGenerator::parse`] from
//! [`response`](super::response) to decode each `data:` line.

use std::collections::HashMap;

use async_trait::async_trait;
use futures::stream::{BoxStream, StreamExt};
use serde::Serialize;
use serde_json::Value;

use super::base::{BaseLLM, ChatCompletion, ChatCompletionChunk, LlmOpts, Message};
use super::config::OpenAIConfig;
use super::response::LLMGenerator;
use crate::core::exceptions::OperonError;
use crate::providers::http::{get_client, ProviderError};

/// OpenAI-compatible LLM backend.
pub struct OpenAILlm {
    pub config: OpenAIConfig,
}

impl OpenAILlm {
    pub fn new(config: OpenAIConfig) -> Self {
        Self { config }
    }

    fn completions_url(&self) -> String {
        let base = if self.config.base_url.is_empty() {
            "https://api.openai.com/v1".to_string()
        } else {
            self.config.base_url.trim_end_matches('/').to_string()
        };
        format!("{}/chat/completions", base)
    }
}

#[async_trait]
impl BaseLLM for OpenAILlm {
    async fn generate(
        &self,
        messages: Vec<Message>,
        opts: &LlmOpts,
    ) -> Result<ChatCompletion, OperonError> {
        let body = build_request_body(&self.config.model, &messages, opts, false);
        let client = get_client();
        let resp = client
            .post(self.completions_url())
            .bearer_auth(&self.config.api_key)
            .json(&body)
            .send()
            .await
            .map_err(ProviderError::from)?;

        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("openai: {}", text))
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
        // POST chat/completions with stream=true; OpenAI replies with
        // Server-Sent Events (each event is a `data: <json>` line followed
        // by a blank line). We buffer the byte stream, split on SSE event
        // boundaries (`\n\n`), and feed each `data:` line to
        // `LLMGenerator::parse` which returns one typed chunk per line or
        // None for the `[DONE]` sentinel + keep-alive blanks. Mirrors the
        // Python `_stream_core` path verified against real OpenAI.
        let body = build_request_body(&self.config.model, &messages, opts, true);
        let client = get_client();
        let resp = client
            .post(self.completions_url())
            .bearer_auth(&self.config.api_key)
            .json(&body)
            .send()
            .await
            .map_err(ProviderError::from)?;

        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("openai stream: {}", text))
                .with_status(status.as_u16())
                .into());
        }

        let bytes_stream = resp.bytes_stream();
        let parsed = async_stream::try_stream! {
            // Pin the stream so we can poll across yields. Reqwest's
            // bytes_stream is Send but not Unpin in the futures::Stream
            // sense — wrap once.
            futures::pin_mut!(bytes_stream);
            let mut buf = String::new();
            while let Some(chunk) = bytes_stream.next().await {
                let bytes = chunk.map_err(|e| OperonError::from(ProviderError::from(e)))?;
                // OpenAI emits valid UTF-8; use from_utf8_lossy so a
                // partial multi-byte sequence at a chunk boundary still
                // accumulates correctly (the next chunk's continuation
                // bytes will land in `buf` and parse as a whole on the
                // next event boundary).
                buf.push_str(&String::from_utf8_lossy(&bytes));
                // SSE event boundary is a blank line — i.e. "\n\n" (and
                // occasionally "\r\n\r\n" depending on the server).
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
            // Drain trailing data — some servers omit the final newline.
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

/// Locate the start of an SSE event terminator (`\n\n` or `\r\n\r\n`)
/// inside `buf`. Returns the byte index of the first terminator's first
/// char, or `None` if no complete event is in the buffer yet.
fn find_event_boundary(buf: &str) -> Option<usize> {
    // Prefer the shorter terminator if both could match; lookup wins.
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

/// Serialize messages + options into the OpenAI chat/completions request
/// body. Kept as a free fn so Azure / vLLM / other OpenAI-flavored
/// backends can reuse it.
pub(crate) fn build_request_body(
    model: &str,
    messages: &[Message],
    opts: &LlmOpts,
    stream: bool,
) -> Value {
    #[derive(Serialize)]
    struct Body<'a> {
        model: &'a str,
        messages: &'a [Message],
        stream: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        temperature: Option<f32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        top_p: Option<f32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        n: Option<u32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        stop: Option<&'a Vec<String>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        max_tokens: Option<u32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        frequency_penalty: Option<f32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        presence_penalty: Option<f32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        response_format: Option<&'a Value>,
        #[serde(skip_serializing_if = "Option::is_none")]
        tools: Option<&'a Value>,
        #[serde(flatten)]
        extras: &'a HashMap<String, Value>,
    }
    serde_json::to_value(Body {
        model,
        messages,
        stream,
        temperature: opts.temperature,
        top_p: opts.top_p,
        n: opts.n,
        stop: opts.stop.as_ref(),
        max_tokens: opts.max_tokens,
        frequency_penalty: opts.frequency_penalty,
        presence_penalty: opts.presence_penalty,
        response_format: opts.response_format.as_ref(),
        tools: opts.tools.as_ref(),
        extras: &opts.extras,
    })
    .unwrap_or(Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(role: &str, text: &str) -> Message {
        Message {
            role: role.into(),
            content: Value::from(text),
            name: None,
            tool_call_id: None,
            extras: Default::default(),
        }
    }

    #[test]
    fn completions_url_uses_override_base() {
        let cfg = OpenAIConfig {
            proxy: None,
            cost_per_input_token: None,
            cost_per_output_token: None,
            api_type: "openai".into(),
            api_key: String::new(),
            base_url: "https://my.proxy/v1/".into(),
            model: "gpt-4o".into(),
            batch_size: 0,
            batch_flush_interval: 5.0,
            batch_poll_interval: 30.0,
            batch_timeout: 3600.0,
        };
        let llm = OpenAILlm::new(cfg);
        assert_eq!(
            llm.completions_url(),
            "https://my.proxy/v1/chat/completions"
        );
    }

    #[test]
    fn build_request_body_omits_none_fields() {
        let opts = LlmOpts {
            temperature: Some(0.2),
            max_tokens: Some(16),
            ..LlmOpts::default()
        };
        let body = build_request_body("gpt-4o", &[msg("user", "hi")], &opts, false);
        let obj = body.as_object().unwrap();
        assert!(obj.contains_key("temperature"));
        assert!(obj.contains_key("max_tokens"));
        // Not set → must be omitted (see `skip_serializing_if = "Option::is_none"`).
        assert!(!obj.contains_key("top_p"));
        assert!(!obj.contains_key("frequency_penalty"));
    }
}
