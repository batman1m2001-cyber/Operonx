//! `create_onnx_backend` — build an [`OnnxBackend`] from its config.
//!
//! Mirrors Python [`operon/providers/onnx/factory.py`](../../../../../operon/providers/onnx/factory.py).

use std::sync::Arc;

use super::backend::{OnnxBackend, OnnxInferenceBackend};
use super::config::OnnxInferenceConfig;
use crate::core::exceptions::OperonError;

/// Construct the default ONNX backend.
pub fn create_onnx_backend(
    config: OnnxInferenceConfig,
) -> Result<Arc<dyn OnnxInferenceBackend>, OperonError> {
    Ok(Arc::new(OnnxBackend::new(config)))
}
