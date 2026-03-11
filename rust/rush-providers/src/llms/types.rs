//! Shared LLM request/response types — used by all LLM providers.
//!
//! These types mirror the OpenAI Chat Completions API format,
//! which is also used by Azure, vLLM, and Gemini (Vertex AI compat).

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

// =============================================================================
// Request types
// =============================================================================

/// OpenAI-compatible chat completion request.
#[derive(Serialize, Clone)]
pub struct ChatCompletionRequest {
    pub model: String,
    pub messages: Vec<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub top_p: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_tokens: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stop: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub response_format: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<Vec<Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_choice: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seed: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub frequency_penalty: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub presence_penalty: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub n: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub logprobs: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub top_logprobs: Option<i64>,
}

/// Streaming variant — adds stream + stream_options fields.
#[derive(Serialize)]
pub struct StreamingChatCompletionRequest {
    #[serde(flatten)]
    pub base: ChatCompletionRequest,
    pub stream: bool,
    pub stream_options: Value,
}

// =============================================================================
// Response types
// =============================================================================

/// OpenAI chat completion response.
#[derive(Deserialize)]
pub struct ChatCompletionResponse {
    pub choices: Vec<Choice>,
    pub usage: Option<Usage>,
    pub model: String,
}

#[derive(Deserialize)]
pub struct Choice {
    pub message: ResponseMessage,
    pub finish_reason: Option<String>,
    pub logprobs: Option<Value>,
}

#[derive(Deserialize)]
pub struct ResponseMessage {
    pub role: String,
    pub content: Option<String>,
    pub tool_calls: Option<Vec<Value>>,
    #[serde(alias = "reasoning_content")]
    pub thinking_content: Option<String>,
    pub refusal: Option<String>,
}

#[derive(Deserialize)]
pub struct Usage {
    pub prompt_tokens: i64,
    pub completion_tokens: i64,
    pub total_tokens: i64,
    #[serde(default)]
    pub cache_creation_input_tokens: Option<i64>,
    #[serde(default)]
    pub cache_read_input_tokens: Option<i64>,
}

// =============================================================================
// Builders
// =============================================================================

/// Build a ChatCompletionRequest from op inputs.
pub fn build_chat_request(model: &str, inputs: &Value) -> ChatCompletionRequest {
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

/// Build a streaming request from a base chat request.
pub fn build_streaming_request(model: &str, inputs: &Value) -> StreamingChatCompletionRequest {
    StreamingChatCompletionRequest {
        base: build_chat_request(model, inputs),
        stream: true,
        stream_options: json!({"include_usage": true}),
    }
}

// =============================================================================
// Response formatting
// =============================================================================

/// Format a ChatCompletionResponse into the flat output dict.
pub fn format_completion_response(resp: ChatCompletionResponse) -> Value {
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
pub fn format_batch_result(body: &Value) -> Value {
    if let Ok(resp) = serde_json::from_value::<ChatCompletionResponse>(body.clone()) {
        return format_completion_response(resp);
    }
    let mut result = body.clone();
    if result.get("content").is_none() {
        result["content"] = json!("");
    }
    if result.get("role").is_none() {
        result["role"] = json!("assistant");
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // =========================================================================
    // build_chat_request tests
    // =========================================================================

    #[test]
    fn test_build_chat_request_basic() {
        let inputs = json!({
            "messages": [
                {"role": "user", "content": "Hello"}
            ]
        });

        let req = build_chat_request("gpt-4o", &inputs);
        assert_eq!(req.model, "gpt-4o");
        assert_eq!(req.messages.len(), 1);
        assert_eq!(req.messages[0]["content"], "Hello");
        assert!(req.temperature.is_none());
        assert!(req.max_tokens.is_none());
        assert!(req.tools.is_none());
    }

    #[test]
    fn test_build_chat_request_with_params() {
        let inputs = json!({
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 0.7,
            "max_tokens": 100,
            "top_p": 0.9,
            "seed": 42,
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
            "n": 2,
            "logprobs": true,
            "top_logprobs": 5
        });

        let req = build_chat_request("gpt-4o", &inputs);
        assert_eq!(req.temperature, Some(0.7));
        assert_eq!(req.max_tokens, Some(100));
        assert_eq!(req.top_p, Some(0.9));
        assert_eq!(req.seed, Some(42));
        assert_eq!(req.frequency_penalty, Some(0.5));
        assert_eq!(req.presence_penalty, Some(0.3));
        assert_eq!(req.n, Some(2));
        assert_eq!(req.logprobs, Some(true));
        assert_eq!(req.top_logprobs, Some(5));
    }

    #[test]
    fn test_build_chat_request_with_tools() {
        let inputs = json!({
            "messages": [{"role": "user", "content": "What's the weather?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "parameters": {"type": "object"}
                    }
                }
            ],
            "tool_choice": "auto"
        });

        let req = build_chat_request("gpt-4o", &inputs);
        assert!(req.tools.is_some());
        assert_eq!(req.tools.as_ref().unwrap().len(), 1);
        assert_eq!(req.tool_choice, Some(json!("auto")));
    }

    #[test]
    fn test_build_chat_request_empty_messages() {
        let inputs = json!({});
        let req = build_chat_request("gpt-4o", &inputs);
        assert!(req.messages.is_empty());
    }

    #[test]
    fn test_build_chat_request_with_response_format() {
        let inputs = json!({
            "messages": [{"role": "user", "content": "Give JSON"}],
            "response_format": {"type": "json_object"}
        });

        let req = build_chat_request("gpt-4o", &inputs);
        assert_eq!(req.response_format, Some(json!({"type": "json_object"})));
    }

    #[test]
    fn test_build_chat_request_with_stop() {
        let inputs = json!({
            "messages": [{"role": "user", "content": "Hi"}],
            "stop": ["\n", "END"]
        });

        let req = build_chat_request("gpt-4o", &inputs);
        assert_eq!(req.stop, Some(json!(["\n", "END"])));
    }

    // =========================================================================
    // build_streaming_request tests
    // =========================================================================

    #[test]
    fn test_build_streaming_request() {
        let inputs = json!({
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.5
        });

        let req = build_streaming_request("gpt-4o", &inputs);
        assert!(req.stream);
        assert_eq!(req.stream_options, json!({"include_usage": true}));
        assert_eq!(req.base.model, "gpt-4o");
        assert_eq!(req.base.temperature, Some(0.5));
    }

    // =========================================================================
    // Serialization tests
    // =========================================================================

    #[test]
    fn test_chat_request_serialization_skips_none() {
        let inputs = json!({
            "messages": [{"role": "user", "content": "Hi"}]
        });

        let req = build_chat_request("gpt-4o", &inputs);
        let serialized = serde_json::to_value(&req).unwrap();

        // None fields should be absent
        assert!(serialized.get("temperature").is_none());
        assert!(serialized.get("max_tokens").is_none());
        assert!(serialized.get("tools").is_none());
        assert!(serialized.get("tool_choice").is_none());
        assert!(serialized.get("seed").is_none());

        // Required fields should be present
        assert_eq!(serialized["model"], "gpt-4o");
        assert!(serialized["messages"].is_array());
    }

    #[test]
    fn test_chat_request_serialization_includes_set_fields() {
        let inputs = json!({
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 0.7,
            "max_tokens": 100
        });

        let req = build_chat_request("gpt-4o", &inputs);
        let serialized = serde_json::to_value(&req).unwrap();

        assert_eq!(serialized["temperature"], 0.7);
        assert_eq!(serialized["max_tokens"], 100);
    }

    // =========================================================================
    // format_completion_response tests
    // =========================================================================

    #[test]
    fn test_format_completion_response_basic() {
        let resp = ChatCompletionResponse {
            choices: vec![Choice {
                message: ResponseMessage {
                    role: "assistant".to_string(),
                    content: Some("Hello!".to_string()),
                    tool_calls: None,
                    thinking_content: None,
                    refusal: None,
                },
                finish_reason: Some("stop".to_string()),
                logprobs: None,
            }],
            usage: Some(Usage {
                prompt_tokens: 10,
                completion_tokens: 5,
                total_tokens: 15,
                cache_creation_input_tokens: None,
                cache_read_input_tokens: None,
            }),
            model: "gpt-4o-2024-01-01".to_string(),
        };

        let result = format_completion_response(resp);
        assert_eq!(result["content"], "Hello!");
        assert_eq!(result["role"], "assistant");
        assert_eq!(result["finish_reason"], "stop");
        assert_eq!(result["model_used"], "gpt-4o-2024-01-01");
        assert_eq!(result["tokens_used"]["prompt_tokens"], 10);
        assert_eq!(result["tokens_used"]["completion_tokens"], 5);
        assert_eq!(result["tokens_used"]["total_tokens"], 15);
    }

    #[test]
    fn test_format_completion_response_with_tool_calls() {
        let resp = ChatCompletionResponse {
            choices: vec![Choice {
                message: ResponseMessage {
                    role: "assistant".to_string(),
                    content: None,
                    tool_calls: Some(vec![json!({
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "{\"city\": \"London\"}"
                        }
                    })]),
                    thinking_content: None,
                    refusal: None,
                },
                finish_reason: Some("tool_calls".to_string()),
                logprobs: None,
            }],
            usage: None,
            model: "gpt-4o".to_string(),
        };

        let result = format_completion_response(resp);
        assert_eq!(result["content"], ""); // content is None → default ""
        assert_eq!(result["finish_reason"], "tool_calls");
        assert_eq!(result["tool_calls"].as_array().unwrap().len(), 1);
        assert_eq!(result["tokens_used"], json!({}));
    }

    #[test]
    fn test_format_completion_response_empty_choices() {
        let resp = ChatCompletionResponse {
            choices: vec![],
            usage: None,
            model: "gpt-4o".to_string(),
        };

        let result = format_completion_response(resp);
        assert_eq!(result["content"], "");
        assert_eq!(result["role"], "assistant");
        assert_eq!(result["finish_reason"], "stop");
    }

    #[test]
    fn test_format_completion_response_with_cache_tokens() {
        let resp = ChatCompletionResponse {
            choices: vec![Choice {
                message: ResponseMessage {
                    role: "assistant".to_string(),
                    content: Some("Hi".to_string()),
                    tool_calls: None,
                    thinking_content: None,
                    refusal: None,
                },
                finish_reason: Some("stop".to_string()),
                logprobs: None,
            }],
            usage: Some(Usage {
                prompt_tokens: 100,
                completion_tokens: 10,
                total_tokens: 110,
                cache_creation_input_tokens: Some(50),
                cache_read_input_tokens: Some(30),
            }),
            model: "gpt-4o".to_string(),
        };

        let result = format_completion_response(resp);
        assert_eq!(result["tokens_used"]["cache_creation_input_tokens"], 50);
        assert_eq!(result["tokens_used"]["cache_read_input_tokens"], 30);
    }

    #[test]
    fn test_format_completion_response_with_thinking() {
        let resp = ChatCompletionResponse {
            choices: vec![Choice {
                message: ResponseMessage {
                    role: "assistant".to_string(),
                    content: Some("Final answer".to_string()),
                    tool_calls: None,
                    thinking_content: Some("Let me think...".to_string()),
                    refusal: None,
                },
                finish_reason: Some("stop".to_string()),
                logprobs: None,
            }],
            usage: None,
            model: "o1".to_string(),
        };

        let result = format_completion_response(resp);
        assert_eq!(result["thinking_content"], "Let me think...");
    }

    #[test]
    fn test_format_completion_response_with_refusal() {
        let resp = ChatCompletionResponse {
            choices: vec![Choice {
                message: ResponseMessage {
                    role: "assistant".to_string(),
                    content: None,
                    tool_calls: None,
                    thinking_content: None,
                    refusal: Some("I cannot help with that.".to_string()),
                },
                finish_reason: Some("stop".to_string()),
                logprobs: None,
            }],
            usage: None,
            model: "gpt-4o".to_string(),
        };

        let result = format_completion_response(resp);
        assert_eq!(result["refusal"], "I cannot help with that.");
    }

    // =========================================================================
    // format_batch_result tests
    // =========================================================================

    #[test]
    fn test_format_batch_result_valid_response() {
        let body = json!({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Batch response"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "total_tokens": 8
            },
            "model": "gpt-4o"
        });

        let result = format_batch_result(&body);
        assert_eq!(result["content"], "Batch response");
        assert_eq!(result["model_used"], "gpt-4o");
    }

    #[test]
    fn test_format_batch_result_non_standard() {
        // Non-standard body gets defaults added
        let body = json!({"some_field": "value"});
        let result = format_batch_result(&body);
        assert_eq!(result["content"], "");
        assert_eq!(result["role"], "assistant");
        assert_eq!(result["some_field"], "value");
    }

    #[test]
    fn test_format_batch_result_preserves_existing() {
        let body = json!({"content": "existing", "role": "user"});
        let result = format_batch_result(&body);
        assert_eq!(result["content"], "existing");
        assert_eq!(result["role"], "user");
    }

    // =========================================================================
    // Response deserialization tests
    // =========================================================================

    #[test]
    fn test_response_deserialization() {
        let raw = json!({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "total_tokens": 11
            },
            "model": "gpt-4o"
        });

        let resp: ChatCompletionResponse = serde_json::from_value(raw).unwrap();
        assert_eq!(resp.model, "gpt-4o");
        assert_eq!(resp.choices.len(), 1);
        assert_eq!(resp.choices[0].message.content, Some("Hello".to_string()));
    }

    #[test]
    fn test_response_deserialization_reasoning_content_alias() {
        // The `reasoning_content` field should deserialize into `thinking_content`
        let raw = json!({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning_content": "thinking"
                },
                "finish_reason": "stop"
            }],
            "model": "o1"
        });

        let resp: ChatCompletionResponse = serde_json::from_value(raw).unwrap();
        assert_eq!(
            resp.choices[0].message.thinking_content,
            Some("thinking".to_string())
        );
    }
}
