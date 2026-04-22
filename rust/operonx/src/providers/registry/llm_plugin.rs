//! LLM plugin registration.
//!
//! Mirrors Python [`operon/providers/registry/llm_plugin.py`](../../../../../operon/providers/registry/llm_plugin.py).
//! Binds category `"llm"` in the global [`ConfigRegistry`] to a factory that
//! deserializes raw config dicts into typed [`LLMConfig`] + constructs the
//! matching backend via [`create_llm`].

use std::sync::Arc;

use serde_json::Value;

use super::LlmResource;
use crate::core::exceptions::OperonError;
use crate::core::registry::{registry, ConfigDict, Factory, ResourceInstance};
use crate::providers::llms::{create_llm, LLMConfig};

/// Register the LLM category factory in the global [`ConfigRegistry`].
///
/// Idempotent — returns `Ok(())` if the category is already registered (so
/// calling `providers::registry::register_all()` repeatedly is safe).
pub fn register() -> Result<(), OperonError> {
    if registry().get_factory("llm").is_some() {
        return Ok(());
    }
    let factory: Factory = Arc::new(|cfg: ConfigDict| {
        let typed: LLMConfig = serde_json::from_value(Value::Object(cfg))?;
        let llm = create_llm(typed)?;
        Ok(Arc::new(LlmResource(llm)) as ResourceInstance)
    });
    registry().register("llm", factory, Some("LLMConfig"))
}
