//! ProviderRegistry — creates provider ops from config.
//!
//! Implements the unified OpRegistry trait from hush-icore.
//! Handles LLM, embedding, rerank, prompt, and ONNX ops.

use hush_icore::config::BaseOpConfig;
use hush_icore::ops::op_trait::Op;
use hush_icore::registry::OpRegistry;

/// Provider op registry — knows how to create LlmOp, EmbeddingOp, etc.
pub struct ProviderRegistry;

impl OpRegistry for ProviderRegistry {
    fn create_op<'a>(&self, config: &'a BaseOpConfig) -> Option<Box<dyn Op + 'a>> {
        match config.op_type.as_str() {
            "llm" => Some(Box::new(super::llm::LlmOp::new(config))),
            "embedding" => Some(Box::new(super::embedding::EmbeddingOp::new(config))),
            "rerank" => Some(Box::new(super::rerank::RerankOp::new(config))),
            "prompt" => Some(Box::new(super::prompt::PromptOp::new(config))),
            // "parser" is handled by CoreRegistry in hush-icore
            #[cfg(feature = "onnx")]
            "onnx" => Some(Box::new(super::onnx::OnnxOp::new(config))),
            _ => None,
        }
    }
}
