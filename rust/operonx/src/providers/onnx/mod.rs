//! ONNX inference backend.
//!
//! Mirrors Python `operonx/providers/onnx/`. Gated behind the `onnx` feature.

pub mod backend;
pub mod config;
pub mod factory;

pub use backend::{OnnxBackend, OnnxInferenceBackend};
pub use config::{OnnxInferenceConfig, OnnxInputType};
pub use factory::create_onnx_backend;
