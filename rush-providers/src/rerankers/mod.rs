//! Reranker providers — per-provider implementations mirroring hush-providers/rerankers/.
//!
//! Dispatches by api_type to the appropriate provider:
//! - vLLM → vllm.rs (native HTTP)
//! - Pinecone → pinecone.rs (native HTTP)
//! - Cohere → cohere.rs (native HTTP)
//! - HuggingFace → huggingface.rs (stub — use ONNX instead)
//! - ONNX → onnx.rs (pure Rust: ort + tokenizers)

pub mod cohere;
pub mod huggingface;
#[cfg(feature = "onnx")]
pub mod onnx;
pub mod pinecone;
pub mod vllm;

use serde_json::{json, Value};

use crate::config::reranking::RerankingConfig;
use crate::http::{ProviderError, ProviderResult};

/// Dispatch reranking to the appropriate provider based on api_type.
pub async fn rerank(
    config: &RerankingConfig,
    query: &str,
    texts: &[String],
    top_k: Option<usize>,
    threshold: f64,
) -> ProviderResult<Value> {
    match config.api_type.as_str() {
        "vllm" => vllm::rerank(config, query, texts, top_k, threshold).await,
        "pinecone" => pinecone::rerank(config, query, texts, top_k, threshold).await,
        "cohere" => cohere::rerank(config, query, texts, top_k, threshold).await,
        "hf" => huggingface::rerank(config, query, texts, top_k, threshold).await,
        #[cfg(feature = "onnx")]
        "onnx" => onnx::rerank(config, query, texts, top_k, threshold).await,
        other => Err(ProviderError {
            message: format!(
                "Unsupported reranking api_type: '{}'. Supported: vllm, pinecone, cohere, hf, onnx",
                other
            ),
            status_code: None,
            error_code: None,
        }),
    }
}

/// Filter results by threshold, sort by score descending, limit to top_k.
///
/// Shared helper used by all HTTP-based reranker providers.
pub(crate) fn filter_and_sort(
    raw: Vec<(usize, f64)>,
    top_k: Option<usize>,
    threshold: f64,
) -> Vec<Value> {
    let mut results: Vec<(usize, f64)> = raw
        .into_iter()
        .filter(|(_, score)| *score >= threshold)
        .collect();

    results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    if let Some(k) = top_k {
        if k > 0 {
            results.truncate(k);
        }
    }

    results
        .into_iter()
        .map(|(index, score)| json!({"index": index, "score": score}))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // =========================================================================
    // filter_and_sort tests
    // =========================================================================

    #[test]
    fn test_filter_and_sort_basic() {
        let raw = vec![(0, 0.9), (1, 0.5), (2, 0.8)];
        let result = filter_and_sort(raw, None, 0.0);

        // Should be sorted descending
        assert_eq!(result.len(), 3);
        assert_eq!(result[0]["index"], 0);
        assert_eq!(result[0]["score"], 0.9);
        assert_eq!(result[1]["index"], 2);
        assert_eq!(result[1]["score"], 0.8);
        assert_eq!(result[2]["index"], 1);
        assert_eq!(result[2]["score"], 0.5);
    }

    #[test]
    fn test_filter_and_sort_with_threshold() {
        let raw = vec![(0, 0.9), (1, 0.3), (2, 0.7), (3, 0.1)];
        let result = filter_and_sort(raw, None, 0.5);

        // Only scores >= 0.5 should remain
        assert_eq!(result.len(), 2);
        assert_eq!(result[0]["index"], 0);
        assert_eq!(result[1]["index"], 2);
    }

    #[test]
    fn test_filter_and_sort_with_top_k() {
        let raw = vec![(0, 0.9), (1, 0.8), (2, 0.7), (3, 0.6)];
        let result = filter_and_sort(raw, Some(2), 0.0);

        assert_eq!(result.len(), 2);
        assert_eq!(result[0]["index"], 0);
        assert_eq!(result[1]["index"], 1);
    }

    #[test]
    fn test_filter_and_sort_threshold_and_top_k() {
        let raw = vec![(0, 0.95), (1, 0.2), (2, 0.85), (3, 0.6), (4, 0.1)];
        let result = filter_and_sort(raw, Some(2), 0.5);

        // Filter by threshold first (0.95, 0.85, 0.6), then top_k=2
        assert_eq!(result.len(), 2);
        assert_eq!(result[0]["index"], 0);
        assert_eq!(result[0]["score"], 0.95);
        assert_eq!(result[1]["index"], 2);
        assert_eq!(result[1]["score"], 0.85);
    }

    #[test]
    fn test_filter_and_sort_empty() {
        let raw: Vec<(usize, f64)> = vec![];
        let result = filter_and_sort(raw, None, 0.0);
        assert!(result.is_empty());
    }

    #[test]
    fn test_filter_and_sort_all_filtered() {
        let raw = vec![(0, 0.1), (1, 0.2)];
        let result = filter_and_sort(raw, None, 0.5);
        assert!(result.is_empty());
    }

    #[test]
    fn test_filter_and_sort_top_k_zero() {
        // top_k=0 should not truncate (treated as "no limit")
        let raw = vec![(0, 0.9), (1, 0.8)];
        let result = filter_and_sort(raw, Some(0), 0.0);
        assert_eq!(result.len(), 2);
    }

    #[test]
    fn test_filter_and_sort_top_k_larger_than_results() {
        let raw = vec![(0, 0.9), (1, 0.8)];
        let result = filter_and_sort(raw, Some(10), 0.0);
        assert_eq!(result.len(), 2);
    }

    #[test]
    fn test_filter_and_sort_equal_scores() {
        let raw = vec![(0, 0.5), (1, 0.5), (2, 0.5)];
        let result = filter_and_sort(raw, None, 0.0);
        assert_eq!(result.len(), 3);
    }

    #[test]
    fn test_filter_and_sort_exact_threshold() {
        // Score exactly at threshold should be included
        let raw = vec![(0, 0.5), (1, 0.49)];
        let result = filter_and_sort(raw, None, 0.5);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0]["index"], 0);
    }

    #[test]
    fn test_filter_and_sort_output_format() {
        let raw = vec![(3, 0.75)];
        let result = filter_and_sort(raw, None, 0.0);
        assert_eq!(result[0], json!({"index": 3, "score": 0.75}));
    }
}
