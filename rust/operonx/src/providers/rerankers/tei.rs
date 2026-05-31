//! HuggingFace Text Embeddings Inference (TEI) reranker backend.
//!
//! Mirrors Python [`operonx/providers/rerankers/tei.py`](../../../../../operonx/providers/rerankers/tei.py).
//! Posts to `/rerank` with `{query, texts: [...], raw_scores: false,
//! truncate: true}` and parses an array of `{index, score}` items.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::base::{BaseReranker, RerankOpts, RerankResult};
use super::config::RerankingConfig;
use crate::core::exceptions::OperonError;
use crate::providers::http::{get_client, ProviderError};

pub struct TeiReranker {
    pub config: RerankingConfig,
}

impl TeiReranker {
    pub fn new(config: RerankingConfig) -> Self {
        Self { config }
    }

    fn rerank_url(&self) -> String {
        let base = self
            .config
            .base_url
            .as_deref()
            .unwrap_or("http://localhost:8080")
            .trim_end_matches('/');
        format!("{}/rerank", base)
    }
}

#[derive(Serialize)]
struct TeiBody<'a> {
    query: &'a str,
    texts: Vec<String>,
    truncate: bool,
    raw_scores: bool,
}

#[derive(Deserialize)]
struct TeiItem {
    index: usize,
    score: f32,
}

#[async_trait]
impl BaseReranker for TeiReranker {
    async fn run(
        &self,
        query: String,
        texts: Vec<Value>,
        top_k: usize,
        _opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError> {
        // TEI accepts strings only; coerce any Value to text first.
        let text_strs: Vec<String> = texts
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
        let body = TeiBody {
            query: &query,
            texts: text_strs,
            truncate: true,
            raw_scores: false,
        };
        let mut req = get_client().post(self.rerank_url()).json(&body);
        if let Some(key) = &self.config.api_key {
            req = req.bearer_auth(key);
        }
        let resp = req.send().await.map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("tei rerank: {}", text))
                .with_status(status.as_u16())
                .into());
        }
        let mut items: Vec<TeiItem> = resp.json().await.map_err(ProviderError::from)?;
        items.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        items.truncate(top_k.max(1));
        let out: Vec<RerankResult> = items
            .into_iter()
            .map(|it| RerankResult {
                index: it.index,
                score: it.score,
                document: texts.get(it.index).cloned().unwrap_or(Value::Null),
            })
            .collect();
        Ok(out)
    }
}
