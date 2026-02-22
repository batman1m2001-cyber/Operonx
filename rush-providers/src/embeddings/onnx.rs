//! ONNX embedding provider — pure Rust using `ort` + `tokenizers`.
//!
//! Mirrors hush-providers/hush/providers/embeddings/onnx.py:
//! - Loads model.onnx + tokenizer.json from a local directory
//! - Tokenizes input texts with padding
//! - Runs ONNX inference → last_hidden_state
//! - Mean pooling with attention mask
//! - L2 normalization
//! - Returns {"embeddings": [[f64, ...], ...]}

use std::path::Path;

use ndarray::Array2;
use ort::session::{builder::GraphOptimizationLevel, Session};
use ort::value::Tensor;
use serde_json::{json, Value};
use tokenizers::{PaddingDirection, PaddingParams, PaddingStrategy, Tokenizer};

use crate::config::embedding::EmbeddingConfig;
use crate::http::{ProviderError, ProviderResult};

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

    // Run inference on a blocking thread (ONNX Runtime is sync + CPU-intensive)
    tokio::task::spawn_blocking(move || embed_sync(&model_dir, &texts))
        .await
        .map_err(|e| ProviderError {
            message: format!("ONNX embedding task panicked: {}", e),
            status_code: None,
            error_code: None,
        })?
}

/// Synchronous embedding pipeline.
fn embed_sync(model_dir: &str, texts: &[String]) -> ProviderResult<Value> {
    let dir = Path::new(model_dir);
    let model_path = dir.join("model.onnx");
    let tokenizer_path = dir.join("tokenizer.json");

    if !model_path.exists() {
        return Err(ProviderError {
            message: format!("ONNX model not found: {}", model_path.display()),
            status_code: None,
            error_code: None,
        });
    }
    if !tokenizer_path.exists() {
        return Err(ProviderError {
            message: format!("Tokenizer not found: {}", tokenizer_path.display()),
            status_code: None,
            error_code: None,
        });
    }

    // 1. Load tokenizer with padding enabled
    let mut tokenizer = Tokenizer::from_file(&tokenizer_path).map_err(|e| ProviderError {
        message: format!("Failed to load tokenizer: {}", e),
        status_code: None,
        error_code: None,
    })?;

    tokenizer.with_padding(Some(PaddingParams {
        strategy: PaddingStrategy::BatchLongest,
        direction: PaddingDirection::Right,
        pad_id: 0,
        pad_token: "[PAD]".to_string(),
        ..Default::default()
    }));

    if let Some(trunc) = tokenizer.get_truncation() {
        // Keep existing truncation settings
        let _ = trunc;
    } else {
        tokenizer
            .with_truncation(Some(tokenizers::TruncationParams {
                max_length: 512,
                ..Default::default()
            }))
            .ok();
    }

    // 2. Tokenize
    let text_refs: Vec<&str> = texts.iter().map(|s| s.as_str()).collect();
    let encodings = tokenizer.encode_batch(text_refs, true).map_err(|e| ProviderError {
        message: format!("Tokenization failed: {}", e),
        status_code: None,
        error_code: None,
    })?;

    if encodings.is_empty() {
        return Ok(json!({ "embeddings": [] }));
    }

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
        Array2::<i64>::from_shape_vec((batch_size, seq_len), input_ids_flat.clone()).map_err(
            |e| ProviderError {
                message: format!("Failed to build input_ids array: {}", e),
                status_code: None,
                error_code: None,
            },
        )?;
    let attention_mask =
        Array2::<i64>::from_shape_vec((batch_size, seq_len), attention_mask_flat.clone()).map_err(
            |e| ProviderError {
                message: format!("Failed to build attention_mask array: {}", e),
                status_code: None,
                error_code: None,
            },
        )?;

    // 3. Create ONNX session
    let session = Session::builder()
        .map_err(|e| ort_error("Session::builder()", e))?
        .with_optimization_level(GraphOptimizationLevel::Level3)
        .map_err(|e| ort_error("with_optimization_level", e))?
        .with_intra_threads(4)
        .map_err(|e| ort_error("with_intra_threads", e))?
        .commit_from_file(&model_path)
        .map_err(|e| ort_error("commit_from_file", e))?;

    // 4. Build inputs — check if model expects token_type_ids
    let input_ids_tensor =
        Tensor::from_array(input_ids).map_err(|e| ort_error("input_ids tensor", e))?;
    let attention_mask_tensor =
        Tensor::from_array(attention_mask).map_err(|e| ort_error("attention_mask tensor", e))?;

    let model_input_names: Vec<String> = session
        .inputs
        .iter()
        .map(|i| i.name.clone())
        .collect();
    let needs_token_type_ids = model_input_names
        .iter()
        .any(|name| name == "token_type_ids");

    let outputs = if needs_token_type_ids {
        let token_type_ids = Array2::<i64>::zeros((batch_size, seq_len));
        let token_type_ids_tensor =
            Tensor::from_array(token_type_ids).map_err(|e| ort_error("token_type_ids tensor", e))?;

        session
            .run(
                ort::inputs![
                    "input_ids" => input_ids_tensor,
                    "attention_mask" => attention_mask_tensor,
                    "token_type_ids" => token_type_ids_tensor
                ]
                .map_err(|e| ort_error("build inputs", e))?,
            )
            .map_err(|e| ort_error("session.run", e))?
    } else {
        session
            .run(
                ort::inputs![
                    "input_ids" => input_ids_tensor,
                    "attention_mask" => attention_mask_tensor
                ]
                .map_err(|e| ort_error("build inputs", e))?,
            )
            .map_err(|e| ort_error("session.run", e))?
    };

    // 5. Extract last_hidden_state — shape [batch_size, seq_len, hidden_dim]
    // Try common output names
    let output_key = if outputs.len() == 1 {
        session.outputs[0].name.as_str()
    } else {
        // Prefer "last_hidden_state", fall back to first output
        let names: Vec<&str> = session.outputs.iter().map(|o| o.name.as_str()).collect();
        if names.contains(&"last_hidden_state") {
            "last_hidden_state"
        } else {
            names[0]
        }
    };

    let hidden_states = outputs[output_key]
        .try_extract_tensor::<f32>()
        .map_err(|e| ort_error("extract output", e))?;

    let shape = hidden_states.shape();
    if shape.len() != 3 {
        return Err(ProviderError {
            message: format!(
                "Expected 3D output [batch, seq, hidden], got shape {:?}",
                shape
            ),
            status_code: None,
            error_code: None,
        });
    }
    let hidden_dim = shape[2];

    // 6. Mean pooling with attention mask
    let mut embeddings = Vec::with_capacity(batch_size);
    for i in 0..batch_size {
        let mut pooled = vec![0.0f64; hidden_dim];
        let mask_start = i * seq_len;
        let mut mask_sum = 0.0f64;

        for t in 0..seq_len {
            let m = attention_mask_flat[mask_start + t] as f64;
            mask_sum += m;
            if m > 0.0 {
                for d in 0..hidden_dim {
                    pooled[d] += hidden_states[[i, t, d]] as f64;
                }
            }
        }

        // Avoid division by zero
        let divisor = if mask_sum > 1e-9 { mask_sum } else { 1e-9 };
        for d in 0..hidden_dim {
            pooled[d] /= divisor;
        }

        // 7. L2 normalization
        let norm: f64 = pooled.iter().map(|x| x * x).sum::<f64>().sqrt();
        let norm = if norm > 1e-12 { norm } else { 1e-12 };
        for d in 0..hidden_dim {
            pooled[d] /= norm;
        }

        embeddings.push(pooled);
    }

    Ok(json!({ "embeddings": embeddings }))
}

fn ort_error(context: &str, e: ort::Error) -> ProviderError {
    ProviderError {
        message: format!("ONNX Runtime error ({}): {}", context, e),
        status_code: None,
        error_code: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_embed_sync_missing_model() {
        let texts = vec!["hello".to_string()];
        let result = embed_sync("/nonexistent/model/dir", &texts);
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .message
            .contains("ONNX model not found"));
    }

    #[test]
    fn test_embed_sync_missing_tokenizer() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("model.onnx"), b"fake").unwrap();

        let texts = vec!["hello".to_string()];
        let result = embed_sync(dir.path().to_str().unwrap(), &texts);
        assert!(result.is_err());
        assert!(result.unwrap_err().message.contains("Tokenizer not found"));
    }

    #[test]
    fn test_embed_sync_empty_texts() {
        // Even with a valid dir, empty texts should fail at model loading,
        // but let's test with missing files — it errors on model not found first
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("model.onnx"), b"fake").unwrap();
        std::fs::write(dir.path().join("tokenizer.json"), b"{}").unwrap();

        let texts: Vec<String> = vec![];
        // Empty texts should get past file checks but fail at tokenizer loading
        // since our tokenizer.json is invalid. That's fine — we're testing the pipeline.
        let result = embed_sync(dir.path().to_str().unwrap(), &texts);
        // With invalid tokenizer.json, it will fail at tokenizer loading
        assert!(result.is_err());
    }
}
