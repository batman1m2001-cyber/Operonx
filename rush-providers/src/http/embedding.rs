//! Embedding HTTP clients — OpenAI/vLLM (OpenAI-compatible) and TEI.
//!
//! Each function makes a single HTTP request and returns parsed embeddings.
//! No GIL needed.

use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::embedding::EmbeddingConfig;
use crate::http::{get_client, ProviderError, ProviderResult};

// =============================================================================
// Response types
// =============================================================================

/// OpenAI-compatible embedding response.
#[derive(Deserialize)]
struct OpenAIEmbeddingResponse {
    data: Vec<EmbeddingData>,
    usage: Option<EmbeddingUsage>,
    #[allow(dead_code)]
    model: Option<String>,
}

#[derive(Deserialize)]
struct EmbeddingData {
    #[allow(dead_code)]
    index: usize,
    embedding: Vec<f64>,
}

#[derive(Deserialize)]
struct EmbeddingUsage {
    prompt_tokens: Option<i64>,
    total_tokens: Option<i64>,
}

// =============================================================================
// Dispatch by api_type
// =============================================================================

/// Execute an embedding request against the appropriate backend.
pub async fn embed(config: &EmbeddingConfig, texts: &[String]) -> ProviderResult<Value> {
    match config.api_type.as_str() {
        "openai" | "vllm" | "azure" => openai_embed(config, texts).await,
        "tei" => tei_embed(config, texts).await,
        other => Err(ProviderError {
            message: format!(
                "Unsupported embedding api_type for native HTTP: '{}' (use Python fallback for hf/onnx)",
                other
            ),
            status_code: None,
            error_code: None,
        }),
    }
}

// =============================================================================
// OpenAI / vLLM / Azure (OpenAI-compatible)
// =============================================================================

async fn openai_embed(config: &EmbeddingConfig, texts: &[String]) -> ProviderResult<Value> {
    let client = get_client();

    let base_url = config
        .base_url
        .as_deref()
        .unwrap_or("https://api.openai.com/v1/embeddings");

    // Append /embeddings if not already in URL
    let url = if base_url.ends_with("/embeddings") {
        base_url.to_string()
    } else {
        format!("{}/embeddings", base_url.trim_end_matches('/'))
    };

    let mut body = json!({
        "input": texts,
    });
    if let Some(ref model) = config.model {
        body["model"] = json!(model);
    }
    if let Some(dims) = config.dimensions {
        body["dimensions"] = json!(dims);
    }

    let mut req = client.post(&url).header("Content-Type", "application/json");
    if let Some(ref api_key) = config.api_key {
        req = req.header("Authorization", format!("Bearer {}", api_key));
    }

    let resp = req.json(&body).send().await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Embedding API error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let parsed: OpenAIEmbeddingResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse embedding response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    // Sort by index and extract embeddings
    let mut data = parsed.data;
    data.sort_by_key(|d| d.index);
    let embeddings: Vec<Vec<f64>> = data.into_iter().map(|d| d.embedding).collect();

    let mut result = json!({ "embeddings": embeddings });
    if let Some(usage) = parsed.usage {
        result["tokens_used"] = json!({
            "prompt_tokens": usage.prompt_tokens.unwrap_or(0),
            "total_tokens": usage.total_tokens.unwrap_or(0),
        });
    }

    Ok(result)
}

// =============================================================================
// TEI (HuggingFace Text Embedding Inference)
// =============================================================================

async fn tei_embed(config: &EmbeddingConfig, texts: &[String]) -> ProviderResult<Value> {
    let client = get_client();

    let base_url = config
        .base_url
        .as_deref()
        .ok_or_else(|| ProviderError {
            message: "TEI embedding requires base_url".to_string(),
            status_code: None,
            error_code: None,
        })?;

    // TEI endpoint: {base_url}/embed (or just base_url if it already includes path)
    let url = if base_url.ends_with("/embed") {
        base_url.to_string()
    } else {
        format!("{}/embed", base_url.trim_end_matches('/'))
    };

    // TEI uses different format: {"inputs": [...]}
    let body = json!({ "inputs": texts });

    let mut req = client.post(&url).header("Content-Type", "application/json");
    if let Some(ref api_key) = config.api_key {
        req = req.header("Authorization", format!("Bearer {}", api_key));
    }

    let resp = req.json(&body).send().await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("TEI embedding error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    // TEI returns raw array: [[float, ...], [float, ...], ...]
    let embeddings: Vec<Vec<f64>> = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse TEI response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    Ok(json!({ "embeddings": embeddings }))
}
