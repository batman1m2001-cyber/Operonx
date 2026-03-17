//! ONNX inference provider config.
//!
//! Used by OnnxOp for running arbitrary ONNX models (classifiers, transformers).
//! Separate from EmbeddingConfig which handles ONNX embeddings.

use serde::Deserialize;

/// Input type determines how inputs are shaped for the ONNX model.
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum OnnxInputType {
    /// MLP: (batch, dim) → (batch,) logits. Per-embedding classification.
    Mlp,
    /// Attention-pooled: (1, T, dim) + role_ids (1, T) + mask (1, T) → (1,) logit.
    Attention,
}

impl Default for OnnxInputType {
    fn default() -> Self {
        OnnxInputType::Mlp
    }
}

/// Config for an ONNX inference model.
#[derive(Debug, Clone, Deserialize)]
pub struct OnnxInferenceConfig {
    /// Path to the .onnx model file.
    pub model_path: String,
    /// Input type: "mlp" or "attention".
    #[serde(default)]
    pub input_type: OnnxInputType,
    /// Number of sessions in the pool (default: 1).
    #[serde(default = "default_pool_size")]
    pub pool_size: usize,
    /// Number of intra-op threads per session (default: 1).
    /// Small models (MLPs, classifiers) don't benefit from parallelism.
    #[serde(default = "default_intra_threads")]
    pub intra_threads: usize,
}

fn default_pool_size() -> usize {
    1
}

fn default_intra_threads() -> usize {
    1
}
