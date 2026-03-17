//! OpFactory — creates provider ops from config.
//!
//! Registered on the Hush engine by hush-serve at startup.
//! hush-icore's scheduler calls this for op types it doesn't know about
//! (llm, embedding, rerank, onnx, prompt, parser).

use hush_icore::config::BaseOpConfig;
use hush_icore::ops::op_trait::{Op, OpFactory};

/// Provider op factory — knows how to create LlmOp, EmbeddingOp, etc.
pub struct ProviderOpFactory;

impl OpFactory for ProviderOpFactory {
    fn create_op<'a>(&self, config: &'a BaseOpConfig) -> Option<Box<dyn Op + 'a>> {
        match config.op_type.as_str() {
            "llm" => Some(Box::new(super::llm::LlmOp::new(config))),
            "embedding" => Some(Box::new(super::embedding::EmbeddingOp::new(config))),
            "rerank" => Some(Box::new(super::rerank::RerankOp::new(config))),
            "prompt" => Some(Box::new(super::prompt::PromptOp::new(config))),
            // "parser" is handled by hush-icore directly (ParserOp in ops/transform/)
            #[cfg(feature = "onnx")]
            "onnx" => Some(Box::new(super::onnx::OnnxOp::new(config))),
            _ => None,
        }
    }
}
