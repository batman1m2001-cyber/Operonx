//! `TritonOp` — generic Triton Inference Server gRPC client op.
//!
//! Mirrors Python [`operonx/providers/ops/triton.py`](../../../../../operonx/providers/ops/triton.py).
//! **New** for Rust (plan §5a marks this 🆕) — the Python impl uses
//! `tritonclient.grpc.aio`; the Rust port will use `tonic` + `prost` gated
//! behind the `triton` feature (see `Cargo.toml`).
//!
//! # Phase 6 scope
//! Stub. The shape is in place so `kind = "triton"` in a serialized graph
//! routes here cleanly; implementation lands alongside the `tonic` client in
//! Phase 6b.

use serde_json::{Map, Value};

use crate::core::configs::op_config::OpConfig;
use crate::core::exceptions::OperonError;

/// Execute a Triton inference op.
pub async fn execute(
    op: &OpConfig,
    _inputs: Map<String, Value>,
) -> Result<Value, OperonError> {
    #[cfg(feature = "triton")]
    {
        return Err(OperonError::Provider(format!(
            "TritonOp::execute not yet implemented (Phase 6b — tonic gRPC client) for '{}'",
            op.full_name
        )));
    }
    #[cfg(not(feature = "triton"))]
    {
        Err(OperonError::Provider(format!(
            "operonx built without the `triton` feature — rebuild with --features triton (op: {})",
            op.full_name
        )))
    }
}
