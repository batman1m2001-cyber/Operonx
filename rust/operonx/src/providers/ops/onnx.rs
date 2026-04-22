//! `OnnxOp` — run arbitrary ONNX models via ResourceHub.
//!
//! Mirrors Python [`operon/providers/ops/onnx.py`](../../../../../operon/providers/ops/onnx.py).
//! Supports two input-type flavors (MLP / Attention) — dispatch to either
//! happens inside the [`OnnxInferenceBackend`] itself; this op just forwards
//! the inputs map and returns whatever the backend emits.

use serde_json::{json, Map, Value};

use super::utils::resolve_onnx;
use crate::core::configs::op_config::OpConfig;
use crate::core::exceptions::OperonError;

/// Execute the ONNX op.
pub async fn execute(
    op: &OpConfig,
    inputs: Map<String, Value>,
) -> Result<Value, OperonError> {
    let resources = op.resource_keys();
    let key = resources.first().ok_or_else(|| {
        OperonError::Config(format!("OnnxOp '{}' missing `resource`", op.full_name))
    })?;

    let backend = resolve_onnx(key)?;
    // Forward the inputs dict verbatim — the backend knows its own shape.
    let payload = json!(inputs);
    backend.run(payload).await
}
