pub mod config;

//! Generic ONNX inference — session pool for arbitrary ONNX models.
//!
//! Supports two input types:
//! - MLP: (batch, dim) → (batch,) logits
//! - Attention: (1, T, dim) + role_ids + mask → (1,) logit

use std::sync::{Arc, Mutex, OnceLock};

use dashmap::DashMap;
use ndarray::{Array2, Array3};
use ort::session::Session;
use ort::value::Tensor;
use serde_json::Value;

use crate::config::onnx::{OnnxInferenceConfig, OnnxInputType};

/// Global pool cache — keyed by model_path.
static POOLS: OnceLock<DashMap<String, Arc<OnnxInferencePool>>> = OnceLock::new();

fn pools() -> &'static DashMap<String, Arc<OnnxInferencePool>> {
    POOLS.get_or_init(DashMap::new)
}

/// Get or create an inference pool for the given config.
pub fn get_pool(config: &OnnxInferenceConfig) -> Result<Arc<OnnxInferencePool>, String> {
    let key = config.model_path.clone();
    if let Some(pool) = pools().get(&key) {
        return Ok(pool.value().clone());
    }

    let pool = Arc::new(OnnxInferencePool::new(config)?);
    pools().insert(key, pool.clone());
    Ok(pool)
}

/// Pool of ONNX sessions for concurrent inference.
pub struct OnnxInferencePool {
    sessions: Vec<Mutex<Session>>,
    input_type: OnnxInputType,
}

impl OnnxInferencePool {
    pub fn new(config: &OnnxInferenceConfig) -> Result<Self, String> {
        let mut sessions = Vec::with_capacity(config.pool_size);
        for _ in 0..config.pool_size {
            let session = Session::builder()
                .map_err(|e| format!("Session builder: {}", e))?
                .with_intra_threads(config.intra_threads)
                .map_err(|e| format!("Intra threads: {}", e))?
                .commit_from_file(&config.model_path)
                .map_err(|e| format!("Load ONNX '{}': {}", config.model_path, e))?;
            sessions.push(Mutex::new(session));
        }

        Ok(OnnxInferencePool {
            sessions,
            input_type: config.input_type.clone(),
        })
    }

    fn with_session<F, R>(&self, f: F) -> R
    where
        F: FnOnce(&mut Session) -> R,
    {
        for s in &self.sessions {
            if let Ok(mut guard) = s.try_lock() {
                return f(&mut guard);
            }
        }
        let mut guard = self.sessions[0].lock().unwrap();
        f(&mut guard)
    }

    /// MLP inference: (batch, dim) → Vec<f32> probabilities.
    pub fn predict_mlp(&self, embeddings: &[Vec<f32>]) -> Result<Vec<f32>, String> {
        if embeddings.is_empty() {
            return Ok(vec![]);
        }

        let batch = embeddings.len();
        let dim = embeddings[0].len();
        let flat: Vec<f32> = embeddings.iter().flatten().copied().collect();
        let input_arr = Array2::from_shape_vec((batch, dim), flat)
            .map_err(|e| format!("Shape error: {}", e))?;

        self.with_session(|session| {
            let input_tensor = Tensor::from_array(input_arr.clone().into_dyn())
                .map_err(|e| format!("Tensor error: {}", e))?;

            let outputs = session
                .run(ort::inputs!["input" => input_tensor])
                .map_err(|e| format!("ONNX run: {}", e))?;

            let (_shape, logits_data) = outputs[0]
                .try_extract_tensor::<f32>()
                .map_err(|e| format!("Extract: {}", e))?;

            let probs: Vec<f32> = logits_data.iter().map(|&l| sigmoid(l)).collect();
            Ok(probs)
        })
    }

    /// Attention/Transformer inference: embeddings + role_ids + mask → f32 probability.
    pub fn predict_sequence(
        &self,
        embeddings: &[Vec<f32>],
        role_ids: &[i64],
        mask: &[bool],
    ) -> Result<f32, String> {
        if embeddings.is_empty() {
            return Ok(0.0);
        }

        let n_turns = embeddings.len();
        let dim = embeddings[0].len();

        let flat: Vec<f32> = embeddings.iter().flatten().copied().collect();
        let emb_arr = Array3::from_shape_vec((1, n_turns, dim), flat)
            .map_err(|e| format!("Embedding shape: {}", e))?;

        let role_arr = Array2::from_shape_vec((1, n_turns), role_ids.to_vec())
            .map_err(|e| format!("Role IDs shape: {}", e))?;

        let mask_arr = Array2::from_shape_vec((1, n_turns), mask.to_vec())
            .map_err(|e| format!("Mask shape: {}", e))?;

        self.with_session(|session| {
            let emb_tensor = Tensor::from_array(emb_arr.clone().into_dyn())
                .map_err(|e| format!("Emb tensor: {}", e))?;
            let role_tensor = Tensor::from_array(role_arr.clone().into_dyn())
                .map_err(|e| format!("Role tensor: {}", e))?;
            let mask_tensor = Tensor::from_array(mask_arr.clone().into_dyn())
                .map_err(|e| format!("Mask tensor: {}", e))?;

            let outputs = session
                .run(ort::inputs![
                    "embeddings" => emb_tensor,
                    "role_ids" => role_tensor,
                    "mask" => mask_tensor
                ])
                .map_err(|e| format!("ONNX run: {}", e))?;

            let (_shape, logits_data) = outputs[0]
                .try_extract_tensor::<f32>()
                .map_err(|e| format!("Extract: {}", e))?;

            Ok(sigmoid(logits_data[0]))
        })
    }
}

fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x).exp())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sigmoid() {
        assert!((sigmoid(0.0) - 0.5).abs() < 1e-6);
        assert!(sigmoid(10.0) > 0.999);
        assert!(sigmoid(-10.0) < 0.001);
    }
}
