//! OpenAI / vLLM / Azure embedding provider — mirrors Python's `embeddings/vllm.py`.

use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::embedding::EmbeddingConfig;
use crate::http::{get_client, ProviderError, ProviderResult};

/// OpenAI embedding provider struct.
pub struct OpenAIEmbedder<'a> {
    pub config: &'a EmbeddingConfig,
}

impl<'a> OpenAIEmbedder<'a> {
    pub fn new(config: &'a EmbeddingConfig) -> Self {
        OpenAIEmbedder { config }
    }
}

impl super::base::EmbedderProvider for OpenAIEmbedder<'_> {
    fn embed(&self, texts: &[String]) -> impl std::future::Future<Output = ProviderResult<Vec<Vec<f32>>>> + Send {
        let config = self.config;
        async move {
            let result = embed(config, texts).await?;
            // Extract embeddings from the Value
            match result.get("embeddings").and_then(|v| v.as_array()) {
                Some(arr) => {
                    let embeddings: Vec<Vec<f32>> = arr.iter()
                        .filter_map(|v| v.as_array().map(|a| a.iter().filter_map(|f| f.as_f64().map(|f| f as f32)).collect()))
                        .collect();
                    Ok(embeddings)
                }
                None => Ok(vec![]),
            }
        }
    }
}

// =============================================================================
// Response types
// =============================================================================

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
// Implementation
// =============================================================================

/// OpenAI-compatible embedding request (covers OpenAI, vLLM, Azure).
pub async fn embed(config: &EmbeddingConfig, texts: &[String]) -> ProviderResult<Value> {
    let client = get_client();

    let base_url = config
        .base_url
        .as_deref()
        .unwrap_or("https://api.openai.com/v1/embeddings");

    let url = if base_url.ends_with("/embeddings") {
        base_url.to_string()
    } else {
        format!("{}/embeddings", base_url.trim_end_matches('/'))
    };

    let mut body = json!({ "input": texts });
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
