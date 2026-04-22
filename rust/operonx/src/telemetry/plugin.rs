//! Telemetry plugin registration — wires `langfuse` (and eventually `otel`)
//! categories into the global [`ConfigRegistry`].
//!
//! Mirrors Python [`operon/telemetry/plugin.py`](../../../../operon/telemetry/plugin.py).
//! Per §6b.11 the Python guard is a module-level `_registered` flag; Rust's
//! `register()` functions check the registry to stay idempotent.

use std::sync::Arc;

use serde_json::Value;

use crate::core::exceptions::OperonError;
use crate::core::registry::{registry, ConfigDict, Factory, ResourceInstance};
use crate::telemetry::backends::langfuse::{LangfuseClient, LangfuseConfig};

/// Typed wrapper stored in [`ResourceHub`](crate::core::registry::ResourceHub)
/// so the telemetry layer can `Arc::downcast` cleanly.
pub struct LangfuseResource(pub Arc<LangfuseClient>);

/// Register the `langfuse` category factory (idempotent).
pub fn register_langfuse() -> Result<(), OperonError> {
    if registry().get_factory("langfuse").is_some() {
        return Ok(());
    }
    let factory: Factory = Arc::new(|cfg: ConfigDict| {
        let typed: LangfuseConfig = serde_json::from_value(Value::Object(cfg))?;
        let client = Arc::new(LangfuseClient::new(typed));
        Ok(Arc::new(LangfuseResource(client)) as ResourceInstance)
    });
    registry().register("langfuse", factory, Some("LangfuseConfig"))
}

/// Register every built-in telemetry plugin. Currently just Langfuse — OTEL
/// lands in v0.7 per §6a.
pub fn register_all() -> Result<(), OperonError> {
    register_langfuse()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn register_all_installs_langfuse() {
        register_all().unwrap();
        assert!(registry().get_factory("langfuse").is_some());
    }
}
