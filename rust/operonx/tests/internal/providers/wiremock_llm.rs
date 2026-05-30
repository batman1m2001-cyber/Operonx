//! Provider HTTP contract tests via `wiremock`.
//!
//! Per plan §11 Phase 9: exercise LLM / embedding / reranker providers
//! against a mocked HTTP server so CI has no live-API dependency. These
//! tests prove the client constructs the right request shape and parses
//! the server's response correctly.

use operonx::providers::llms::base::{BaseLLM, LlmOpts, Message};
use operonx::providers::llms::config::OpenAIConfig;
use operonx::providers::llms::openai::OpenAILlm;
use serde_json::json;
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn user_message(text: &str) -> Message {
    Message {
        role: "user".into(),
        content: serde_json::Value::String(text.into()),
        name: None,
        tool_call_id: None,
        extras: Default::default(),
    }
}

fn fake_completion_response() -> serde_json::Value {
    json!({
        "id": "chatcmpl-test-1",
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Hello, world."},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}
    })
}

fn cfg_for(base_url: String) -> OpenAIConfig {
    OpenAIConfig {
        proxy: None,
        cost_per_input_token: None,
        cost_per_output_token: None,
        api_type: "openai".into(),
        api_key: "sk-fake".into(),
        base_url,
        model: "gpt-4o".into(),
        batch_size: 0,
        batch_flush_interval: 5.0,
        batch_poll_interval: 30.0,
        batch_timeout: 3600.0,
    }
}

#[tokio::test]
async fn openai_llm_generate_posts_bearer_auth_and_parses_completion() {
    let server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/chat/completions"))
        .and(header("authorization", "Bearer sk-fake"))
        .respond_with(ResponseTemplate::new(200).set_body_json(fake_completion_response()))
        .expect(1)
        .mount(&server)
        .await;

    let llm = OpenAILlm::new(cfg_for(server.uri()));
    let messages = vec![user_message("Say hi")];
    let result = llm
        .generate(messages, &LlmOpts::default())
        .await
        .expect("generate should succeed against mock");

    assert_eq!(result.model, "gpt-4o");
    let first = result
        .choices
        .first()
        .expect("response has at least one choice");
    let message = first.message.as_ref().expect("choice has a message object");
    assert_eq!(message.content.as_str(), Some("Hello, world."));
    let usage = result.usage.as_ref().expect("usage is populated");
    assert_eq!(usage.total_tokens, 7);
}

#[tokio::test]
async fn openai_llm_stream_decodes_sse_chunks_and_done_sentinel() {
    // SSE wire shape: each event terminated by a blank line. `[DONE]` is
    // the closing sentinel; LLMGenerator::parse swallows it so it never
    // surfaces as a chunk.
    let sse = "\
data: {\"id\":\"chatcmpl-1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hello\"}}]}\n\n\
data: {\"id\":\"chatcmpl-1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\", \"}}]}\n\n\
data: {\"id\":\"chatcmpl-1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"world.\"}}]}\n\n\
data: [DONE]\n\n";

    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/chat/completions"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("content-type", "text/event-stream")
                .set_body_string(sse),
        )
        .expect(1)
        .mount(&server)
        .await;

    let llm = OpenAILlm::new(cfg_for(server.uri()));
    let mut stream = llm
        .stream(vec![user_message("Say hi")], &LlmOpts::default())
        .await
        .expect("stream should open");

    use futures::StreamExt;
    let mut deltas = Vec::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.expect("chunk parse");
        let delta = chunk.choices[0]
            .delta
            .get("content")
            .and_then(|v| v.as_str())
            .map(String::from)
            .unwrap_or_default();
        deltas.push(delta);
    }
    assert_eq!(deltas, vec!["Hello", ", ", "world."]);
}

#[tokio::test]
async fn openai_llm_stream_handles_split_events_across_chunks() {
    // Validate that a `data:` line whose closing `\n\n` arrives in a later
    // TCP packet is still parsed correctly. wiremock sends the body
    // atomically so we can't truly mimic that — but we can interleave a
    // keep-alive blank between two real events and confirm both arrive.
    let sse = "\
\n\
data: {\"id\":\"a\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"x\"}}]}\n\n\
\n\
data: {\"id\":\"a\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"y\"}}]}\n\n\
data: [DONE]\n\n";

    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/chat/completions"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("content-type", "text/event-stream")
                .set_body_string(sse),
        )
        .mount(&server)
        .await;

    let llm = OpenAILlm::new(cfg_for(server.uri()));
    let mut stream = llm
        .stream(vec![user_message("hi")], &LlmOpts::default())
        .await
        .expect("stream");
    use futures::StreamExt;
    let mut deltas = Vec::new();
    while let Some(c) = stream.next().await {
        let c = c.expect("chunk");
        deltas.push(
            c.choices[0]
                .delta
                .get("content")
                .and_then(|v| v.as_str())
                .map(String::from)
                .unwrap_or_default(),
        );
    }
    assert_eq!(deltas, vec!["x", "y"]);
}

#[tokio::test]
async fn openai_llm_stream_surfaces_http_error() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/chat/completions"))
        .respond_with(ResponseTemplate::new(500).set_body_string("server down"))
        .mount(&server)
        .await;
    let llm = OpenAILlm::new(cfg_for(server.uri()));
    match llm.stream(vec![user_message("x")], &LlmOpts::default()).await {
        Ok(_) => panic!("500 should not produce a stream"),
        Err(e) => {
            let msg = e.to_string();
            assert!(msg.contains("openai stream"), "err message: {}", msg);
        }
    }
}

#[tokio::test]
async fn openai_llm_generate_surfaces_http_error() {
    let server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/chat/completions"))
        .respond_with(
            ResponseTemplate::new(429)
                .set_body_string("{\"error\": {\"message\": \"rate limited\"}}"),
        )
        .expect(1)
        .mount(&server)
        .await;

    let llm = OpenAILlm::new(cfg_for(server.uri()));
    let err = llm
        .generate(vec![user_message("hi")], &LlmOpts::default())
        .await
        .expect_err("429 must surface as an error");
    let msg = err.to_string();
    assert!(msg.contains("openai"), "err message: {}", msg);
}
