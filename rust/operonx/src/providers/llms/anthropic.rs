//! Anthropic Claude chat completion backend.
//!
//! Mirrors Python [`operonx/providers/llms/anthropic.py`](../../../../../operonx/providers/llms/anthropic.py).
//! Uses the `/v1/messages` endpoint. Translates OpenAI-shaped messages into
//! Anthropic's `(system, messages)` split, and remaps `stop_reason` →
//! `finish_reason` so downstream consumers don't care which backend served
//! the call.

use std::collections::HashMap;

use async_trait::async_trait;
use futures::stream::{BoxStream, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::base::{
    BaseLLM, ChatCompletion, ChatCompletionChunk, ChunkChoice, CompletionChoice, LlmOpts, Message,
    Usage,
};
use super::config::AnthropicConfig;
use crate::core::exceptions::OperonError;
use crate::providers::http::{get_client, ProviderError};

pub struct AnthropicLlm {
    pub config: AnthropicConfig,
}

impl AnthropicLlm {
    pub fn new(config: AnthropicConfig) -> Self {
        Self { config }
    }

    fn messages_url(&self) -> String {
        let base = self.config.base_url.trim_end_matches('/');
        format!("{}/v1/messages", base)
    }

    fn headers(&self) -> reqwest::header::HeaderMap {
        use reqwest::header::{HeaderMap, HeaderValue};
        let mut h = HeaderMap::new();
        h.insert(
            "x-api-key",
            HeaderValue::from_str(&self.config.api_key).unwrap_or(HeaderValue::from_static("")),
        );
        h.insert(
            "anthropic-version",
            HeaderValue::from_str(&self.config.anthropic_version)
                .unwrap_or(HeaderValue::from_static("2023-06-01")),
        );
        h.insert("content-type", HeaderValue::from_static("application/json"));
        h
    }
}

#[async_trait]
impl BaseLLM for AnthropicLlm {
    async fn generate(
        &self,
        messages: Vec<Message>,
        opts: &LlmOpts,
    ) -> Result<ChatCompletion, OperonError> {
        let body = build_request_body(&self.config.model, &messages, opts, false);
        let client = get_client();
        let resp = client
            .post(self.messages_url())
            .headers(self.headers())
            .json(&body)
            .send()
            .await
            .map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("anthropic: {}", text))
                .with_status(status.as_u16())
                .into());
        }
        let raw: AnthropicResponse = resp.json().await.map_err(ProviderError::from)?;
        Ok(to_chat_completion(raw))
    }

    async fn stream(
        &self,
        messages: Vec<Message>,
        opts: &LlmOpts,
    ) -> Result<BoxStream<'static, Result<ChatCompletionChunk, OperonError>>, OperonError> {
        let body = build_request_body(&self.config.model, &messages, opts, true);
        let client = get_client();
        let resp = client
            .post(self.messages_url())
            .headers(self.headers())
            .json(&body)
            .send()
            .await
            .map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("anthropic stream: {}", text))
                .with_status(status.as_u16())
                .into());
        }

        let bytes_stream = resp.bytes_stream();
        let model = self.config.model.clone();
        let parsed = async_stream::try_stream! {
            futures::pin_mut!(bytes_stream);
            let mut buf = String::new();
            let mut current_id = String::new();
            while let Some(chunk) = bytes_stream.next().await {
                let bytes = chunk.map_err(|e| OperonError::from(ProviderError::from(e)))?;
                buf.push_str(&String::from_utf8_lossy(&bytes));
                while let Some(idx) = find_event_boundary(&buf) {
                    let (event, rest) = buf.split_at(idx);
                    let event_owned = event.to_string();
                    buf = rest[event_terminator_len(&buf[idx..])..].to_string();
                    if let Some(chunk) = parse_sse_event(&event_owned, &model, &mut current_id) {
                        yield chunk;
                    }
                }
            }
            if !buf.trim().is_empty() {
                if let Some(chunk) = parse_sse_event(&buf, &model, &mut current_id) {
                    yield chunk;
                }
            }
        };
        Ok(Box::pin(parsed))
    }
}

// ── Request body ────────────────────────────────────────────────────────

fn build_request_body(model: &str, messages: &[Message], opts: &LlmOpts, stream: bool) -> Value {
    let (system, msgs) = split_system(messages);
    let mut body = serde_json::Map::new();
    body.insert("model".into(), Value::String(model.into()));
    body.insert(
        "max_tokens".into(),
        Value::Number(serde_json::Number::from(opts.max_tokens.unwrap_or(1024))),
    );
    if let Some(sys) = system {
        body.insert("system".into(), Value::String(sys));
    }
    body.insert("messages".into(), Value::Array(msgs));
    if let Some(t) = opts.temperature {
        body.insert("temperature".into(), serde_json::json!(t));
    }
    if let Some(t) = opts.top_p {
        body.insert("top_p".into(), serde_json::json!(t));
    }
    if let Some(stop) = &opts.stop {
        body.insert("stop_sequences".into(), serde_json::json!(stop));
    }
    if stream {
        body.insert("stream".into(), Value::Bool(true));
    }
    Value::Object(body)
}

fn split_system(messages: &[Message]) -> (Option<String>, Vec<Value>) {
    let mut system = None;
    let mut out = Vec::with_capacity(messages.len());
    for m in messages {
        let role = m.role.as_str();
        let content_text = match &m.content {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        if role == "system" {
            system = Some(content_text);
        } else {
            out.push(serde_json::json!({"role": role, "content": content_text}));
        }
    }
    (system, out)
}

// ── Response parsing ─────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct AnthropicResponse {
    #[serde(default)]
    id: String,
    #[serde(default)]
    model: String,
    #[serde(default)]
    content: Vec<AnthropicBlock>,
    #[serde(default)]
    stop_reason: Option<String>,
    #[serde(default)]
    usage: Option<AnthropicUsage>,
}

#[derive(Debug, Deserialize, Serialize)]
struct AnthropicBlock {
    #[serde(rename = "type", default)]
    block_type: String,
    #[serde(default)]
    text: String,
}

#[derive(Debug, Deserialize)]
struct AnthropicUsage {
    #[serde(default)]
    input_tokens: u32,
    #[serde(default)]
    output_tokens: u32,
    /// Prompt-cache hit. Surfaced into OpenAI's
    /// `prompt_tokens_details.cached_tokens` to keep downstream metrics
    /// uniform across providers.
    #[serde(default)]
    cache_read_input_tokens: u32,
    /// Prompt-cache write. Surfaced into `extras.cache_write_tokens` on
    /// the OpenAI Usage struct (Python parity).
    #[serde(default)]
    cache_creation_input_tokens: u32,
}

fn to_chat_completion(raw: AnthropicResponse) -> ChatCompletion {
    let text: String = raw
        .content
        .iter()
        .filter(|b| b.block_type == "text")
        .map(|b| b.text.clone())
        .collect();
    let finish_reason = raw
        .stop_reason
        .as_deref()
        .map(map_stop_reason)
        .map(String::from);
    let usage = raw.usage.map(|u| {
        let mut extras = HashMap::new();
        if u.cache_creation_input_tokens > 0 {
            extras.insert(
                "cache_write_tokens".into(),
                Value::Number(u.cache_creation_input_tokens.into()),
            );
        }
        let mut prompt_tokens_details = serde_json::Map::new();
        prompt_tokens_details.insert(
            "cached_tokens".into(),
            Value::Number(u.cache_read_input_tokens.into()),
        );
        Usage {
            prompt_tokens: u.input_tokens,
            completion_tokens: u.output_tokens,
            total_tokens: u.input_tokens + u.output_tokens,
            prompt_tokens_details: Some(Value::Object(prompt_tokens_details)),
            extras,
        }
    });
    let message = Message {
        role: "assistant".into(),
        content: Value::String(text),
        name: None,
        tool_call_id: None,
        extras: HashMap::new(),
    };
    ChatCompletion {
        id: raw.id,
        object: "chat.completion".into(),
        created: chrono::Utc::now().timestamp(),
        model: raw.model,
        choices: vec![CompletionChoice {
            index: 0,
            message: Some(message),
            finish_reason,
            extras: HashMap::new(),
        }],
        usage,
        extras: HashMap::new(),
    }
}

fn map_stop_reason(reason: &str) -> &'static str {
    match reason {
        "end_turn" => "stop",
        "max_tokens" => "length",
        "stop_sequence" => "stop",
        "tool_use" => "tool_calls",
        _ => "stop",
    }
}

// ── Streaming SSE parsing ───────────────────────────────────────────────

fn parse_sse_event(
    event: &str,
    model: &str,
    current_id: &mut String,
) -> Option<ChatCompletionChunk> {
    let mut data: Option<&str> = None;
    let mut kind: Option<&str> = None;
    for line in event.lines() {
        if let Some(rest) = line.strip_prefix("event:") {
            kind = Some(rest.trim());
        } else if let Some(rest) = line.strip_prefix("data:") {
            data = Some(rest.trim());
        }
    }
    let data = data?;
    let kind = kind.unwrap_or("");
    let parsed: Value = serde_json::from_str(data).ok()?;

    match kind {
        "message_start" => {
            if let Some(id) = parsed
                .get("message")
                .and_then(|m| m.get("id"))
                .and_then(|v| v.as_str())
            {
                *current_id = id.to_string();
            }
            None
        }
        "content_block_delta" => {
            let text = parsed
                .get("delta")
                .and_then(|d| d.get("text"))
                .and_then(|v| v.as_str())?;
            Some(ChatCompletionChunk {
                id: current_id.clone(),
                object: "chat.completion.chunk".into(),
                created: chrono::Utc::now().timestamp(),
                model: model.into(),
                choices: vec![ChunkChoice {
                    index: 0,
                    delta: serde_json::json!({"content": text}),
                    finish_reason: None,
                }],
                extras: HashMap::new(),
            })
        }
        "message_delta" => {
            let reason = parsed
                .get("delta")
                .and_then(|d| d.get("stop_reason"))
                .and_then(|v| v.as_str())
                .map(|r| map_stop_reason(r).to_string());
            reason.map(|r| ChatCompletionChunk {
                id: current_id.clone(),
                object: "chat.completion.chunk".into(),
                created: chrono::Utc::now().timestamp(),
                model: model.into(),
                choices: vec![ChunkChoice {
                    index: 0,
                    delta: serde_json::json!({}),
                    finish_reason: Some(r),
                }],
                extras: HashMap::new(),
            })
        }
        _ => None,
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

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(role: &str, text: &str) -> Message {
        Message {
            role: role.into(),
            content: Value::String(text.into()),
            name: None,
            tool_call_id: None,
            extras: HashMap::new(),
        }
    }

    #[test]
    fn split_system_pulls_system_to_field() {
        let (sys, msgs) = split_system(&[
            msg("system", "be helpful"),
            msg("user", "hi"),
            msg("assistant", "hello"),
        ]);
        assert_eq!(sys.as_deref(), Some("be helpful"));
        assert_eq!(msgs.len(), 2);
        assert_eq!(msgs[0].get("role").and_then(|v| v.as_str()), Some("user"));
    }

    #[test]
    fn build_request_body_omits_none_fields() {
        let body = build_request_body(
            "claude-3-haiku",
            &[msg("user", "hi")],
            &LlmOpts {
                max_tokens: Some(64),
                ..LlmOpts::default()
            },
            false,
        );
        let obj = body.as_object().unwrap();
        assert_eq!(
            obj.get("model").and_then(|v| v.as_str()),
            Some("claude-3-haiku")
        );
        assert_eq!(obj.get("max_tokens").and_then(|v| v.as_u64()), Some(64));
        assert!(!obj.contains_key("stream"));
        assert!(!obj.contains_key("system"));
    }

    #[test]
    fn map_stop_reason_handles_known_strings() {
        assert_eq!(map_stop_reason("end_turn"), "stop");
        assert_eq!(map_stop_reason("max_tokens"), "length");
        assert_eq!(map_stop_reason("tool_use"), "tool_calls");
        assert_eq!(map_stop_reason("weird"), "stop");
    }

    #[test]
    fn to_chat_completion_concatenates_text_blocks() {
        let raw = AnthropicResponse {
            id: "msg_1".into(),
            model: "claude-3-haiku".into(),
            content: vec![
                AnthropicBlock {
                    block_type: "text".into(),
                    text: "Hello, ".into(),
                },
                AnthropicBlock {
                    block_type: "text".into(),
                    text: "world.".into(),
                },
            ],
            stop_reason: Some("end_turn".into()),
            usage: Some(AnthropicUsage {
                input_tokens: 4,
                output_tokens: 3,
                cache_read_input_tokens: 1,
                cache_creation_input_tokens: 0,
            }),
        };
        let out = to_chat_completion(raw);
        let msg = out.choices[0].message.as_ref().unwrap();
        assert_eq!(msg.content.as_str(), Some("Hello, world."));
        assert_eq!(out.choices[0].finish_reason.as_deref(), Some("stop"));
        let usage = out.usage.as_ref().unwrap();
        assert_eq!(usage.total_tokens, 7);
    }

    #[test]
    fn parse_sse_event_handles_content_block_delta() {
        let event = "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Hello\"}}\n";
        let mut id = String::new();
        let chunk = parse_sse_event(event, "claude-3-haiku", &mut id).expect("chunk");
        let delta = &chunk.choices[0].delta;
        assert_eq!(delta.get("content").and_then(|v| v.as_str()), Some("Hello"));
    }
}
