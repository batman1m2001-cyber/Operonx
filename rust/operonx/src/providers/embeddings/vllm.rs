//! vLLM / OpenAI-compatible embedding backend.
//!
//! Python groups `openai`, `azure`, `gemini`, and `vllm` under a single
//! OpenAI-shaped embedding call path — this file covers that shared
//! implementation.
//!
//! # Phase 5 scope
//! Minimal `run()` — POST to `/embeddings`, parse response. Batch
//! pagination deferred to Phase 5b.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::base::{BaseEmbedder, EmbedOpts, EmbedResult};
use super::config::EmbeddingConfig;
use crate::core::exceptions::OperonError;
use crate::providers::http::{get_client, ProviderError};

/// Embedding backend used by OpenAI / Azure / Gemini / vLLM — shared shape.
pub struct VllmEmbedder {
    pub config: EmbeddingConfig,
}

impl VllmEmbedder {
    pub fn new(config: EmbeddingConfig) -> Self {
        Self { config }
    }

    fn embeddings_url(&self) -> String {
        let base = self
            .config
            .base_url
            .as_deref()
            .unwrap_or("https://api.openai.com/v1")
            .trim_end_matches('/');
        format!("{}/embeddings", base)
    }
}

#[derive(Serialize)]
struct EmbedBody<'a> {
    input: &'a [String],
    model: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    dimensions: Option<usize>,
}

#[derive(Deserialize)]
struct EmbedResponse {
    data: Vec<EmbedDatum>,
    #[serde(default)]
    model: String,
    #[serde(default)]
    usage: Option<Value>,
}

#[derive(Deserialize)]
struct EmbedDatum {
    embedding: Vec<f32>,
}

#[async_trait]
impl BaseEmbedder for VllmEmbedder {
    async fn run(&self, texts: Vec<String>, _opts: &EmbedOpts) -> Result<EmbedResult, OperonError> {
        let model = self.config.model.as_deref().ok_or_else(|| {
            OperonError::Config("VllmEmbedder: `model` must be set in config".into())
        })?;

        let body = EmbedBody {
            input: &texts,
            model,
            dimensions: self.config.dimensions,
        };

        let mut req = get_client()
            .post(self.embeddings_url())
            .json(&body);
        if let Some(key) = &self.config.api_key {
            req = req.bearer_auth(key);
        }
        let resp = req.send().await.map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("embedding: {}", text))
                .with_status(status.as_u16())
                .into());
        }
        let parsed: EmbedResponse = resp.json().await.map_err(ProviderError::from)?;
        Ok(EmbedResult {
            embeddings: parsed.data.into_iter().map(|d| d.embedding).collect(),
            model: parsed.model,
            usage: parsed.usage,
            extras: Default::default(),
        })
    }

    fn output_dim(&self) -> usize {
        self.config.dimensions.unwrap_or(0)
    }
}
