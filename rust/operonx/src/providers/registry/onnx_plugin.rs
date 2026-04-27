//! ONNX plugin registration.
//!
//! Mirrors Python [`operonx/providers/registry/onnx_plugin.py`](../../../../../operonx/providers/registry/onnx_plugin.py).

use std::sync::Arc;

use serde_json::Value;

use super::OnnxResource;
use crate::core::exceptions::OperonError;
use crate::core::registry::{registry, ConfigDict, Factory, ResourceInstance};
use crate::providers::onnx::{create_onnx_backend, OnnxInferenceConfig};

/// Register the onnx category factory. Idempotent.
pub fn register() -> Result<(), OperonError> {
    if registry().get_factory("onnx").is_some() {
        return Ok(());
    }
    let factory: Factory = Arc::new(|cfg: ConfigDict| {
        let typed: OnnxInferenceConfig = serde_json::from_value(Value::Object(cfg))?;
        let backend = create_onnx_backend(typed)?;
        Ok(Arc::new(OnnxResource(backend)) as ResourceInstance)
    });
    registry().register("onnx", factory, Some("OnnxInferenceConfig"))
}
