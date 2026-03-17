//! ONNX config stub — used when `onnx` feature is disabled.
//! When enabled, `config::onnx` re-exports from `crate::onnx::config`.

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum OnnxInputType {
    Mlp,
    Attention,
}

impl Default for OnnxInputType {
    fn default() -> Self {
        OnnxInputType::Mlp
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct OnnxInferenceConfig {
    pub model_path: String,
    #[serde(default)]
    pub input_type: OnnxInputType,
    #[serde(default = "default_pool_size")]
    pub pool_size: usize,
    #[serde(default = "default_intra_threads")]
    pub intra_threads: usize,
}

fn default_pool_size() -> usize { 1 }
fn default_intra_threads() -> usize { 1 }
