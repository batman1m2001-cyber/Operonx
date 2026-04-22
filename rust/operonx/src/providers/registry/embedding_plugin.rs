//! Embedding plugin registration.
//!
//! Mirrors Python [`operon/providers/registry/embedding_plugin.py`](../../../../../operon/providers/registry/embedding_plugin.py).

use std::sync::Arc;

use serde_json::Value;

use super::EmbeddingResource;
use crate::core::exceptions::OperonError;
use crate::core::registry::{registry, ConfigDict, Factory, ResourceInstance};
use crate::providers::embeddings::{create_embedder, EmbeddingConfig};

/// Register the embedding category factory. Idempotent.
pub fn register() -> Result<(), OperonError> {
    if registry().get_factory("embedding").is_some() {
        return Ok(());
    }
    let factory: Factory = Arc::new(|cfg: ConfigDict| {
        let typed: EmbeddingConfig = serde_json::from_value(Value::Object(cfg))?;
        let embedder = create_embedder(typed)?;
        Ok(Arc::new(EmbeddingResource(embedder)) as ResourceInstance)
    });
    registry().register("embedding", factory, Some("EmbeddingConfig"))
}
