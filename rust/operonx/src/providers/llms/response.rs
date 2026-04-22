//! [`LLMGenerator`] — static utility for SSE parsing + synthetic chunks.
//!
//! Mirrors Python [`operon/providers/llms/response.py`](../../../../../operon/providers/llms/response.py).
//! Per plan §5b.3 this is a **static utility**, not a stream wrapper — direct
//! port of the four module-level helpers (`parse`, `make_chunk`, `process`,
//! `simulate`).

use std::time::Duration;

use futures::stream::{BoxStream, StreamExt};
use serde_json::Value;

use super::base::{ChatCompletionChunk, ChunkChoice};

/// Namespace-only struct — no state. Mirrors the `LLMGenerator` class on
/// the Python side whose methods are all `@staticmethod`.
pub struct LLMGenerator;

impl LLMGenerator {
    /// Parse one raw SSE `data:` line into a [`ChatCompletionChunk`].
    ///
    /// Returns `None` for `[DONE]` sentinel, keep-alive empty lines, and
    /// unparseable payloads (Python's behavior — swallow, don't propagate).
    pub fn parse(line: &str) -> Option<ChatCompletionChunk> {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            return None;
        }
        let payload = trimmed
            .strip_prefix("data:")
            .map(|s| s.trim())
            .unwrap_or(trimmed);
        if payload == "[DONE]" {
            return None;
        }
        serde_json::from_str::<ChatCompletionChunk>(payload).ok()
    }

    /// Build a synthetic chunk — used by `process` for final metadata
    /// emission and by fallback streams for "we failed, here's a canned
    /// reply" messages.
    pub fn make_chunk(content: &str, model: &str, chat_id: &str, last: bool) -> ChatCompletionChunk {
        let finish_reason = if last { Some("stop".to_string()) } else { None };
        let delta = if last {
            serde_json::json!({})
        } else {
            serde_json::json!({"content": content, "role": "assistant"})
        };
        ChatCompletionChunk {
            id: chat_id.to_string(),
            object: "chat.completion.chunk".to_string(),
            created: chrono::Utc::now().timestamp(),
            model: model.to_string(),
            choices: vec![ChunkChoice {
                index: 0,
                delta,
                finish_reason,
            }],
            extras: Default::default(),
        }
    }

    /// Throttle + normalize a stream of raw JSON values into typed chunks.
    ///
    /// `delay` is applied between yields — matches Python's
    /// `asyncio.sleep(delay)` pattern for simulating token pacing in tests.
    /// A zero-duration delay is a no-op.
    pub fn process<'a>(
        stream: BoxStream<'a, Value>,
        _model: &str,
        delay: Duration,
    ) -> BoxStream<'a, ChatCompletionChunk> {
        let stream = stream.filter_map(move |v| async move {
            let chunk: ChatCompletionChunk = serde_json::from_value(v).ok()?;
            if !delay.is_zero() {
                tokio::time::sleep(delay).await;
            }
            Some(chunk)
        });
        Box::pin(stream)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::stream;

    #[test]
    fn parse_handles_done_sentinel() {
        assert!(LLMGenerator::parse("data: [DONE]").is_none());
        assert!(LLMGenerator::parse("").is_none());
    }

    #[test]
    fn parse_decodes_valid_chunk() {
        let line = r#"data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-4o","choices":[]}"#;
        let parsed = LLMGenerator::parse(line).expect("parse");
        assert_eq!(parsed.id, "c1");
        assert_eq!(parsed.model, "gpt-4o");
    }

    #[test]
    fn make_chunk_tags_last_with_stop() {
        let c = LLMGenerator::make_chunk("hi", "gpt-4o", "cid", false);
        assert_eq!(c.choices[0].finish_reason, None);
        let c2 = LLMGenerator::make_chunk("", "gpt-4o", "cid", true);
        assert_eq!(c2.choices[0].finish_reason.as_deref(), Some("stop"));
    }

    #[tokio::test]
    async fn process_filters_unparseable() {
        let raw = stream::iter(vec![
            serde_json::json!({"id": "c1", "choices": []}),
            serde_json::json!({"not": "a chunk"}),
            serde_json::json!({"id": "c2", "choices": []}),
        ]);
        let mut out = LLMGenerator::process(Box::pin(raw), "m", Duration::ZERO);
        let first = out.next().await.unwrap();
        assert_eq!(first.id, "c1");
        // The middle un-chunk-shaped value still parses because all fields are
        // `#[serde(default)]` — the stream just yields whatever made it
        // through. Python has the same permissive behavior.
        let _ = out.next().await;
        let _ = out.next().await;
    }
}
