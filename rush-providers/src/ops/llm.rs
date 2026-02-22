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

    let start_time = Instant::now();

    // Batch mode — queue request for batch processing
    if config.batch_mode {
        return execute_batch(&config.configs[0], &inputs).await;
    }

    // Select primary backend (load balancing with weighted random)
    let selected_idx = select_backend(&config.ratios);
    let selected_config = &config.configs[selected_idx];
    let selected_resource = config
        .resources
        .get(selected_idx)
        .cloned()
        .unwrap_or_default();

    // Try primary
    match execute_single(selected_config, &inputs).await {
        Ok(mut result) => {
            // Add metadata
            let duration_ms = start_time.elapsed().as_secs_f64() * 1000.0;
            add_metadata(&mut result, selected_config, &selected_resource, duration_ms);
            Ok(result)
        }
        Err(primary_error) => {
            // Try fallback chain
            if !config.fallback_configs.is_empty() {
                for (i, fallback_config) in config.fallback_configs.iter().enumerate() {
                    let fallback_key = config
                        .fallback
                        .get(i)
                        .cloned()
                        .unwrap_or_else(|| format!("fallback_{}", i));

                    match execute_single(fallback_config, &inputs).await {
                        Ok(mut result) => {
                            let duration_ms = start_time.elapsed().as_secs_f64() * 1000.0;
                            add_metadata(
                                &mut result,
                                fallback_config,
                                &fallback_key,
                                duration_ms,
                            );
                            return Ok(result);
                        }
                        Err(_) => continue,
                    }
                }
                // All fallbacks failed
                Err(ProviderError {
                    message: format!(
                        "Primary and all {} fallbacks failed. Primary error: {}",
                        config.fallback_configs.len(),
                        primary_error
                    ),
                    status_code: primary_error.status_code,
                    error_code: primary_error.error_code,
                })
            } else {
                Err(primary_error)
            }
        }
    }
}

/// Execute a single LLM call (no load balancing / fallback).
async fn execute_single(config: &LLMConfig, inputs: &Value) -> ProviderResult<Value> {
    // Get access token if needed (Gemini → Google OAuth2, or Keycloak)
    let access_token = get_access_token(config).await?;
    llms::chat_completion(config, inputs, access_token.as_deref()).await
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
    // Create a per-request coordinator (in production, should be shared per resource)
    let coordinator = BatchCoordinator::new(batch_config);

    let messages = inputs.get("messages").cloned().unwrap_or(json!([]));
    let mut params = json!({});
    // Copy non-message params
    if let Some(obj) = inputs.as_object() {
        for (k, v) in obj {
            if k != "messages" {
                params[k] = v.clone();
            }
        }
    }

    coordinator.submit(messages, params).await
}

/// Get an access token if the backend requires it (Gemini, Keycloak).
async fn get_access_token(config: &LLMConfig) -> ProviderResult<Option<String>> {
    match config {
        LLMConfig::Gemini(gemini_config) => {
            // Google service account → OAuth2 access token
            let sa_config = GoogleServiceAccountConfig::from_gemini_config(gemini_config);
            let provider = GoogleTokenProvider::new(sa_config);
            let token = provider.get_token().await?;
            Ok(Some(token))
        }
        // OpenAI/Azure use api_key directly (handled in HTTP layer)
        // Keycloak tokens are injected via serialized config (pre-resolved)
        // or managed externally
        _ => Ok(None),
    }
}

/// Select a backend index using weighted random selection.
/// Mirrors Python's `random.choices(llms, weights=ratios, k=1)`.
fn select_backend(ratios: &[f64]) -> usize {
    if ratios.len() <= 1 {
        return 0;
    }

    let dist = match WeightedIndex::new(ratios) {
        Ok(d) => d,
        Err(_) => return 0, // Fallback to first if weights invalid
    };
    let mut rng = thread_rng();
    dist.sample(&mut rng)
}

/// Add metadata to the LLM result (cost, duration, resource info).
fn add_metadata(result: &mut Value, config: &LLMConfig, resource: &str, duration_ms: f64) {
    result["duration_ms"] = json!(duration_ms);

    // Calculate cost
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

    // Context used (rough estimate from messages length)
    if let Some(messages) = result.get("tokens_used").and_then(|t| t.get("prompt_tokens")) {
        result["context_used"] = messages.clone();
    }

    let _ = resource; // Resource key stored by caller in state metadata
}

// =============================================================================
// Streaming execution
// =============================================================================

/// Execute an LLM op in streaming mode with load balancing and fallback.
///
/// Same as `execute()` but uses streaming HTTP and sends each SSE chunk
/// through `chunk_tx`. Returns the accumulated final response.
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

    let start_time = Instant::now();

    // Batch mode does not support streaming
    if config.batch_mode {
        return Err(ProviderError {
            message: "Streaming is not supported in batch mode".to_string(),
            status_code: None,
            error_code: None,
        });
    }

    // Select primary backend (load balancing with weighted random)
    let selected_idx = select_backend(&config.ratios);
    let selected_config = &config.configs[selected_idx];
    let selected_resource = config
        .resources
        .get(selected_idx)
        .cloned()
        .unwrap_or_default();

    // Try primary with streaming
    match execute_single_streaming(selected_config, &inputs, chunk_tx).await {
        Ok(mut result) => {
            let duration_ms = start_time.elapsed().as_secs_f64() * 1000.0;
            add_metadata(&mut result, selected_config, &selected_resource, duration_ms);
            Ok(result)
        }
        Err(primary_error) => {
            // Fallback chain (no streaming for fallbacks — we already consumed chunk_tx)
            // Python's _handle_streaming also only streams from the selected LLM
            if !config.fallback_configs.is_empty() {
                for (i, fallback_config) in config.fallback_configs.iter().enumerate() {
                    let fallback_key = config
                        .fallback
                        .get(i)
                        .cloned()
                        .unwrap_or_else(|| format!("fallback_{}", i));

                    match execute_single(fallback_config, &inputs).await {
                        Ok(mut result) => {
                            let duration_ms = start_time.elapsed().as_secs_f64() * 1000.0;
                            add_metadata(
                                &mut result,
                                fallback_config,
                                &fallback_key,
                                duration_ms,
                            );
                            return Ok(result);
                        }
                        Err(_) => continue,
                    }
                }
                Err(ProviderError {
                    message: format!(
                        "Primary streaming and all {} fallbacks failed. Primary error: {}",
                        config.fallback_configs.len(),
                        primary_error
                    ),
                    status_code: primary_error.status_code,
                    error_code: primary_error.error_code,
                })
            } else {
                Err(primary_error)
            }
        }
    }
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
