//! Provider op implementations — LLMOp, EmbeddingOp, RerankOp, OnnxOp, TritonOp, PromptOp.
//!
//! Mirrors Python `operonx/providers/ops/` one-for-one. Python's `chain.py`
//! is authoring-layer (builds a GraphOp from `@graph`-decorated helpers);
//! the Rust port ships [`chain::build_chat_graph`] / [`chain::build_ask_graph`]
//! that emit the same serialized JSON shape for Rust-native callers — see
//! [`chain`] for the rationale.

pub mod chain;
pub mod embedding;
pub mod factory;
pub mod llm;
pub mod onnx;
pub mod prompt;
pub mod rerank;
pub mod triton;
pub mod utils;

pub use chain::{build_ask_graph, build_chat_graph, AskArgs, ChatArgs};
pub use factory::{execute_provider_op, is_provider_kind};
pub use utils::resolve_hub;
