//! Pinecone reranker backend.
//!
//! Mirrors Python [`operonx/providers/rerankers/pinecone.py`](../../../../../operonx/providers/rerankers/pinecone.py).
//! POSTs to `https://api.pinecone.io/rerank` with `{model, query,
//! documents: [{text:...}], top_n}` and `Api-Key: <key>` auth.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::base::{BaseReranker, RerankOpts, RerankResult};
use super::config::RerankingConfig;
use crate::core::exceptions::OperonError;
use crate::providers::http::{get_client, ProviderError};

pub struct PineconeReranker {
    pub config: RerankingConfig,
}

impl PineconeReranker {
    pub fn new(config: RerankingConfig) -> Self {
        Self { config }
    }

    fn rerank_url(&self) -> String {
        let base = self
            .config
            .base_url
            .as_deref()
            .unwrap_or("https://api.pinecone.io")
            .trim_end_matches('/');
        format!("{}/rerank", base)
    }
}

#[derive(Serialize)]
struct PineconeBody<'a> {
    model: &'a str,
    query: &'a str,
    documents: Vec<PineconeDoc>,
    top_n: usize,
}

#[derive(Serialize)]
struct PineconeDoc {
    text: String,
}

#[derive(Deserialize)]
struct PineconeResponse {
    data: Vec<PineconeItem>,
}

#[derive(Deserialize)]
struct PineconeItem {
    index: usize,
    score: f32,
}

#[async_trait]
impl BaseReranker for PineconeReranker {
    async fn run(
        &self,
        query: String,
        texts: Vec<Value>,
        top_k: usize,
        _opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError> {
        let documents: Vec<PineconeDoc> = texts
            .iter()
            .map(|v| PineconeDoc {
                text: match v {
                    Value::String(s) => s.clone(),
                    Value::Object(m) => m
                        .get("content")
                        .and_then(|c| c.as_str())
                        .map(String::from)
                        .unwrap_or_else(|| v.to_string()),
                    other => other.to_string(),
                },
            })
            .collect();
        let model = self.config.model.as_deref().unwrap_or("");
        if model.is_empty() {
            return Err(OperonError::Config(
                "PineconeReranker: `model` must be set in config".into(),
            ));
        }
        let body = PineconeBody {
            model,
            query: &query,
            documents,
            top_n: top_k.max(1),
        };
        let mut req = get_client().post(self.rerank_url()).json(&body);
        if let Some(key) = &self.config.api_key {
            req = req.header("Api-Key", key);
        }
        let resp = req.send().await.map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("pinecone rerank: {}", text))
                .with_status(status.as_u16())
                .into());
        }
        let parsed: PineconeResponse = resp.json().await.map_err(ProviderError::from)?;
        Ok(parsed
            .data
            .into_iter()
            .map(|it| RerankResult {
                index: it.index,
                score: it.score,
                document: texts.get(it.index).cloned().unwrap_or(Value::Null),
            })
            .collect())
    }
}
