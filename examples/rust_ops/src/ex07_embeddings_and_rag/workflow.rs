//! ex07_embeddings_and_rag — retrieval ops for RAG pipelines.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// retrieve: query_vec, doc_vectors, documents -> context_docs (cosine similarity top 3)
#[hush_op]
pub fn retrieve(inputs: &Value) -> Value {
    let query_vec_outer = inputs["query_vec"].as_array().cloned().unwrap_or_default();
    let doc_vectors = inputs["doc_vectors"].as_array().cloned().unwrap_or_default();
    let documents = inputs["documents"].as_array().cloned().unwrap_or_default();

    // query_vec is typically [[...]] from EmbeddingOp — take the first element
    let query_vec: Vec<f64> = if let Some(first) = query_vec_outer.first() {
        first
            .as_array()
            .map(|arr| arr.iter().filter_map(|v| v.as_f64()).collect())
            .unwrap_or_default()
    } else {
        return json!({"context_docs": []});
    };

    let q_norm = vec_norm(&query_vec);
    if q_norm == 0.0 {
        return json!({"context_docs": []});
    }

    let mut scored: Vec<(f64, usize)> = doc_vectors
        .iter()
        .enumerate()
        .map(|(i, dv)| {
            let doc_vec: Vec<f64> = dv
                .as_array()
                .map(|arr| arr.iter().filter_map(|v| v.as_f64()).collect())
                .unwrap_or_default();
            let d_norm = vec_norm(&doc_vec);
            let sim = if d_norm > 0.0 {
                dot_product(&query_vec, &doc_vec) / (q_norm * d_norm)
            } else {
                0.0
            };
            (sim, i)
        })
        .collect();

    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

    let context_docs: Vec<Value> = scored
        .into_iter()
        .take(3)
        .filter_map(|(_, i)| documents.get(i).cloned())
        .collect();

    json!({"context_docs": context_docs})
}

fn dot_product(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

fn vec_norm(v: &[f64]) -> f64 {
    v.iter().map(|x| x * x).sum::<f64>().sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_retrieve_empty() {
        let result = retrieve(&json!({
            "query_vec": [],
            "doc_vectors": [],
            "documents": [],
        }));
        let docs = result["context_docs"].as_array().unwrap();
        assert!(docs.is_empty());
    }
}
