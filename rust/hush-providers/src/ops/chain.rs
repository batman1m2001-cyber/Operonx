//! ChainOp — prompt template formatting + LLM call + optional parsing.
//!
//! Mirrors hush-providers/hush/providers/ops/chain.py.
//! Combines PromptOp (template → messages) with LLMOp (messages → content),
//! and optionally ParserOp (content → structured fields) when `extract` is present.

use std::sync::mpsc::Sender;

use serde_json::{json, Value};

use crate::config::LLMProviderConfig;
use crate::http::ProviderResult;

/// LLM parameter keys that should be forwarded from chain inputs to LLM inputs.
const LLM_PARAM_KEYS: &[&str] = &[
    "temperature",
    "max_tokens",
    "top_p",
    "stop",
    "tools",
    "tool_choice",
    "response_format",
    "seed",
    "frequency_penalty",
    "presence_penalty",
    "n",
    "logprobs",
    "top_logprobs",
];

/// Build LLM inputs from chain inputs: format template → extract messages + LLM params.
///
/// Returns `(llm_inputs, messages)` where messages are also returned separately
/// to be included in the chain output.
fn build_llm_inputs(inputs: &Value) -> ProviderResult<(Value, Value)> {
    let prompt_result = crate::ops::prompt::execute(inputs.clone())?;
    let messages = prompt_result
        .get("messages")
        .cloned()
        .unwrap_or(json!([]));

    let mut llm_inputs = json!({ "messages": messages });
    if let Some(obj) = inputs.as_object() {
        for &key in LLM_PARAM_KEYS {
            if let Some(v) = obj.get(key) {
                llm_inputs[key] = v.clone();
            }
        }
    }

    Ok((llm_inputs, messages))
}

/// If the LLM result has content and the inputs specify `schema`,
/// run the content through ParserOp and merge parsed fields into the result.
///
/// Mirrors Python's chain() structured mode: Prompt → LLM → ParserOp.
fn maybe_parse(result: &mut Value, inputs: &Value) -> ProviderResult<()> {
    let schema = match inputs.get("schema").and_then(|v| v.as_array()) {
        Some(arr) if !arr.is_empty() => arr.clone(),
        _ => return Ok(()),
    };

    let mode = inputs
        .get("mode")
        .and_then(|v| v.as_str())
        .unwrap_or("xml");

    let content = result
        .get("content")
        .and_then(|v| v.as_str())
        .unwrap_or_default();

    if content.is_empty() {
        return Ok(());
    }

    let mut parser_inputs = json!({
        "text": content,
        "mode": mode,
        "schema": schema,
    });

    // Forward validators if present
    if let Some(validators) = inputs.get("validators") {
        parser_inputs["validators"] = validators.clone();
    }

    let parsed = hush_icore::ops::transform::parser_op::execute(parser_inputs)
        .map_err(|e| crate::http::ProviderError {
            message: format!("Parser error in chain: {}", e),
            status_code: None,
            error_code: None,
        })?;

    // Merge parsed fields into result
    if let Some(obj) = parsed.as_object() {
        for (k, v) in obj {
            result[k] = v.clone();
        }
    }

    Ok(())
}

/// Internal execute with injectable LLM call — enables testing with mock LLM.
async fn mock_execute<F, Fut>(
    inputs: Value,
    llm_fn: F,
) -> ProviderResult<Value>
where
    F: Fn(Value) -> Fut,
    Fut: std::future::Future<Output = ProviderResult<Value>>,
{
    let max_attempts = inputs.get("retry").and_then(|v| v.as_u64()).unwrap_or(0) + 1;
    let defaults = inputs.get("default").cloned();

    for attempt in 0..max_attempts {
        let (llm_inputs, messages) = build_llm_inputs(&inputs)?;
        let mut result = llm_fn(llm_inputs).await?;
        result["messages"] = messages;

        match maybe_parse(&mut result, &inputs) {
            Ok(_) => {
                result["error"] = Value::Null;
                return Ok(result);
            }
            Err(_) if attempt < max_attempts - 1 => {
                continue;
            }
            Err(e) => {
                result["error"] = Value::String(format!("{}", e));
                if let Some(Value::Object(defs)) = &defaults {
                    for (k, v) in defs {
                        result[k] = v.clone();
                    }
                }
                return Ok(result);
            }
        }
    }

    // Unreachable
    let (llm_inputs, messages) = build_llm_inputs(&inputs)?;
    let mut result = llm_fn(llm_inputs).await?;
    result["messages"] = messages;
    Ok(result)
}

/// Execute a chain op: format template → call LLM → optional parse → return merged result.
/// Supports retry on parse/validation failure via `retry` input key.
/// On exhausted retries, applies `default` values.
pub async fn execute(inputs: Value, config: &LLMProviderConfig) -> ProviderResult<Value> {
    mock_execute(inputs, |llm_inputs| {
        crate::ops::llm::execute(llm_inputs, config)
    }).await
}

/// Execute a chain op in streaming mode.
pub async fn execute_streaming(
    inputs: Value,
    config: &LLMProviderConfig,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let (llm_inputs, messages) = build_llm_inputs(&inputs)?;
    let mut result = crate::ops::llm::execute_streaming(llm_inputs, config, chunk_tx).await?;
    result["messages"] = messages;
    maybe_parse(&mut result, &inputs)?;
    Ok(result)
}

// =============================================================================
// Tests
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::sync::Arc;

    /// Helper: create mock LLM that returns responses in order.
    fn mock_llm(responses: Vec<Value>) -> impl Fn(Value) -> std::pin::Pin<Box<dyn std::future::Future<Output = ProviderResult<Value>> + Send>> {
        let call_count = Arc::new(AtomicU32::new(0));
        move |_inputs: Value| {
            let n = call_count.fetch_add(1, Ordering::SeqCst) as usize;
            let resp = responses[n.min(responses.len() - 1)].clone();
            Box::pin(async move { Ok(resp) })
        }
    }

    // ── Test 1: Validator reject → exhaust retries → default ────────

    #[tokio::test]
    async fn test_validator_reject_uses_default() {
        let inputs = json!({
            "template": "Classify: {text}",
            "text": "some input",
            "schema": ["result: str"],
            "mode": "xml",
            "validators": {"result": ["CONFIRM", "DENY", "FALLBACK"]},
            "retry": 2,
            "default": {"result": "FALLBACK"},
        });

        // LLM always returns UNKNOWN — validator rejects
        let llm = mock_llm(vec![
            json!({"content": "<result>UNKNOWN</result>", "role": "assistant", "model_used": "mock", "tokens_used": {}, "finish_reason": "stop", "tool_calls": []}),
            json!({"content": "<result>UNKNOWN</result>", "role": "assistant", "model_used": "mock", "tokens_used": {}, "finish_reason": "stop", "tool_calls": []}),
            json!({"content": "<result>UNKNOWN</result>", "role": "assistant", "model_used": "mock", "tokens_used": {}, "finish_reason": "stop", "tool_calls": []}),
        ]);

        let result = mock_execute(inputs, llm).await.unwrap();
        assert_eq!(result["result"], "FALLBACK");
        assert!(result["error"].is_string()); // has error message
    }

    // ── Test 2: Parse fail → retry → success ───────────────────────

    #[tokio::test]
    async fn test_parse_fail_then_success() {
        let inputs = json!({
            "template": "Classify: {text}",
            "text": "vâng đúng rồi",
            "schema": ["result: str"],
            "mode": "xml",
            "validators": {"result": ["CONFIRM", "DENY"]},
            "retry": 1,
            "default": {"result": "FALLBACK"},
        });

        let llm = mock_llm(vec![
            json!({"content": "garbage not xml", "role": "assistant", "model_used": "mock", "tokens_used": {}, "finish_reason": "stop", "tool_calls": []}),
            json!({"content": "<result>CONFIRM</result>", "role": "assistant", "model_used": "mock", "tokens_used": {}, "finish_reason": "stop", "tool_calls": []}),
        ]);

        let result = mock_execute(inputs, llm).await.unwrap();
        assert_eq!(result["result"], "CONFIRM");
        assert!(result["error"].is_null());
    }

    // ── Test 3: No retry → single attempt → default ────────────────

    #[tokio::test]
    async fn test_no_retry_uses_default() {
        let inputs = json!({
            "template": "Classify: {text}",
            "text": "test",
            "schema": ["result: str"],
            "mode": "xml",
            "validators": {"result": ["CONFIRM", "DENY", "FALLBACK"]},
            "retry": 0,
            "default": {"result": "FALLBACK"},
        });

        let llm = mock_llm(vec![
            json!({"content": "garbage", "role": "assistant", "model_used": "mock", "tokens_used": {}, "finish_reason": "stop", "tool_calls": []}),
        ]);

        let result = mock_execute(inputs, llm).await.unwrap();
        assert_eq!(result["result"], "FALLBACK");
        assert!(result["error"].is_string());
    }

    // ── Test 4: First attempt succeeds → no retry ──────────────────

    #[tokio::test]
    async fn test_success_no_retry_needed() {
        let inputs = json!({
            "template": "Classify: {text}",
            "text": "vâng",
            "schema": ["intent: str"],
            "mode": "xml",
            "validators": {"intent": ["CONFIRM", "DENY"]},
            "retry": 2,
        });

        let llm = mock_llm(vec![
            json!({"content": "<intent>CONFIRM</intent>", "role": "assistant", "model_used": "mock", "tokens_used": {}, "finish_reason": "stop", "tool_calls": []}),
        ]);

        let result = mock_execute(inputs, llm).await.unwrap();
        assert_eq!(result["intent"], "CONFIRM");
        assert!(result["error"].is_null());
    }

    // ── Test 5: Multiple fields ────────────────────────────────────

    #[tokio::test]
    async fn test_multiple_fields() {
        let inputs = json!({
            "template": "Analyze: {text}",
            "text": "không",
            "schema": ["intent: str", "confidence: float"],
            "mode": "xml",
        });

        let llm = mock_llm(vec![
            json!({"content": "<intent>DENY</intent><confidence>0.95</confidence>", "role": "assistant", "model_used": "mock", "tokens_used": {}, "finish_reason": "stop", "tool_calls": []}),
        ]);

        let result = mock_execute(inputs, llm).await.unwrap();
        assert_eq!(result["intent"], "DENY");
        assert_eq!(result["confidence"], 0.95);
    }
}
