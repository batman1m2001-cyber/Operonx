//! Provider integrations — LLM, embedding, reranker, ONNX, Triton, auth.
//!
//! Mirrors Python `operon/providers/`.

pub mod auth;
pub mod embeddings;
pub mod http;
pub mod llms;
pub mod onnx;
pub mod ops;
pub mod registry;
pub mod rerankers;
pub mod utils;
