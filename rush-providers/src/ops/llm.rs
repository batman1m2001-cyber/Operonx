//! LLM op — load balancing, fallback chains, batch mode, auth.
//!
//! Mirrors hush-providers/hush/providers/ops/llm.py:
//! - Weighted random selection across multiple backends
//! - Sequential fallback chain on failure
//! - OpenAI Batch API integration
//! - Keycloak / Google service account auth
//! - Cost tracking

use std::sync::mpsc::Sender;
use std::time::Instant;

use rand::distributions::WeightedIndex;
use rand::prelude::*;
use serde_json::{json, Value};

use crate::auth::google::{GoogleServiceAccountConfig, GoogleTokenProvider};
use crate::batch::coordinator::{BatchConfig, BatchCoordinator};
use crate::config::llm::LLMConfig;
use crate::config::LLMProviderConfig;
use crate::http::{ProviderError, ProviderResult};
use crate::llms;

/// Execute an LLM op with full load balancing, fallback, and batch support.
pub async fn execute(inputs: Value, config: &LLMProviderConfig) -> ProviderResult<Value> {
    if config.configs.is_empty() {
        return Err(ProviderError {
            message: "LLM op has no backend configs".to_string(),
            status_code: None,
            error_code: None,
        });
    }

    if config.batch_mode {
        return execute_batch(&config.configs[0], &inputs).await;
    }

    let start_time = Instant::now();
    let (_idx, selected_config, selected_resource) = select_primary(config);

    match execute_single(selected_config, &inputs).await {
        Ok(mut result) => {
            add_metadata(&mut result, selected_config, &selected_resource, start_time);
            Ok(result)
        }
        Err(primary_error) => {
            try_fallbacks(config, &inputs, primary_error, start_time).await
        }
    }
}

/// Execute an LLM op in streaming mode with load balancing and fallback.
pub async fn execute_streaming(
    inputs: Value,
    config: &LLMProviderConfig,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    if config.configs.is_empty() {
        return Err(ProviderError {
            message: "LLM op has no backend configs".to_string(),
            status_code: None,
            error_code: None,
        });
    }

    if config.batch_mode {
        return Err(ProviderError {
            message: "Streaming is not supported in batch mode".to_string(),
            status_code: None,
            error_code: None,
        });
    }

    let start_time = Instant::now();
    let (_idx, selected_config, selected_resource) = select_primary(config);

    match execute_single_streaming(selected_config, &inputs, chunk_tx).await {
        Ok(mut result) => {
            add_metadata(&mut result, selected_config, &selected_resource, start_time);
            Ok(result)
        }
        Err(primary_error) => {
            // Fallback chain uses non-streaming (chunk_tx already consumed)
            try_fallbacks(config, &inputs, primary_error, start_time).await
        }
    }
}

// =============================================================================
// Backend selection and fallback
// =============================================================================

/// Select the primary backend using weighted random selection.
/// Returns (index, config, resource_name).
fn select_primary(config: &LLMProviderConfig) -> (usize, &LLMConfig, String) {
    let idx = select_backend(&config.ratios);
    let cfg = &config.configs[idx];
    let resource = config.resources.get(idx).cloned().unwrap_or_default();
    (idx, cfg, resource)
}

/// Try the fallback chain after primary failure.
async fn try_fallbacks(
    config: &LLMProviderConfig,
    inputs: &Value,
    primary_error: ProviderError,
    start_time: Instant,
) -> ProviderResult<Value> {
    if config.fallback_configs.is_empty() {
        return Err(primary_error);
    }

    for (i, fallback_config) in config.fallback_configs.iter().enumerate() {
        let fallback_key = config
            .fallback
            .get(i)
            .cloned()
            .unwrap_or_else(|| format!("fallback_{}", i));

        match execute_single(fallback_config, inputs).await {
            Ok(mut result) => {
                add_metadata(&mut result, fallback_config, &fallback_key, start_time);
                return Ok(result);
            }
            Err(_) => continue,
        }
    }

    Err(ProviderError {
        message: format!(
            "Primary and all {} fallbacks failed. Primary error: {}",
            config.fallback_configs.len(),
            primary_error
        ),
        status_code: primary_error.status_code,
        error_code: primary_error.error_code,
    })
}

// =============================================================================
// Single call helpers
// =============================================================================

/// Execute a single LLM call (no load balancing / fallback).
async fn execute_single(config: &LLMConfig, inputs: &Value) -> ProviderResult<Value> {
    let access_token = get_access_token(config).await?;
    llms::chat_completion(config, inputs, access_token.as_deref()).await
}

/// Execute a single streaming LLM call.
async fn execute_single_streaming(
    config: &LLMConfig,
    inputs: &Value,
    chunk_tx: Sender<Value>,
) -> ProviderResult<Value> {
    let access_token = get_access_token(config).await?;
    llms::chat_completion_stream(config, inputs, access_token.as_deref(), chunk_tx).await
}

/// Execute in batch mode (OpenAI Batch API).
async fn execute_batch(config: &LLMConfig, inputs: &Value) -> ProviderResult<Value> {
    let openai_config = match config {
        LLMConfig::OpenAI(c) => c,
        _ => {
            return Err(ProviderError {
                message: "Batch mode is only supported for OpenAI/vLLM backends".to_string(),
                status_code: None,
                error_code: None,
            })
        }
    };

    let batch_config = BatchConfig::from_openai_config(openai_config);
    let coordinator = BatchCoordinator::new(batch_config);

    let messages = inputs.get("messages").cloned().unwrap_or(json!([]));
    let mut params = json!({});
    if let Some(obj) = inputs.as_object() {
        for (k, v) in obj {
            if k != "messages" {
                params[k] = v.clone();
            }
        }
    }

    coordinator.submit(messages, params).await
}

// =============================================================================
// Auth and metadata
// =============================================================================

/// Get an access token if the backend requires it (Gemini, Keycloak).
async fn get_access_token(config: &LLMConfig) -> ProviderResult<Option<String>> {
    match config {
        LLMConfig::Gemini(gemini_config) => {
            let sa_config = GoogleServiceAccountConfig::from_gemini_config(gemini_config);
            let provider = GoogleTokenProvider::new(sa_config);
            let token = provider.get_token().await?;
            Ok(Some(token))
        }
        _ => Ok(None),
    }
}

/// Select a backend index using weighted random selection.
fn select_backend(ratios: &[f64]) -> usize {
    if ratios.len() <= 1 {
        return 0;
    }

    let dist = match WeightedIndex::new(ratios) {
        Ok(d) => d,
        Err(_) => return 0,
    };
    let mut rng = thread_rng();
    dist.sample(&mut rng)
}

/// Add metadata to the LLM result (cost, duration, resource info).
fn add_metadata(result: &mut Value, config: &LLMConfig, resource: &str, start_time: Instant) {
    let duration_ms = start_time.elapsed().as_secs_f64() * 1000.0;
    result["duration_ms"] = json!(duration_ms);

    let base = match config {
        LLMConfig::OpenAI(c) => &c.base,
        LLMConfig::Azure(c) => &c.base,
        LLMConfig::Gemini(c) => &c.base,
    };

    if let (Some(cost_in), Some(cost_out)) = (
        base.cost_per_input_token,
        base.cost_per_output_token,
    ) {
        if let Some(tokens) = result.get("tokens_used") {
            let input_tokens = tokens
                .get("prompt_tokens")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0);
            let output_tokens = tokens
                .get("completion_tokens")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0);
            let cost_usd = input_tokens * cost_in + output_tokens * cost_out;
            result["cost_usd"] = json!(cost_usd);
        }
    }

    if let Some(messages) = result.get("tokens_used").and_then(|t| t.get("prompt_tokens")) {
        result["context_used"] = messages.clone();
    }

    let _ = resource;
}
