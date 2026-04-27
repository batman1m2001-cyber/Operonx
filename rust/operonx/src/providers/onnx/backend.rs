//! Shared ONNX inference backend.
//!
//! Mirrors Python [`operonx/providers/onnx/backend.py`](../../../../../operonx/providers/onnx/backend.py).
//! Per plan §5a the shared session pool is extracted from the per-category
//! `embeddings/onnx.rs` + `rerankers/onnx.rs` files so both reuse it.
//!
//! Feature-gated behind `onnx` — requires the `ort` crate.
//!
//! # Phase 5 scope
//! Struct + trait stub. The session-pool (intra-op threads, pool_size,
//! input-type dispatch) lands in Phase 5b.

use async_trait::async_trait;
use serde_json::Value;

use super::config::OnnxInferenceConfig;
use crate::core::exceptions::OperonError;

/// Shared ONNX inference trait — one call point for both embedders and
/// rerankers.
#[async_trait]
pub trait OnnxInferenceBackend: Send + Sync {
    /// Run inference — `inputs` is a JSON-shaped map the backend interprets
    /// according to the model's declared [`OnnxInputType`](super::config::OnnxInputType).
    async fn run(&self, inputs: Value) -> Result<Value, OperonError>;
}

/// Default backend implementation. In Phase 5b this holds a pooled `ort`
/// `Session` + a tokenizer handle; today it's a stub.
pub struct OnnxBackend {
    pub config: OnnxInferenceConfig,
}

impl OnnxBackend {
    pub fn new(config: OnnxInferenceConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl OnnxInferenceBackend for OnnxBackend {
    async fn run(&self, _inputs: Value) -> Result<Value, OperonError> {
        #[cfg(feature = "onnx")]
        {
            return Err(OperonError::Provider(format!(
                "OnnxBackend::run not yet implemented (Phase 5b) — model_path={}",
                self.config.model_path
            )));
        }
        #[cfg(not(feature = "onnx"))]
        {
            Err(OperonError::Provider(
                "operonx built without the `onnx` feature — rebuild with --features onnx".into(),
            ))
        }
    }
}
