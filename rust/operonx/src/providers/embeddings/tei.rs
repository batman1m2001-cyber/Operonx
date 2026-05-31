//! HuggingFace Text Embeddings Inference (TEI) embedding backend.
//!
//! Mirrors Python [`operonx/providers/embeddings/tei.py`](../../../../../operonx/providers/embeddings/tei.py).
//! TEI uses its own `/embed` endpoint (not OpenAI-compatible) — body
//! `{inputs: [...]}` → response `[[...], [...], ...]` (raw nested float array).

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::base::{BaseEmbedder, EmbedOpts, EmbedResult};
use super::config::EmbeddingConfig;
use crate::core::exceptions::OperonError;
use crate::providers::http::{get_client, ProviderError};

pub struct TeiEmbedder {
    pub config: EmbeddingConfig,
}

impl TeiEmbedder {
    pub fn new(config: EmbeddingConfig) -> Self {
        Self { config }
    }

    fn embed_url(&self) -> String {
        let base = self
            .config
            .base_url
            .as_deref()
            .unwrap_or("http://localhost:8080")
            .trim_end_matches('/');
        format!("{}/embed", base)
    }
}

#[derive(Serialize)]
struct EmbedBody<'a> {
    inputs: &'a [String],
    #[serde(skip_serializing_if = "Option::is_none")]
    truncate: Option<bool>,
}

/// TEI response shape: a bare array-of-arrays `[[f32, ...], ...]`.
#[derive(Deserialize)]
struct EmbedResponse(Vec<Vec<f32>>);

#[async_trait]
impl BaseEmbedder for TeiEmbedder {
    async fn run(&self, texts: Vec<String>, _opts: &EmbedOpts) -> Result<EmbedResult, OperonError> {
        let body = EmbedBody {
            inputs: &texts,
            truncate: Some(true),
        };
        let mut req = get_client().post(self.embed_url()).json(&body);
        if let Some(key) = &self.config.api_key {
            req = req.bearer_auth(key);
        }
        let resp = req.send().await.map_err(ProviderError::from)?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(ProviderError::new(format!("tei: {}", text))
                .with_status(status.as_u16())
                .into());
        }
        let parsed: EmbedResponse = resp.json().await.map_err(ProviderError::from)?;
        Ok(EmbedResult {
            embeddings: parsed.0,
            model: self.config.model.clone().unwrap_or_default(),
            usage: None,
            extras: Default::default(),
        })
    }

    fn output_dim(&self) -> usize {
        self.config.dimensions.unwrap_or(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tei_url_uses_base_or_default() {
        let cfg = EmbeddingConfig {
            api_type: super::super::config::EmbeddingType::Tei,
            api_key: None,
            base_url: Some("http://my-tei:8080/".into()),
            api_version: None,
            model: Some("bge-m3".into()),
            embed_batch_size: None,
            dimensions: None,
            model_path: None,
            tokenizer_path: None,
            max_length: None,
        };
        let e = TeiEmbedder::new(cfg);
        assert_eq!(e.embed_url(), "http://my-tei:8080/embed");
    }
}
