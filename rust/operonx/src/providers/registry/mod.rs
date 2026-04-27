//! Provider-plugin registration into the global [`ConfigRegistry`].
//!
//! Mirrors Python `operonx/providers/registry/`.
//!
//! Python achieves registration via "import triggers registration" — each
//! `*_plugin.py` imports register() at module load. Rust doesn't execute
//! module-level code on import, so Phase 5 ships an explicit
//! [`register_all`] function that the engine / user calls on startup.
//!
//! Phase 6 adds `inventory::submit!` entries to the individual plugin files
//! so registrations become truly zero-cost / compile-time; until then this
//! explicit entry point is the supported mechanism.

use std::sync::Arc;

use crate::core::exceptions::OperonError;
use crate::providers::auth::KeycloakTokenProvider;
use crate::providers::embeddings::BaseEmbedder;
use crate::providers::llms::BaseLLM;
use crate::providers::onnx::OnnxInferenceBackend;
use crate::providers::rerankers::BaseReranker;

pub mod auth_plugin;
pub mod embedding_plugin;
pub mod llm_plugin;
pub mod onnx_plugin;
pub mod rerank_plugin;

// ── Typed resource newtypes ───────────────────────────────────────────────
//
// `ConfigRegistry` stores every built resource as `Arc<dyn Any + Send +
// Sync>`. To recover the concrete trait object at dispatch time we need a
// `Sized` wrapper — `Arc<dyn Trait>` *is* Sized (a fat pointer) but casting
// it into `Arc<dyn Any>` isn't straightforward since `dyn Trait` itself
// isn't `Any`. Wrapping in a named struct solves both ends:
// `Arc<LlmResource>` downcasts cleanly and exposes the inner `Arc<dyn
// BaseLLM>` via field access.

/// Wraps an [`Arc<dyn BaseLLM>`] for storage in the `ResourceHub` cache.
pub struct LlmResource(pub Arc<dyn BaseLLM>);

/// Wraps an [`Arc<dyn BaseEmbedder>`].
pub struct EmbeddingResource(pub Arc<dyn BaseEmbedder>);

/// Wraps an [`Arc<dyn BaseReranker>`].
pub struct RerankerResource(pub Arc<dyn BaseReranker>);

/// Wraps an [`Arc<dyn OnnxInferenceBackend>`].
pub struct OnnxResource(pub Arc<dyn OnnxInferenceBackend>);

/// Wraps an [`Arc<KeycloakTokenProvider>`].
pub struct KeycloakResource(pub Arc<KeycloakTokenProvider>);

/// Register every built-in provider plugin (LLM / embedding / reranking /
/// auth / onnx). Idempotent — re-invocation is a no-op for already-
/// registered categories.
pub fn register_all() -> Result<(), OperonError> {
    llm_plugin::register()?;
    embedding_plugin::register()?;
    rerank_plugin::register()?;
    auth_plugin::register()?;
    onnx_plugin::register()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::registry::registry;

    #[test]
    fn register_all_installs_every_category() {
        register_all().unwrap();
        for cat in ["llm", "embedding", "reranking", "keycloak", "onnx"] {
            assert!(
                registry().get_factory(cat).is_some(),
                "plugin '{}' not registered",
                cat
            );
        }
    }
}
