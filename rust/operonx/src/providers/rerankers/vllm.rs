//! vLLM reranker (cross-encoder served via vLLM) — OpenAI-style /v1/rerank.
//!
//! Mirrors Python [`operonx/providers/rerankers/vllm.py`](../../../../../operonx/providers/rerankers/vllm.py).
//! Body: `{model, query, documents, top_n}`. Response: `{results: [{index,
//! relevance_score, document}, ...]}`. Same shape as Cohere.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::base::{BaseReranker, RerankOpts, RerankResult};
use super::config::RerankingConfig;
use crate::core::exceptions::OperonError;
use crate::providers::http::{get_client, ProviderError};

pub struct VllmReranker {
    pub config: RerankingConfig,
}

impl VllmReranker {
    pub fn new(config: RerankingConfig) -> Self {
        Self { config }
    }

    fn rerank_url(&self) -> String {
        let base = self
            .config
            .base_url
            .as_deref()
            .unwrap_or("http://localhost:8000/v1")
            .trim_end_matches('/');
        format!("{}/rerank", base)
    }
}

#[derive(Serialize)]
struct VllmBody<'a> {
    #[serde(skip_serializing_if = "str::is_empty")]
    model: &'a str,
    query: &'a str,
    documents: Vec<String>,
    top_n: usize,
}

#[derive(Deserialize)]
struct VllmResponse {
    results: Vec<VllmItem>,
}

#[derive(Deserialize)]
struct VllmItem {
    index: usize,
    relevance_score: f32,
}

#[async_trait]
impl BaseReranker for VllmReranker {
    async fn run(
        &self,
        query: String,
        texts: Vec<Value>,
        top_k: usize,
        _opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError> {
        let documents: Vec<String> = texts
            .iter()
            .map(|v| match v {
                Value::String(s) => s.clone(),
                Value::Object(m) => m
                    .get("content")
                    .and_then(|c| c.as_str())
                    .map(String::from)
                    .unwrap_or_else(|| v.to_string()),
                other => other.to_string(),
            })
            .collect();
        let model = self.config.model.as_deref().unwrap_or("");
        let body = VllmBody {
            model,
            query: &query,
            documents,
            top_n: top_k.max(1),
        };
        let mut req = get_client().post(self.rerank_url()).json(&body);
        if let Some(key) = &self.config.api_key {
            req = req.bearer_auth(key);
        }
        let resp = req.send().await.map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("vllm rerank: {}", text))
                .with_status(status.as_u16())
                .into());
        }
        let parsed: VllmResponse = resp.json().await.map_err(ProviderError::from)?;
        Ok(parsed
            .results
            .into_iter()
            .map(|it| RerankResult {
                index: it.index,
                score: it.relevance_score,
                document: texts.get(it.index).cloned().unwrap_or(Value::Null),
            })
            .collect())
    }
}
