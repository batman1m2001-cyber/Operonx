//! ONNX Runtime reranker (cross-encoder) backend.
//!
//! Mirrors Python [`operonx/providers/rerankers/onnx.py`](../../../../../operonx/providers/rerankers/onnx.py).
//! Feature-gated behind `onnx` — requires `ort` + `tokenizers` crates.

use async_trait::async_trait;
use serde_json::Value;

use super::base::{BaseReranker, RerankOpts, RerankResult};
use super::config::RerankingConfig;
use crate::core::exceptions::OperonError;

pub struct OnnxReranker {
    pub config: RerankingConfig,
}

impl OnnxReranker {
    pub fn new(config: RerankingConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseReranker for OnnxReranker {
    async fn run(
        &self,
        _query: String,
        _texts: Vec<Value>,
        _top_k: usize,
        _opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError> {
        #[cfg(feature = "onnx")]
        {
            return Err(OperonError::Provider(
                "OnnxReranker::run not yet implemented (Phase 5b — shared ONNX backend)".into(),
            ));
        }
        #[cfg(not(feature = "onnx"))]
        {
            Err(OperonError::Provider(
                "operonx built without the `onnx` feature — rebuild with --features onnx".into(),
            ))
        }
    }
}
