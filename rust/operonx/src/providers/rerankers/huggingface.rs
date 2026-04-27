//! HuggingFace Transformers reranker backend.
//!
//! Mirrors Python [`operonx/providers/rerankers/huggingface.py`](../../../../../operonx/providers/rerankers/huggingface.py).
//! Per plan §5a recommendation, stays a **stub** in v0.6 — Rust prod paths
//! use ONNX exports ([`super::onnx::OnnxReranker`]).

use async_trait::async_trait;
use serde_json::Value;

use super::base::{BaseReranker, RerankOpts, RerankResult};
use super::config::RerankingConfig;
use crate::core::exceptions::OperonError;

pub struct HuggingFaceReranker {
    pub config: RerankingConfig,
}

impl HuggingFaceReranker {
    pub fn new(config: RerankingConfig) -> Self {
        Self { config }
    }
}

#[async_trait]
impl BaseReranker for HuggingFaceReranker {
    async fn run(
        &self,
        _query: String,
        _texts: Vec<Value>,
        _top_k: usize,
        _opts: &RerankOpts,
    ) -> Result<Vec<RerankResult>, OperonError> {
        Err(OperonError::Provider(
            "HuggingFaceReranker not supported in Rust v0.6 — convert your cross-encoder to ONNX and use `api_type: onnx` instead".into(),
        ))
    }
}
