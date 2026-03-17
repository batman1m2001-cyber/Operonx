//! ONNX embedding provider — pure Rust using `ort` + `tokenizers`.
//!
//! - Loads model.onnx + tokenizer.json from a local directory
//! - Tokenizes input texts with padding
//! - Runs ONNX inference → last_hidden_state
//! - Mean pooling with attention mask
//! - L2 normalization
//! Caching is handled at the op level, not inside the embedder.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use ndarray::Array2;
use ort::session::{builder::GraphOptimizationLevel, Session};
use ort::value::Tensor;
use serde_json::{json, Value};
use tokenizers::{PaddingDirection, PaddingParams, PaddingStrategy, Tokenizer};

use crate::config::embedding::EmbeddingConfig;
use crate::http::{ProviderError, ProviderResult};

/// One slot in the session pool: owns its own Session + Tokenizer.
struct Slot {
    session: Session,
    tokenizer: Tokenizer,
}

/// Persistent ONNX embedder with a pool of sessions for concurrent inference.
///
/// Caching is handled at the op level (via `cache=True` on EmbeddingOp),
/// not inside the embedder.
pub struct OnnxEmbedder {
    pool: Vec<Mutex<Slot>>,
    needs_token_type_ids: bool,
    output_name: String,
}

impl OnnxEmbedder {
    /// Create a new embedder with default pool size (1).
    pub fn new(model_dir: &str) -> Result<Self, String> {
        Self::with_pool_size(model_dir, 1)
    }

    /// Create a new embedder with a custom pool size.
    pub fn with_pool_size(model_dir: &str, pool_size: usize) -> Result<Self, String> {
        let dir = Path::new(model_dir);
        let model_path = dir.join("model.onnx");
        let tokenizer_path = dir.join("tokenizer.json");

        if !model_path.exists() {
            return Err(format!("ONNX model not found: {}", model_path.display()));
        }
        if !tokenizer_path.exists() {
            return Err(format!("Tokenizer not found: {}", tokenizer_path.display()));
        }

        let pool_size = pool_size.max(1);
        let mut pool = Vec::with_capacity(pool_size);
        let mut needs_token_type_ids = false;
        let mut output_name = String::new();

        for i in 0..pool_size {
            let session = Self::create_session(&model_path)?;
            let tokenizer = Self::create_tokenizer(&tokenizer_path)?;

            if i == 0 {
                needs_token_type_ids = session
                    .inputs()
                    .iter()
                    .any(|inp| inp.name() == "token_type_ids");

                let outputs = session.outputs();
                output_name = if outputs.len() == 1 {
                    outputs[0].name().to_string()
                } else {
                    outputs
                        .iter()
                        .find(|o| o.name() == "last_hidden_state")
                        .map(|o| o.name().to_string())
                        .unwrap_or_else(|| outputs[0].name().to_string())
                };
            }

            pool.push(Mutex::new(Slot { session, tokenizer }));
        }

        Ok(Self {
            pool,
            needs_token_type_ids,
            output_name,
        })
    }

    fn create_session(model_path: &PathBuf) -> Result<Session, String> {
        Session::builder()
            .map_err(|e| format!("Session::builder(): {}", e))?
            .with_intra_threads(1)
            .map_err(|e| format!("with_intra_threads: {}", e))?
            .with_optimization_level(GraphOptimizationLevel::Level3)
            .map_err(|e| format!("with_optimization_level: {}", e))?
            .commit_from_file(model_path)
            .map_err(|e| format!("commit_from_file: {}", e))
    }

    fn create_tokenizer(tokenizer_path: &PathBuf) -> Result<Tokenizer, String> {
        let mut tokenizer = Tokenizer::from_file(tokenizer_path)
            .map_err(|e| format!("Failed to load tokenizer: {}", e))?;

        let pad_id = tokenizer.token_to_id("[PAD]").unwrap_or(0);
        tokenizer.with_padding(Some(PaddingParams {
            strategy: PaddingStrategy::BatchLongest,
            direction: PaddingDirection::Right,
            pad_id,
            pad_token: "[PAD]".to_string(),
            ..Default::default()
        }));
        tokenizer
            .with_truncation(Some(tokenizers::TruncationParams {
                max_length: 512,
                ..Default::default()
            }))
            .ok();

        Ok(tokenizer)
    }

    /// Embed texts, returning one f32 vector per input text (mean-pooled + L2-normalized).
    pub fn embed(&self, texts: &[&str]) -> Result<Vec<Vec<f32>>, String> {
        if texts.is_empty() {
            return Ok(vec![]);
        }
        self.embed_raw(texts)
    }

    /// Raw ONNX inference without cache.
    fn embed_raw(&self, texts: &[&str]) -> Result<Vec<Vec<f32>>, String> {
        if texts.is_empty() {
            return Ok(vec![]);
        }

        // Grab any available slot from the pool
        let mut slot_guard = None;
        for slot in &self.pool {
            if let Ok(guard) = slot.try_lock() {
                slot_guard = Some(guard);
                break;
            }
        }
        if slot_guard.is_none() {
            // All slots busy — block on the first one
            slot_guard = Some(self.pool[0].lock().unwrap());
        }
        let slot = slot_guard.as_mut().unwrap();

        let encodings = slot
            .tokenizer
            .encode_batch(texts.to_vec(), true)
            .map_err(|e| format!("Tokenization failed: {}", e))?;

        let batch_size = encodings.len();
        let seq_len = encodings[0].get_ids().len();

        let input_ids_flat: Vec<i64> = encodings
            .iter()
            .flat_map(|enc| enc.get_ids().iter().map(|&id| id as i64))
            .collect();
        let attention_mask_flat: Vec<i64> = encodings
            .iter()
            .flat_map(|enc| enc.get_attention_mask().iter().map(|&m| m as i64))
            .collect();

        let input_ids =
            Array2::<i64>::from_shape_vec((batch_size, seq_len), input_ids_flat.clone())
                .map_err(|e| format!("input_ids shape: {}", e))?;
        let attention_mask =
            Array2::<i64>::from_shape_vec((batch_size, seq_len), attention_mask_flat.clone())
                .map_err(|e| format!("attention_mask shape: {}", e))?;

        let input_ids_tensor = Tensor::from_array(input_ids.into_dyn())
            .map_err(|e| format!("input_ids tensor: {}", e))?;
        let attention_mask_tensor = Tensor::from_array(attention_mask.into_dyn())
            .map_err(|e| format!("attention_mask tensor: {}", e))?;

        let outputs = if self.needs_token_type_ids {
            let token_type_ids = Array2::<i64>::zeros((batch_size, seq_len));
            let token_type_ids_tensor = Tensor::from_array(token_type_ids.into_dyn())
                .map_err(|e| format!("token_type_ids tensor: {}", e))?;
            slot.session
                .run(ort::inputs![
                    "input_ids" => input_ids_tensor,
                    "attention_mask" => attention_mask_tensor,
                    "token_type_ids" => token_type_ids_tensor
                ])
                .map_err(|e| format!("session.run: {}", e))?
        } else {
            slot.session
                .run(ort::inputs![
                    "input_ids" => input_ids_tensor,
                    "attention_mask" => attention_mask_tensor
                ])
                .map_err(|e| format!("session.run: {}", e))?
        };

        let (hidden_shape, hidden_data) = outputs[self.output_name.as_str()]
            .try_extract_tensor::<f32>()
            .map_err(|e| format!("extract output: {}", e))?;

        if hidden_shape.len() != 3 {
            return Err(format!(
                "Expected 3D output [batch, seq, hidden], got {:?}",
                hidden_shape
            ));
        }
        let hidden_dim = hidden_shape[2] as usize;

        // Mean pooling with attention mask + L2 normalization
        let mut embeddings = Vec::with_capacity(batch_size);
        for i in 0..batch_size {
            let mut pooled = vec![0.0f32; hidden_dim];
            let mask_start = i * seq_len;
            let mut mask_sum = 0.0f64;

            for t in 0..seq_len {
                let m = attention_mask_flat[mask_start + t] as f64;
                mask_sum += m;
                if m > 0.0 {
                    let offset = i * seq_len * hidden_dim + t * hidden_dim;
                    for d in 0..hidden_dim {
                        pooled[d] += hidden_data[offset + d];
                    }
                }
            }

            let divisor = if mask_sum > 1e-9 { mask_sum as f32 } else { 1e-9 };
            for d in 0..hidden_dim {
                pooled[d] /= divisor;
            }

            // L2 normalization
            let norm: f32 = pooled.iter().map(|x| x * x).sum::<f32>().sqrt();
            if norm > 1e-12 {
                for d in 0..hidden_dim {
                    pooled[d] /= norm;
                }
            }

            embeddings.push(pooled);
        }

        Ok(embeddings)
    }

}

/// Embed texts using a local ONNX model (pure Rust inference).
///
/// `config.model` must be a path to a local directory containing:
/// - `model.onnx` — the ONNX model file
/// - `tokenizer.json` — HuggingFace tokenizer config
pub async fn embed(config: &EmbeddingConfig, texts: &[String]) -> ProviderResult<Value> {
    let model_dir = config.model.clone().ok_or_else(|| ProviderError {
        message: "ONNX embedding requires 'model' to be a local directory path".to_string(),
        status_code: None,
        error_code: None,
    })?;
    let texts = texts.to_vec();

    tokio::task::spawn_blocking(move || {
        let embedder = OnnxEmbedder::new(&model_dir).map_err(|e| ProviderError {
            message: e,
            status_code: None,
            error_code: None,
        })?;
        let text_refs: Vec<&str> = texts.iter().map(|s| s.as_str()).collect();
        let embeddings = embedder.embed(&text_refs).map_err(|e| ProviderError {
            message: e,
            status_code: None,
            error_code: None,
        })?;
        let embeddings_f64: Vec<Vec<f64>> = embeddings
            .into_iter()
            .map(|v| v.into_iter().map(|x| x as f64).collect())
            .collect();
        Ok(json!({ "embeddings": embeddings_f64 }))
    })
    .await
    .map_err(|e| ProviderError {
        message: format!("ONNX embedding task panicked: {}", e),
        status_code: None,
        error_code: None,
    })?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_onnx_embedder_missing_model() {
        let err = match OnnxEmbedder::new("/nonexistent/model/dir") {
            Err(e) => e,
            Ok(_) => panic!("expected error"),
        };
        assert!(err.contains("ONNX model not found"));
    }

    #[test]
    fn test_onnx_embedder_missing_tokenizer() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("model.onnx"), b"fake").unwrap();

        let err = match OnnxEmbedder::new(dir.path().to_str().unwrap()) {
            Err(e) => e,
            Ok(_) => panic!("expected error"),
        };
        assert!(err.contains("Tokenizer not found"));
    }

    #[test]
    fn test_embed_invalid_tokenizer() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("model.onnx"), b"fake").unwrap();
        std::fs::write(dir.path().join("tokenizer.json"), b"{}").unwrap();
        assert!(OnnxEmbedder::new(dir.path().to_str().unwrap()).is_err());
    }

    #[test]
    fn test_hash_text_deterministic() {
        let h1 = hash_text("hello world");
        let h2 = hash_text("hello world");
        let h3 = hash_text("different text");
        assert_eq!(h1, h2);
        assert_ne!(h1, h3);
    }

    #[test]
    fn test_cache_save_load_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let cache_path = dir.path().join("test_cache.bin");

        // Create a cache with some data
        let map = DashMap::new();
        map.insert(123u64, vec![1.0f32, 2.0, 3.0]);
        map.insert(456u64, vec![4.0f32, 5.0, 6.0]);
        let cache = EmbeddingCache {
            map,
            path: cache_path.clone(),
        };

        // Save
        let embedder_stub = OnnxEmbedder {
            pool: vec![],
            needs_token_type_ids: false,
            output_name: String::new(),
            cache: Some(cache),
        };
        let count = embedder_stub.save_cache().unwrap();
        assert_eq!(count, 2);

        // Load
        let loaded = OnnxEmbedder::load_cache_file(&cache_path);
        assert_eq!(loaded.len(), 2);
        assert_eq!(*loaded.get(&123u64).unwrap(), vec![1.0f32, 2.0, 3.0]);
        assert_eq!(*loaded.get(&456u64).unwrap(), vec![4.0f32, 5.0, 6.0]);
    }
}
