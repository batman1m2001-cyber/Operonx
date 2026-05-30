//! `LLMOp` — language-model op.
//!
//! Mirrors Python [`operonx/providers/ops/llm.py`](../../../../../operonx/providers/ops/llm.py).
//! Features:
//! - Single-model or weighted load-balancing across multiple resources.
//! - Ordered fallback chain on primary failure (per §5b.5).
//! - Batch-mode dispatch (stubbed — Phase 5b).
//! - Streaming stubbed — Phase 6 follow-up wires it into `ExecutionHandle`.
//!
//! # Phase 6 scope
//! Non-streaming `generate()` path with multi-model selection + fallback.
//! `batch_mode=true` errors with a clear Phase 5b message. Streaming kind
//! (`stream=true` on the op) errors for now.

use std::sync::Arc;

use rand::seq::SliceRandom;
use rand::{rngs::StdRng, SeedableRng};
use serde_json::{json, Map, Value};
use tracing::{info, warn};

use super::utils::resolve_llm;
use crate::core::configs::op_config::OpConfig;
use crate::core::exceptions::OperonError;
use crate::providers::llms::{BaseLLM, ChatCompletion, CompletionChoice, LlmOpts, Message, Usage};

/// Execute an LLM op: resolve backends, select one, call `generate()` or
/// `stream()` (accumulated to a final completion), fall back on error.
///
/// **Streaming behavior:** when `op.stream == true` the executor calls
/// `BaseLLM::stream()` and concatenates the deltas into a single content
/// string. This matches the Python semantics of the non-streaming output
/// shape while exercising the streaming HTTP path (the SSE parsing,
/// reconnection, and error handling are still validated end-to-end).
///
/// Per-chunk frame emission to `ExecutionHandle` lands in a follow-up that
/// adds streaming-aware dispatch to the scheduler — for now the wire-level
/// streaming path is verified, the per-frame fanout is the future delta.
pub async fn execute(op: &OpConfig, inputs: Map<String, Value>) -> Result<Value, OperonError> {
    if op.batch_mode {
        return Err(OperonError::Provider(
            "LLMOp batch_mode not yet implemented (Phase 5b — BatchCoordinator)".into(),
        ));
    }

    let resources = op.resource_keys();
    if resources.is_empty() {
        return Err(OperonError::Config(format!(
            "LLMOp '{}' missing `resource`",
            op.full_name
        )));
    }

    let messages = parse_messages(inputs.get("messages"))?;
    let opts = build_opts(&inputs);

    // Resolve primaries.
    let mut llms: Vec<(String, Arc<dyn BaseLLM>)> = Vec::with_capacity(resources.len());
    for key in &resources {
        let llm = resolve_llm(key)?;
        llms.push((key.clone(), llm));
    }

    let (selected_key, selected_llm) = select_llm(&llms, op.ratios.as_deref());

    let stream_mode = op.stream;
    let primary_result = if stream_mode {
        run_streaming(&selected_llm, messages.clone(), &opts).await
    } else {
        selected_llm.generate(messages.clone(), &opts).await
    };

    match primary_result {
        Ok(completion) => Ok(completion_to_output(&completion, &selected_key)),
        Err(primary_err) => {
            let fallback_keys = op.fallback.clone().unwrap_or_default();
            if fallback_keys.is_empty() {
                return Err(primary_err);
            }
            warn!(
                "LLMOp '{}' primary '{}' failed: {}. Falling back…",
                op.full_name, selected_key, primary_err
            );
            run_fallback(&op.full_name, &fallback_keys, messages, &opts, stream_mode).await
        }
    }
}

/// Open a streaming completion and accumulate the deltas into a single
/// `ChatCompletion`. The final shape matches what `generate()` would
/// return for the same prompt, so downstream LLMOp processing (output
/// extraction, fallback handling) is identical regardless of stream mode.
async fn run_streaming(
    llm: &Arc<dyn BaseLLM>,
    messages: Vec<Message>,
    opts: &LlmOpts,
) -> Result<ChatCompletion, OperonError> {
    use futures::StreamExt;
    let mut stream = llm.stream(messages, opts).await?;
    let mut content = String::new();
    let mut role = String::from("assistant");
    let mut finish_reason: Option<String> = None;
    let mut id = String::new();
    let mut model = String::new();
    while let Some(chunk_result) = stream.next().await {
        let chunk = chunk_result?;
        if id.is_empty() {
            id = chunk.id.clone();
        }
        if model.is_empty() {
            model = chunk.model.clone();
        }
        if let Some(c) = chunk.choices.first() {
            if let Some(delta_role) = c.delta.get("role").and_then(|v| v.as_str()) {
                role = delta_role.to_string();
            }
            if let Some(delta_content) = c.delta.get("content").and_then(|v| v.as_str()) {
                content.push_str(delta_content);
            }
            if let Some(fr) = &c.finish_reason {
                finish_reason = Some(fr.clone());
            }
        }
    }
    Ok(ChatCompletion {
        id,
        object: "chat.completion".into(),
        created: chrono::Utc::now().timestamp(),
        model,
        choices: vec![CompletionChoice {
            index: 0,
            message: Some(Message {
                role,
                content: Value::String(content),
                name: None,
                tool_call_id: None,
                extras: Default::default(),
            }),
            finish_reason,
            extras: Default::default(),
        }],
        usage: None,
        extras: Default::default(),
    })
}

async fn run_fallback(
    op_name: &str,
    fallback_keys: &[String],
    messages: Vec<Message>,
    opts: &LlmOpts,
    stream_mode: bool,
) -> Result<Value, OperonError> {
    let mut last_err: Option<OperonError> = None;
    for key in fallback_keys {
        let llm = match resolve_llm(key) {
            Ok(l) => l,
            Err(e) => {
                last_err = Some(e);
                continue;
            }
        };
        info!("LLMOp '{}' trying fallback '{}'", op_name, key);
        let result = if stream_mode {
            run_streaming(&llm, messages.clone(), opts).await
        } else {
            llm.generate(messages.clone(), opts).await
        };
        match result {
            Ok(completion) => {
                info!("LLMOp '{}' fallback '{}' succeeded", op_name, key);
                return Ok(completion_to_output(&completion, key));
            }
            Err(e) => {
                warn!("LLMOp '{}' fallback '{}' failed: {}", op_name, key, e);
                last_err = Some(e);
            }
        }
    }
    Err(last_err.unwrap_or_else(|| {
        OperonError::Provider(format!(
            "LLMOp '{}' all fallbacks failed (none provided)",
            op_name
        ))
    }))
}

fn select_llm<'a>(
    llms: &'a [(String, Arc<dyn BaseLLM>)],
    ratios: Option<&[f32]>,
) -> (String, Arc<dyn BaseLLM>) {
    debug_assert!(!llms.is_empty());
    if llms.len() == 1 {
        let (k, l) = &llms[0];
        return (k.clone(), l.clone());
    }
    let mut rng = StdRng::from_entropy();
    let indices: Vec<usize> = (0..llms.len()).collect();

    if let Some(weights) = ratios {
        if weights.len() == llms.len() {
            // Simple weighted random — sample cumulative buckets.
            let total: f32 = weights.iter().sum();
            if total > 0.0 {
                use rand::Rng;
                let mut pick: f32 = rng.gen::<f32>() * total;
                for (i, &w) in weights.iter().enumerate() {
                    if pick < w {
                        let (k, l) = &llms[i];
                        return (k.clone(), l.clone());
                    }
                    pick -= w;
                }
            }
        }
    }

    // Uniform fallback.
    let idx = *indices.choose(&mut rng).expect("non-empty");
    let (k, l) = &llms[idx];
    (k.clone(), l.clone())
}

fn parse_messages(raw: Option<&Value>) -> Result<Vec<Message>, OperonError> {
    let arr = raw
        .and_then(|v| v.as_array())
        .ok_or_else(|| OperonError::Config("LLMOp: `messages` must be a list".into()))?;
    arr.iter()
        .map(|m| {
            serde_json::from_value::<Message>(m.clone())
                .map_err(|e| OperonError::Config(format!("LLMOp: invalid message: {}", e)))
        })
        .collect()
}

fn build_opts(inputs: &Map<String, Value>) -> LlmOpts {
    let mut opts = LlmOpts::default();
    opts.temperature = inputs
        .get("temperature")
        .and_then(|v| v.as_f64())
        .map(|v| v as f32);
    opts.top_p = inputs
        .get("top_p")
        .and_then(|v| v.as_f64())
        .map(|v| v as f32);
    opts.max_tokens = inputs
        .get("max_tokens")
        .and_then(|v| v.as_u64())
        .map(|v| v as u32);
    opts.frequency_penalty = inputs
        .get("frequency_penalty")
        .and_then(|v| v.as_f64())
        .map(|v| v as f32);
    opts.presence_penalty = inputs
        .get("presence_penalty")
        .and_then(|v| v.as_f64())
        .map(|v| v as f32);
    opts.n = inputs.get("n").and_then(|v| v.as_u64()).map(|v| v as u32);
    opts.stop = inputs.get("stop").and_then(|v| match v {
        Value::String(s) => Some(vec![s.clone()]),
        Value::Array(a) => Some(
            a.iter()
                .filter_map(|x| x.as_str().map(String::from))
                .collect(),
        ),
        _ => None,
    });
    opts.response_format = inputs.get("response_format").cloned();
    opts.tools = inputs.get("tools").cloned();
    // Anything non-standard rides in extras.
    for (k, v) in inputs {
        if !matches!(
            k.as_str(),
            "messages"
                | "temperature"
                | "top_p"
                | "max_tokens"
                | "tools"
                | "tool_choice"
                | "response_format"
                | "stop"
                | "frequency_penalty"
                | "presence_penalty"
                | "seed"
                | "logprobs"
                | "top_logprobs"
                | "n"
                | "user"
        ) {
            opts.extras.insert(k.clone(), v.clone());
        }
    }
    opts
}

/// Convert a [`ChatCompletion`] to Python-compatible output dict (keys match
/// `LLMOp.outputs` schema: role, content, finish_reason, model_used,
/// tool_calls, usage, extras).
fn completion_to_output(completion: &ChatCompletion, resource: &str) -> Value {
    let choice = completion
        .choices
        .first()
        .cloned()
        .unwrap_or(CompletionChoice {
            index: 0,
            message: None,
            finish_reason: None,
            extras: Default::default(),
        });

    let message = choice.message.unwrap_or(Message {
        role: "assistant".into(),
        content: Value::Null,
        name: None,
        tool_call_id: None,
        extras: Default::default(),
    });

    let content = match &message.content {
        Value::String(s) => Value::String(s.clone()),
        Value::Null => Value::String(String::new()),
        other => other.clone(),
    };

    let tool_calls = message
        .extras
        .get("tool_calls")
        .cloned()
        .unwrap_or(Value::Array(Vec::new()));
    let refusal = message
        .extras
        .get("refusal")
        .cloned()
        .unwrap_or(Value::Null);
    let thinking = message
        .extras
        .get("reasoning_content")
        .cloned()
        .unwrap_or(Value::Null);

    let usage_dict = normalize_usage(completion.usage.as_ref());

    json!({
        "role": message.role,
        "content": content,
        "finish_reason": choice.finish_reason,
        "model_used": if resource.is_empty() { &completion.model } else { resource },
        "tool_calls": tool_calls,
        "usage": usage_dict,
        "extras": {
            "thinking_content": thinking,
            "refusal": refusal,
            "logprobs": Value::Null,
        },
    })
}

fn normalize_usage(usage: Option<&Usage>) -> Value {
    let Some(u) = usage else {
        return json!({
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        });
    };
    let cached_tokens = u
        .prompt_tokens_details
        .as_ref()
        .and_then(|d| d.get("cached_tokens"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let cache_write_tokens = u
        .extras
        .get("cache_write_tokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let reasoning_tokens = u
        .extras
        .get("completion_tokens_details")
        .and_then(|d| d.get("reasoning_tokens"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    json!({
        "prompt_tokens": u.prompt_tokens,
        "completion_tokens": u.completion_tokens,
        "total_tokens": u.total_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
    })
}
