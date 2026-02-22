//! Reranking HTTP clients — vLLM, TEI, Pinecone, Cohere.
//!
//! Each function makes a single HTTP request and returns parsed reranking scores.
//! No GIL needed.

use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::reranking::RerankingConfig;
use crate::http::{get_client, ProviderError, ProviderResult};

// =============================================================================
// Response types
// =============================================================================

/// vLLM reranker response.
#[derive(Deserialize)]
struct VLLMRerankerResponse {
    results: Vec<VLLMRerankerResult>,
}

#[derive(Deserialize)]
struct VLLMRerankerResult {
    index: usize,
    relevance_score: f64,
}

/// TEI reranker response: list of [index, score] pairs.
/// TEI returns an array of objects: [{"index": 0, "score": 0.95}, ...]
#[derive(Deserialize)]
struct TEIRerankerResult {
    index: usize,
    score: f64,
}

/// Pinecone reranker response.
#[derive(Deserialize)]
struct PineconeRerankerResponse {
    data: Vec<PineconeRerankerResult>,
}

#[derive(Deserialize)]
struct PineconeRerankerResult {
    index: usize,
    score: f64,
}

/// Cohere reranker response.
#[derive(Deserialize)]
struct CohereRerankerResponse {
    results: Vec<CohereRerankerResult>,
}

#[derive(Deserialize)]
struct CohereRerankerResult {
    index: usize,
    relevance_score: f64,
}

// =============================================================================
// Dispatch by api_type
// =============================================================================

/// Execute a reranking request against the appropriate backend.
pub async fn rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    match config.api_type.as_str() {
        "vllm" => vllm_rerank(config, query, texts, top_k, threshold).await,
        "tei" => tei_rerank(config, query, texts, top_k, threshold).await,
        "pinecone" => pinecone_rerank(config, query, texts, top_k, threshold).await,
        "cohere" => cohere_rerank(config, query, texts, top_k, threshold).await,
        other => Err(ProviderError {
            message: format!(
                "Unsupported reranking api_type for native HTTP: '{}' (use Python fallback for hf/onnx)",
                other
            ),
            status_code: None,
            error_code: None,
        }),
    }
}

// =============================================================================
// vLLM reranker
// =============================================================================

async fn vllm_rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    let client = get_client();

    let base_url = config
        .base_url
        .as_deref()
        .ok_or_else(|| ProviderError {
            message: "vLLM reranker requires base_url".to_string(),
            status_code: None,
            error_code: None,
        })?;

    let mut body = json!({
        "query": query,
        "documents": texts,
    });
    if let Some(ref model) = config.model {
        body["model"] = json!(model);
    }

    let mut req = client
        .post(base_url)
        .header("Content-Type", "application/json");
    if let Some(ref api_key) = config.api_key {
        req = req.header("Authorization", format!("Bearer {}", api_key));
    }

    let resp = req.json(&body).send().await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("vLLM reranker error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let parsed: VLLMRerankerResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse vLLM reranker response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    let results = filter_and_sort(
        parsed.results.into_iter().map(|r| (r.index, r.relevance_score)).collect(),
        top_k,
        threshold,
    );

    Ok(json!({ "results": results }))
}

// =============================================================================
// TEI reranker
// =============================================================================

async fn tei_rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    let client = get_client();

    let base_url = config
        .base_url
        .as_deref()
        .ok_or_else(|| ProviderError {
            message: "TEI reranker requires base_url".to_string(),
            status_code: None,
            error_code: None,
        })?;

    let url = if base_url.ends_with("/rerank") {
        base_url.to_string()
    } else {
        format!("{}/rerank", base_url.trim_end_matches('/'))
    };

    let body = json!({
        "query": query,
        "texts": texts,
        "raw_scores": false,
    });

    let mut req = client.post(&url).header("Content-Type", "application/json");
    if let Some(ref api_key) = config.api_key {
        req = req.header("Authorization", format!("Bearer {}", api_key));
    }

    let resp = req.json(&body).send().await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("TEI reranker error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let parsed: Vec<TEIRerankerResult> = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse TEI reranker response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    let results = filter_and_sort(
        parsed.into_iter().map(|r| (r.index, r.score)).collect(),
        top_k,
        threshold,
    );

    Ok(json!({ "results": results }))
}

// =============================================================================
// Pinecone reranker
// =============================================================================

async fn pinecone_rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    let client = get_client();

    let base_url = config
        .base_url
        .as_deref()
        .unwrap_or("https://api.pinecone.io/rerank");

    let documents: Vec<Value> = texts
        .iter()
        .enumerate()
        .map(|(i, text)| {
            json!({
                "id": format!("doc_{}", i),
                "text": text,
            })
        })
        .collect();

    let mut body = json!({
        "query": query,
        "documents": documents,
        "return_documents": true,
        "parameters": {"truncate": "END"},
    });
    if let Some(ref model) = config.model {
        body["model"] = json!(model);
    }
    if let Some(k) = top_k {
        body["top_n"] = json!(k);
    } else {
        body["top_n"] = json!(texts.len());
    }

    let api_key = config.api_key.as_deref().ok_or_else(|| ProviderError {
        message: "Pinecone reranker requires api_key".to_string(),
        status_code: None,
        error_code: None,
    })?;

    let mut req = client
        .post(base_url)
        .header("Content-Type", "application/json")
        .header("Accept", "application/json")
        .header("Api-Key", api_key);
    if let Some(ref api_version) = config.api_version {
        req = req.header("X-Pinecone-API-Version", api_version.as_str());
    }

    let resp = req.json(&body).send().await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Pinecone reranker error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let parsed: PineconeRerankerResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse Pinecone response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    let results = filter_and_sort(
        parsed.data.into_iter().map(|r| (r.index, r.score)).collect(),
        top_k,
        threshold,
    );

    Ok(json!({ "results": results }))
}

// =============================================================================
// Cohere reranker
// =============================================================================

async fn cohere_rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    let client = get_client();

    let base_url = config
        .base_url
        .as_deref()
        .unwrap_or("https://api.cohere.ai/v1/rerank");

    let api_key = config.api_key.as_deref().ok_or_else(|| ProviderError {
        message: "Cohere reranker requires api_key".to_string(),
        status_code: None,
        error_code: None,
    })?;

    let mut body = json!({
        "query": query,
        "documents": texts,
    });
    if let Some(ref model) = config.model {
        body["model"] = json!(model);
    }
    if let Some(k) = top_k {
        body["top_n"] = json!(k);
    }

    let resp = client
        .post(base_url)
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&body)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Cohere reranker error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let parsed: CohereRerankerResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse Cohere response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    let results = filter_and_sort(
        parsed.results.into_iter().map(|r| (r.index, r.relevance_score)).collect(),
        top_k,
        threshold,
    );

    Ok(json!({ "results": results }))
}

// =============================================================================
// Helpers
// =============================================================================

/// Filter results by threshold, sort by score descending, limit to top_k.
fn filter_and_sort(
    raw: Vec<(usize, f64)>,
    top_k: Option<usize>,
    threshold: f64,
) -> Vec<Value> {
    let mut results: Vec<(usize, f64)> = raw
        .into_iter()
        .filter(|(_, score)| *score >= threshold)
        .collect();

    // Sort by score descending
    results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    // Limit to top_k
    if let Some(k) = top_k {
        if k > 0 {
            results.truncate(k);
        }
    }

    results
        .into_iter()
        .map(|(index, score)| json!({"index": index, "score": score}))
        .collect()
}
