//! ONNX inference configuration.
//!
//! Mirrors Python [`operonx/providers/onnx/config.py`](../../../../../operonx/providers/onnx/config.py).

use serde::{Deserialize, Serialize};

/// ONNX model input shape convention.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OnnxInputType {
    /// `(batch, dim) → (batch,)` logits.
    Mlp,
    /// `(1, T, dim)` + `role_ids` + `mask` → `(1,)` logit.
    Attention,
}

impl Default for OnnxInputType {
    fn default() -> Self {
        OnnxInputType::Mlp
    }
}

/// Inference-time ONNX model configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OnnxInferenceConfig {
    pub model_path: String,

    #[serde(default)]
    pub input_type: OnnxInputType,

    #[serde(default = "default_pool_size")]
    pub pool_size: usize,

    #[serde(default = "default_intra_threads")]
    pub intra_threads: usize,

    /// `"cpu"` or `"cuda"`; `None` means "auto / provider default".
    #[serde(default)]
    pub device: Option<String>,
}

fn default_pool_size() -> usize {
    1
}
fn default_intra_threads() -> usize {
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_with_defaults() {
        let src = r#"{"model_path": "/tmp/m.onnx"}"#;
        let cfg: OnnxInferenceConfig = serde_json::from_str(src).unwrap();
        assert_eq!(cfg.input_type, OnnxInputType::Mlp);
        assert_eq!(cfg.pool_size, 1);
    }
}
