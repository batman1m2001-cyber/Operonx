//! Shared helpers for provider ops.
//!
//! Mirrors Python [`operon/providers/ops/_utils.py`](../../../../../operon/providers/ops/_utils.py).
//! Per plan §5b.8 the Python file exposes only `resolve_hub()` — nothing
//! else. The Rust port mirrors that plus a few downcast helpers needed to
//! recover typed trait objects from the [`ResourceHub`] cache.

use std::sync::Arc;

use crate::core::exceptions::OperonError;
use crate::core::registry::ResourceHub;
use crate::providers::auth::KeycloakTokenProvider;
use crate::providers::embeddings::BaseEmbedder;
use crate::providers::llms::BaseLLM;
use crate::providers::onnx::OnnxInferenceBackend;
use crate::providers::registry::{
    EmbeddingResource, KeycloakResource, LlmResource, OnnxResource, RerankerResource,
};
use crate::providers::rerankers::BaseReranker;

/// Return the active [`ResourceHub`] singleton — direct port of Python's
/// `resolve_hub()`.
pub fn resolve_hub() -> Result<Arc<ResourceHub>, OperonError> {
    ResourceHub::instance()
}

// ── Typed fetch helpers ──────────────────────────────────────────────────

/// Resolve `llm:<key>` to a concrete [`BaseLLM`] handle.
pub fn resolve_llm(key: &str) -> Result<Arc<dyn BaseLLM>, OperonError> {
    let hub = resolve_hub()?;
    let full = format!("llm:{}", key);
    let instance = hub.get(&full)?;
    let wrapper: Arc<LlmResource> = instance.downcast::<LlmResource>().map_err(|_| {
        OperonError::ResourceHub(format!(
            "resource '{}' is not an LLM (downcast failed)",
            full
        ))
    })?;
    Ok(wrapper.0.clone())
}

/// Resolve `embedding:<key>` to a concrete [`BaseEmbedder`] handle.
pub fn resolve_embedder(key: &str) -> Result<Arc<dyn BaseEmbedder>, OperonError> {
    let hub = resolve_hub()?;
    let full = format!("embedding:{}", key);
    let instance = hub.get(&full)?;
    let wrapper: Arc<EmbeddingResource> =
        instance.downcast::<EmbeddingResource>().map_err(|_| {
            OperonError::ResourceHub(format!(
                "resource '{}' is not an Embedder (downcast failed)",
                full
            ))
        })?;
    Ok(wrapper.0.clone())
}

/// Resolve `reranking:<key>` to a concrete [`BaseReranker`] handle.
pub fn resolve_reranker(key: &str) -> Result<Arc<dyn BaseReranker>, OperonError> {
    let hub = resolve_hub()?;
    let full = format!("reranking:{}", key);
    let instance = hub.get(&full)?;
    let wrapper: Arc<RerankerResource> =
        instance.downcast::<RerankerResource>().map_err(|_| {
            OperonError::ResourceHub(format!(
                "resource '{}' is not a Reranker (downcast failed)",
                full
            ))
        })?;
    Ok(wrapper.0.clone())
}

/// Resolve `onnx:<key>` to a concrete [`OnnxInferenceBackend`] handle.
pub fn resolve_onnx(key: &str) -> Result<Arc<dyn OnnxInferenceBackend>, OperonError> {
    let hub = resolve_hub()?;
    let full = format!("onnx:{}", key);
    let instance = hub.get(&full)?;
    let wrapper: Arc<OnnxResource> = instance.downcast::<OnnxResource>().map_err(|_| {
        OperonError::ResourceHub(format!(
            "resource '{}' is not an Onnx backend (downcast failed)",
            full
        ))
    })?;
    Ok(wrapper.0.clone())
}

/// Resolve `keycloak:<key>` to a [`KeycloakTokenProvider`].
pub fn resolve_keycloak(key: &str) -> Result<Arc<KeycloakTokenProvider>, OperonError> {
    let hub = resolve_hub()?;
    let full = format!("keycloak:{}", key);
    let instance = hub.get(&full)?;
    let wrapper: Arc<KeycloakResource> =
        instance.downcast::<KeycloakResource>().map_err(|_| {
            OperonError::ResourceHub(format!(
                "resource '{}' is not a Keycloak provider (downcast failed)",
                full
            ))
        })?;
    Ok(wrapper.0.clone())
}
