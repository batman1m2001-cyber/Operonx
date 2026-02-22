//! ChainOp — prompt template formatting + LLM call in a single op.
//!
//! Mirrors hush-providers/hush/providers/ops/chain.py.
//! Combines PromptOp (template → messages) with LLMOp (messages → content).

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

/// Execute a chain op: format template → call LLM → return merged result.
///
/// Inputs: {template, ...template_vars, conversation_history, tool_results, + LLM params}
/// Outputs: {messages, content, role, finish_reason, model_used, tokens_used, ...}
pub async fn execute(inputs: Value, config: &LLMProviderConfig) -> ProviderResult<Value> {
    // 1. Format prompt (reuse prompt op)
    let prompt_result = crate::ops::prompt::execute(inputs.clone())?;
    let messages = prompt_result
        .get("messages")
        .cloned()
        .unwrap_or(json!([]));

    // 2. Build LLM inputs: messages + completion params from original inputs
    let mut llm_inputs = json!({ "messages": messages });
    if let Some(obj) = inputs.as_object() {
        for &key in LLM_PARAM_KEYS {
            if let Some(v) = obj.get(key) {
                llm_inputs[key] = v.clone();
            }
        }
    }

    // 3. Execute LLM
    let mut result = crate::ops::llm::execute(llm_inputs, config).await?;

    // 4. Include the formatted messages in output
    result["messages"] = messages;

    Ok(result)
}

/// Execute a chain op in streaming mode.
pub async fn execute_streaming(
    inputs: Value,
    config: &LLMProviderConfig,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    // 1. Format prompt
    let prompt_result = crate::ops::prompt::execute(inputs.clone())?;
    let messages = prompt_result
        .get("messages")
        .cloned()
        .unwrap_or(json!([]));

    // 2. Build LLM inputs
    let mut llm_inputs = json!({ "messages": messages });
    if let Some(obj) = inputs.as_object() {
        for &key in LLM_PARAM_KEYS {
            if let Some(v) = obj.get(key) {
                llm_inputs[key] = v.clone();
            }
        }
    }

    // 3. Execute LLM with streaming
    let mut result = crate::ops::llm::execute_streaming(llm_inputs, config, chunk_tx).await?;

    // 4. Include the formatted messages in output
    result["messages"] = messages;

    Ok(result)
}
