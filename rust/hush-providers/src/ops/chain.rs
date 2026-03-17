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

/// If the LLM result has content and the inputs specify `parser_extract`,
/// run the content through ParserOp and merge parsed fields into the result.
///
/// Mirrors Python's chain() structured mode: Prompt → LLM → ParserOp.
fn maybe_parse(result: &mut Value, inputs: &Value) -> ProviderResult<()> {
    let extract = match inputs.get("parser_extract").and_then(|v| v.as_array()) {
        Some(arr) if !arr.is_empty() => arr.clone(),
        _ => return Ok(()),
    };

    let format = inputs
        .get("parser_format")
        .and_then(|v| v.as_str())
        .unwrap_or("xml");

    let content = result
        .get("content")
        .and_then(|v| v.as_str())
        .unwrap_or_default();

    if content.is_empty() {
        return Ok(());
    }

    let parser_inputs = json!({
        "text": content,
        "parser_format": format,
        "parser_extract": extract,
    });

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

/// Execute a chain op: format template → call LLM → optional parse → return merged result.
pub async fn execute(inputs: Value, config: &LLMProviderConfig) -> ProviderResult<Value> {
    let (llm_inputs, messages) = build_llm_inputs(&inputs)?;
    let mut result = crate::ops::llm::execute(llm_inputs, config).await?;
    result["messages"] = messages;
    maybe_parse(&mut result, &inputs)?;
    Ok(result)
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
