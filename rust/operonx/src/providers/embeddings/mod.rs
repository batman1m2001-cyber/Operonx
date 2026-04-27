//! Embedding providers.
//!
//! Mirrors Python `operonx/providers/embeddings/`.

pub mod base;
pub mod config;
pub mod factory;
pub mod huggingface;
pub mod onnx;
pub mod tei;
pub mod vllm;

pub use base::{BaseEmbedder, EmbedOpts, EmbedResult};
pub use config::{EmbeddingConfig, EmbeddingType};
pub use factory::create_embedder;
