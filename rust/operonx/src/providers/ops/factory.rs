//! Dispatch from [`OpType`] → the matching provider-op `execute` function.
//!
//! Mirrors the role of Python's dispatch table in
//! [`operon/providers/ops/factory.py`](../../../../../operon/providers/ops/factory.py).
//! The scheduler calls [`is_provider_kind`] to decide whether to route the
//! op here; when it does, [`execute_provider_op`] picks the right backend
//! function by `kind`.

use serde_json::{Map, Value};

use super::{embedding, llm, onnx, prompt, rerank, triton};
use crate::core::configs::op_config::{OpConfig, OpType};
use crate::core::exceptions::OperonError;

/// `true` if the scheduler should route this op to
/// [`execute_provider_op`] rather than the [`OpRegistry`] (for `Code` /
/// `Lambda`) or return an error.
pub fn is_provider_kind(kind: OpType) -> bool {
    matches!(
        kind,
        OpType::Llm
            | OpType::Embedding
            | OpType::Rerank
            | OpType::Prompt
            | OpType::Onnx
            | OpType::Triton
    )
}

/// Dispatch a provider op to its concrete implementation.
///
/// Returns the op's output dict (matches Python's declared output schema).
pub async fn execute_provider_op(
    op: &OpConfig,
    inputs: Map<String, Value>,
) -> Result<Value, OperonError> {
    match op.kind {
        OpType::Llm => llm::execute(op, inputs).await,
        OpType::Embedding => embedding::execute(op, inputs).await,
        OpType::Rerank => rerank::execute(op, inputs).await,
        OpType::Prompt => prompt::execute(inputs).await,
        OpType::Onnx => onnx::execute(op, inputs).await,
        OpType::Triton => triton::execute(op, inputs).await,
        other => Err(OperonError::Runtime(format!(
            "execute_provider_op: op type {:?} is not a provider op ({})",
            other, op.full_name
        ))),
    }
}
