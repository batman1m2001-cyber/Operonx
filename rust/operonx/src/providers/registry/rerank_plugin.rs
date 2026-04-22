//! Reranker plugin registration.
//!
//! Mirrors Python [`operon/providers/registry/rerank_plugin.py`](../../../../../operon/providers/registry/rerank_plugin.py).

use std::sync::Arc;

use serde_json::Value;

use super::RerankerResource;
use crate::core::exceptions::OperonError;
use crate::core::registry::{registry, ConfigDict, Factory, ResourceInstance};
use crate::providers::rerankers::{create_reranker, RerankingConfig};

/// Register the reranking category factory. Idempotent.
///
/// Note the category key — Python uses `"reranking"`, not `"rerank"`.
pub fn register() -> Result<(), OperonError> {
    if registry().get_factory("reranking").is_some() {
        return Ok(());
    }
    let factory: Factory = Arc::new(|cfg: ConfigDict| {
        let typed: RerankingConfig = serde_json::from_value(Value::Object(cfg))?;
        let reranker = create_reranker(typed)?;
        Ok(Arc::new(RerankerResource(reranker)) as ResourceInstance)
    });
    registry().register("reranking", factory, Some("RerankingConfig"))
}
