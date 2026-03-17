//! ONNX inference op — runs arbitrary ONNX models via session pool.
//!
//! Inputs:
//!   - embeddings: list of float vectors (required)
//!   - role_ids: list of ints (required for attention type)
//!   - mask: list of bools (required for attention type)
//!
//! Outputs:
//!   - logits: list of raw logit values
//!   - probabilities: list of sigmoid probabilities

use serde_json::{json, Value};

use crate::config::onnx::{OnnxInferenceConfig, OnnxInputType};
use crate::http::ProviderResult;
use crate::onnx;

pub async fn execute(inputs: Value, config: &OnnxInferenceConfig) -> ProviderResult<Value> {
    let pool = onnx::get_pool(config).map_err(|e| crate::http::ProviderError {
        message: format!("Failed to get ONNX pool: {}", e),
        status_code: None,
        error_code: None,
    })?;

    // Extract embeddings from inputs
    let embeddings = extract_embeddings(&inputs)?;

    match config.input_type {
        OnnxInputType::Mlp => {
            let probs = pool.predict_mlp(&embeddings).map_err(provider_err)?;
            Ok(json!({
                "probabilities": probs,
            }))
        }
        OnnxInputType::Attention => {
            let role_ids = extract_i64_array(&inputs, "role_ids")?;
            let mask = extract_bool_array(&inputs, "mask")?;

            let prob = pool.predict_sequence(&embeddings, &role_ids, &mask)
                .map_err(provider_err)?;
            Ok(json!({
                "probability": prob,
            }))
        }
    }
}

fn extract_embeddings(inputs: &Value) -> ProviderResult<Vec<Vec<f32>>> {
    let arr = inputs["embeddings"]
        .as_array()
        .ok_or_else(|| provider_err_str("Missing or invalid 'embeddings' input"))?;

    arr.iter()
        .map(|v| {
            v.as_array()
                .ok_or_else(|| provider_err_str("Each embedding must be an array of floats"))
                .and_then(|a| {
                    a.iter()
                        .map(|f| {
                            f.as_f64()
                                .map(|f| f as f32)
                                .ok_or_else(|| provider_err_str("Embedding value must be a number"))
                        })
                        .collect()
                })
        })
        .collect()
}

fn extract_i64_array(inputs: &Value, key: &str) -> ProviderResult<Vec<i64>> {
    let arr = inputs[key]
        .as_array()
        .ok_or_else(|| provider_err_str(&format!("Missing or invalid '{}' input", key)))?;

    arr.iter()
        .map(|v| {
            v.as_i64()
                .ok_or_else(|| provider_err_str(&format!("'{}' values must be integers", key)))
        })
        .collect()
}

fn extract_bool_array(inputs: &Value, key: &str) -> ProviderResult<Vec<bool>> {
    let arr = inputs[key]
        .as_array()
        .ok_or_else(|| provider_err_str(&format!("Missing or invalid '{}' input", key)))?;

    arr.iter()
        .map(|v| {
            v.as_bool()
                .ok_or_else(|| provider_err_str(&format!("'{}' values must be booleans", key)))
        })
        .collect()
}

fn provider_err(msg: String) -> crate::http::ProviderError {
    crate::http::ProviderError {
        message: msg,
        status_code: None,
        error_code: None,
    }
}

fn provider_err_str(msg: &str) -> crate::http::ProviderError {
    provider_err(msg.to_string())
}
