//! [`BaseEmbedder`] trait.
//!
//! Mirrors Python [`operon/providers/embeddings/base.py`](../../../../../operon/providers/embeddings/base.py).
//! Per plan §5b.1 the canonical method is `run()` (not `embed()`).

use std::collections::HashMap;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::exceptions::OperonError;

/// Embedding-call options — pass-through for provider-specific params
/// (e.g., `input_type` for Cohere, `truncation` for OpenAI v2).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct EmbedOpts {
    #[serde(default)]
    pub extras: HashMap<String, Value>,
}

/// Result of an embed call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbedResult {
    /// `[[f32; dim]; n_texts]`.
    pub embeddings: Vec<Vec<f32>>,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub usage: Option<Value>,
    #[serde(flatten)]
    pub extras: HashMap<String, Value>,
}

/// The trait every embedding backend implements.
#[async_trait]
pub trait BaseEmbedder: Send + Sync {
    /// Embed a list of texts.
    async fn run(&self, texts: Vec<String>, opts: &EmbedOpts) -> Result<EmbedResult, OperonError>;

    /// Declared output vector dimension.
    fn output_dim(&self) -> usize;
}
