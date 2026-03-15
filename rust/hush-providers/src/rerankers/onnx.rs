//! ONNX reranker provider — pure Rust using `ort` + `tokenizers`.
//!
//! Mirrors hush-providers/hush/providers/rerankers/onnx.py:
//! - Loads model.onnx + tokenizer.json from a local directory
//! - Encodes query+text pairs with padding
//! - Runs ONNX inference → logits
//! - Applies sigmoid → scores in [0, 1]
//! - Filters by threshold, sorts descending, limits to top_k

use std::path::Path;
use std::sync::Mutex;

use ndarray::Array2;
use ort::session::{builder::GraphOptimizationLevel, Session};
use ort::value::Tensor;
use serde_json::{json, Value};
use tokenizers::{PaddingDirection, PaddingParams, PaddingStrategy, Tokenizer};

use crate::config::reranking::RerankingConfig;
use crate::http::{ProviderError, ProviderResult};

/// Rerank texts against a query using a local ONNX cross-encoder model.
///
/// `config.model` must be a path to a local directory containing:
/// - `model.onnx` — the ONNX cross-encoder model
/// - `tokenizer.json` — HuggingFace tokenizer config
pub async fn rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    let model_dir = config.model.clone().ok_or_else(|| ProviderError {
        message: "ONNX reranker requires 'model' to be a local directory path".to_string(),
        status_code: None,
        error_code: None,
    })?;
    let query = query.to_string();
    let texts = texts.to_vec();

    let raw_scores = tokio::task::spawn_blocking(move || rerank_sync(&model_dir, &query, &texts))
        .await
        .map_err(|e| ProviderError {
            message: format!("ONNX reranker task panicked: {}", e),
            status_code: None,
            error_code: None,
        })??;

    // Filter by threshold, sort descending, apply top_k
    let results = super::filter_and_sort(raw_scores, top_k, threshold);
    Ok(json!({ "results": results }))
}

/// Synchronous reranking pipeline.
fn rerank_sync(
    model_dir: &str,
    query: &str,
    texts: &[String],
) -> ProviderResult<Vec<(usize, f64)>> {
    if texts.is_empty() {
        return Ok(vec![]);
    }

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

    // 1. Load tokenizer with padding
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

    tokenizer
        .with_truncation(Some(tokenizers::TruncationParams {
            max_length: 512,
            ..Default::default()
        }))
        .ok();

    // 2. Encode query+text pairs (concatenated, matching Python implementation)
    let pairs: Vec<String> = texts
        .iter()
        .map(|text| format!("{} {}", query, text))
        .collect();
    let pair_refs: Vec<&str> = pairs.iter().map(|s| s.as_str()).collect();

    let encodings = tokenizer
        .encode_batch(pair_refs, true)
        .map_err(|e| ProviderError {
            message: format!("Tokenization failed: {}", e),
            status_code: None,
            error_code: None,
        })?;

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
        Array2::<i64>::from_shape_vec((batch_size, seq_len), input_ids_flat).map_err(|e| {
            ProviderError {
                message: format!("Failed to build input_ids array: {}", e),
                status_code: None,
                error_code: None,
            }
        })?;
    let attention_mask =
        Array2::<i64>::from_shape_vec((batch_size, seq_len), attention_mask_flat).map_err(|e| {
            ProviderError {
                message: format!("Failed to build attention_mask array: {}", e),
                status_code: None,
                error_code: None,
            }
        })?;

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
        Tensor::from_array(input_ids.into_dyn()).map_err(|e| ort_error("input_ids tensor", e))?;
    let attention_mask_tensor = Tensor::from_array(attention_mask.into_dyn())
        .map_err(|e| ort_error("attention_mask tensor", e))?;

    let needs_token_type_ids = session
        .inputs()
        .iter()
        .any(|i| i.name() == "token_type_ids");

    // Get output names before run (borrow checker: run() takes &mut self)
    let first_output_name: String = session.outputs()[0].name().to_string();

    let mut session = Mutex::new(session);
    let sess = session.get_mut().unwrap();

    let outputs = if needs_token_type_ids {
        let token_type_ids = Array2::<i64>::zeros((batch_size, seq_len));
        let token_type_ids_tensor = Tensor::from_array(token_type_ids.into_dyn())
            .map_err(|e| ort_error("token_type_ids tensor", e))?;

        sess.run(ort::inputs![
            "input_ids" => input_ids_tensor,
            "attention_mask" => attention_mask_tensor,
            "token_type_ids" => token_type_ids_tensor
        ])
        .map_err(|e| ort_error("session.run", e))?
    } else {
        sess.run(ort::inputs![
            "input_id" => input_ids_tensor,
            "attention_mask" => attention_mask_tensor
        ])
        .map_err(|e| ort_error("session.run", e))?
    };

    // 5. Extract logits
    let (shape, logit_data) = outputs[first_output_name.as_str()]
        .try_extract_tensor::<f32>()
        .map_err(|e| ort_error("extract logits", e))?;

    // 6. Handle output shapes and apply sigmoid
    let scores: Vec<f64> = match shape.len() {
        // 1D: [batch_size] — raw logits
        1 => logit_data.iter().map(|&x| sigmoid(x as f64)).collect(),
        // 2D: [batch_size, num_classes]
        2 => {
            let num_classes = shape[1] as usize;
            (0..batch_size)
                .map(|i| {
                    let logit = if num_classes == 1 {
                        logit_data[i * num_classes] as f64
                    } else {
                        // Take the last class (positive class)
                        logit_data[i * num_classes + num_classes - 1] as f64
                    };
                    sigmoid(logit)
                })
                .collect()
        }
        _ => {
            return Err(ProviderError {
                message: format!("Unexpected logits shape: {:?}", shape),
                status_code: None,
                error_code: None,
            });
        }
    };

    // 7. Build (index, score) pairs
    let results: Vec<(usize, f64)> = scores.into_iter().enumerate().collect();
    Ok(results)
}

/// Sigmoid activation: 1 / (1 + exp(-x))
fn sigmoid(x: f64) -> f64 {
    1.0 / (1.0 + (-x).exp())
}

fn ort_error(context: &str, e: impl std::fmt::Display) -> ProviderError {
    ProviderError {
        message: format!("ONNX Runtime error ({}): {}", context, e),
        status_code: None,
        error_code: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // =========================================================================
    // sigmoid tests
    // =========================================================================

    #[test]
    fn test_sigmoid_zero() {
        assert!((sigmoid(0.0) - 0.5).abs() < 1e-10);
    }

    #[test]
    fn test_sigmoid_large_positive() {
        assert!((sigmoid(10.0) - 1.0).abs() < 1e-4);
    }

    #[test]
    fn test_sigmoid_large_negative() {
        assert!(sigmoid(-10.0) < 1e-4);
    }

    #[test]
    fn test_sigmoid_one() {
        let expected = 1.0 / (1.0 + (-1.0f64).exp());
        assert!((sigmoid(1.0) - expected).abs() < 1e-10);
    }

    #[test]
    fn test_sigmoid_symmetry() {
        // sigmoid(x) + sigmoid(-x) = 1
        for x in [0.5, 1.0, 2.0, 5.0] {
            let sum = sigmoid(x) + sigmoid(-x);
            assert!((sum - 1.0).abs() < 1e-10);
        }
    }

    #[test]
    fn test_sigmoid_range() {
        // sigmoid output is always in [0, 1]
        for x in [-100.0, -10.0, -1.0, 0.0, 1.0, 10.0, 100.0] {
            let s = sigmoid(x);
            assert!(
                s >= 0.0 && s <= 1.0,
                "sigmoid({}) = {} not in [0,1]",
                x,
                s
            );
        }
        // For moderate values, strictly (0, 1)
        for x in [-10.0, -1.0, 0.0, 1.0, 10.0] {
            let s = sigmoid(x);
            assert!(s > 0.0 && s < 1.0, "sigmoid({}) = {} not in (0,1)", x, s);
        }
    }

    // =========================================================================
    // rerank_sync error path tests
    // =========================================================================

    #[test]
    fn test_rerank_sync_empty_texts() {
        let result = rerank_sync("/nonexistent", "query", &[]);
        assert!(result.is_ok());
        assert!(result.unwrap().is_empty());
    }

    #[test]
    fn test_rerank_sync_missing_model() {
        let texts = vec!["hello".to_string()];
        let result = rerank_sync("/nonexistent/model/dir", "query", &texts);
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .message
            .contains("ONNX model not found"));
    }

    #[test]
    fn test_rerank_sync_missing_tokenizer() {
        // Create a temp dir with model.onnx but no tokenizer.json
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("model.onnx"), b"fake").unwrap();

        let texts = vec!["hello".to_string()];
        let result = rerank_sync(dir.path().to_str().unwrap(), "query", &texts);
        assert!(result.is_err());
        assert!(result.unwrap_err().message.contains("Tokenizer not found"));
    }
}
