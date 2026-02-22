//! Cohere reranker provider.

use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::reranking::RerankingConfig;
use crate::http::{get_client, ProviderError, ProviderResult};
use crate::rerankers::filter_and_sort;

#[derive(Deserialize)]
struct CohereRerankerResponse {
    results: Vec<CohereRerankerResult>,
}

#[derive(Deserialize)]
struct CohereRerankerResult {
    index: usize,
    relevance_score: f64,
}

/// Cohere reranking request.
pub async fn rerank(
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
        parsed
            .results
            .into_iter()
            .map(|r| (r.index, r.relevance_score))
            .collect(),
        top_k,
        threshold,
    );

    Ok(json!({ "results": results }))
}
