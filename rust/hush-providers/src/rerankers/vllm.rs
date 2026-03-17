//! vLLM reranker provider — mirrors Python's `rerankers/vllm.py`.

use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::reranking::RerankingConfig;
use crate::http::{get_client, ProviderError, ProviderResult};
use crate::rerankers::filter_and_sort;

/// vLLM reranker provider struct.
pub struct VllmReranker<'a> {
    pub config: &'a RerankingConfig,
}

impl<'a> VllmReranker<'a> {
    pub fn new(config: &'a RerankingConfig) -> Self {
        VllmReranker { config }
    }
}

impl super::base::RerankerProvider for VllmReranker<'_> {
    fn rerank(&self, query: &str, documents: &[Value], top_k: usize) -> impl std::future::Future<Output = ProviderResult<Vec<Value>>> + Send {
        let config = self.config;
        let q = query.to_string();
        let texts: Vec<String> = documents.iter()
            .filter_map(|d| d.as_str().map(String::from).or_else(|| d.get("text").and_then(|t| t.as_str()).map(String::from)))
            .collect();
        async move {
            let result = rerank(config, &q, &texts, Some(top_k), 0.0).await?;
            match result.get("reranks").and_then(|v| v.as_array()) {
                Some(arr) => Ok(arr.clone()),
                None => Ok(vec![]),
            }
        }
    }
}

#[derive(Deserialize)]
struct VLLMRerankerResponse {
    results: Vec<VLLMRerankerResult>,
}

#[derive(Deserialize)]
struct VLLMRerankerResult {
    index: usize,
    relevance_score: f64,
}

/// vLLM reranking request.
pub async fn rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    let client = get_client();

    let base_url = config.base_url.as_deref().ok_or_else(|| ProviderError {
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
