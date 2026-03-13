//! Pinecone reranker provider.

use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::reranking::RerankingConfig;
use crate::http::{get_client, ProviderError, ProviderResult};
use crate::rerankers::filter_and_sort;

#[derive(Deserialize)]
struct PineconeRerankerResponse {
    data: Vec<PineconeRerankerResult>,
}

#[derive(Deserialize)]
struct PineconeRerankerResult {
    index: usize,
    score: f64,
}

/// Pinecone reranking request.
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
