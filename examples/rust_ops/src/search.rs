//! Search ops — keyword search, cosine similarity, RRF, and retrieval for RAG pipelines.

use serde_json::{json, Value};
use std::collections::HashMap;

/// keyword_search: query, docs → results (top docs by term overlap)
pub fn keyword_search(inputs: &Value) -> Value {
    let query = inputs["query"].as_str().unwrap_or("");
    let docs = inputs["docs"].as_array().cloned().unwrap_or_default();
    let top_k = inputs["top_k"].as_u64().unwrap_or(5) as usize;

    let query_terms: Vec<String> = query.split_whitespace().map(|w| w.to_lowercase()).collect();
    let mut scored: Vec<(usize, &Value)> = docs
        .iter()
        .map(|doc| {
            let text = doc.as_str().unwrap_or("");
            let doc_terms: Vec<String> = text.split_whitespace().map(|w| w.to_lowercase()).collect();
            let overlap = query_terms.iter().filter(|qt| doc_terms.contains(qt)).count();
            (overlap, doc)
        })
        .filter(|(overlap, _)| *overlap > 0)
        .collect();

    scored.sort_by(|a, b| b.0.cmp(&a.0));
    let results: Vec<Value> = scored.into_iter().take(top_k).map(|(_, doc)| doc.clone()).collect();
    json!({"results": results})
}

/// keyword_search_expanded: query, docs → results (search with expanded query terms)
pub fn keyword_search_expanded(inputs: &Value) -> Value {
    let query = inputs["query"].as_str().unwrap_or("");
    let docs = inputs["docs"].as_array().cloned().unwrap_or_default();
    let top_k = inputs["top_k"].as_u64().unwrap_or(5) as usize;

    // Expand query with extra terms
    let expanded = format!("{} thành phố du lịch", query);
    let query_terms: Vec<String> = expanded.split_whitespace().map(|w| w.to_lowercase()).collect();

    let mut scored: Vec<(usize, &Value)> = docs
        .iter()
        .map(|doc| {
            let text = doc.as_str().unwrap_or("");
            let doc_terms: Vec<String> = text.split_whitespace().map(|w| w.to_lowercase()).collect();
            let overlap = query_terms.iter().filter(|qt| doc_terms.contains(qt)).count();
            (overlap, doc)
        })
        .filter(|(overlap, _)| *overlap > 0)
        .collect();

    scored.sort_by(|a, b| b.0.cmp(&a.0));
    let results: Vec<Value> = scored.into_iter().take(top_k).map(|(_, doc)| doc.clone()).collect();
    json!({"results": results})
}

/// rrf_merge: r1, r2 → merged (reciprocal rank fusion)
pub fn rrf_merge(inputs: &Value) -> Value {
    let r1 = inputs["r1"].as_array().cloned().unwrap_or_default();
    let r2 = inputs["r2"].as_array().cloned().unwrap_or_default();
    let k = 60.0_f64;

    let mut scores: HashMap<String, f64> = HashMap::new();

    for (rank, doc) in r1.iter().enumerate() {
        let key = doc.to_string();
        *scores.entry(key).or_insert(0.0) += 1.0 / (k + rank as f64 + 1.0);
    }
    for (rank, doc) in r2.iter().enumerate() {
        let key = doc.to_string();
        *scores.entry(key).or_insert(0.0) += 1.0 / (k + rank as f64 + 1.0);
    }

    let mut sorted: Vec<(String, f64)> = scores.into_iter().collect();
    sorted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    // Parse back from string keys to JSON values
    let merged: Vec<Value> = sorted
        .into_iter()
        .filter_map(|(key, _)| serde_json::from_str(&key).ok())
        .collect();

    json!({"merged": merged})
}

/// cosine_search: qv, docs, dvs → results (cosine similarity search)
///
/// qv: query vector (first element used), docs: document strings, dvs: document vectors
pub fn cosine_search(inputs: &Value) -> Value {
    let qv = inputs["qv"].as_array().cloned().unwrap_or_default();
    let docs = inputs["docs"].as_array().cloned().unwrap_or_default();
    let dvs = inputs["dvs"].as_array().cloned().unwrap_or_default();
    let top_k = inputs["top_k"].as_u64().unwrap_or(5) as usize;

    // Extract the first query vector
    let query_vec: Vec<f64> = if let Some(first) = qv.first() {
        first
            .as_array()
            .map(|arr| arr.iter().filter_map(|v| v.as_f64()).collect())
            .unwrap_or_else(|| qv.iter().filter_map(|v| v.as_f64()).collect())
    } else {
        return json!({"results": []});
    };

    let q_norm = vec_norm(&query_vec);
    if q_norm == 0.0 {
        return json!({"results": []});
    }

    let mut scored: Vec<(f64, usize)> = dvs
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

    let results: Vec<Value> = scored
        .into_iter()
        .take(top_k)
        .filter_map(|(_, i)| docs.get(i).cloned())
        .collect();

    json!({"results": results})
}

/// keyword_retrieve: query, docs → candidates (keyword search top 8)
pub fn keyword_retrieve(inputs: &Value) -> Value {
    let query = inputs["query"].as_str().unwrap_or("");
    let docs = inputs["docs"].as_array().cloned().unwrap_or_default();

    let query_terms: Vec<String> = query.split_whitespace().map(|w| w.to_lowercase()).collect();
    let mut scored: Vec<(usize, &Value)> = docs
        .iter()
        .map(|doc| {
            let text = doc.as_str().unwrap_or("");
            let doc_terms: Vec<String> = text.split_whitespace().map(|w| w.to_lowercase()).collect();
            let overlap = query_terms.iter().filter(|qt| doc_terms.contains(qt)).count();
            (overlap, doc)
        })
        .filter(|(overlap, _)| *overlap > 0)
        .collect();

    scored.sort_by(|a, b| b.0.cmp(&a.0));
    let candidates: Vec<Value> = scored.into_iter().take(8).map(|(_, doc)| doc.clone()).collect();
    json!({"candidates": candidates})
}

/// retrieve: query_vec, doc_vectors, documents → context_docs (cosine similarity top 3)
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
    fn test_keyword_search() {
        let result = keyword_search(&json!({
            "query": "biển đẹp",
            "docs": ["bãi biển Mỹ Khê rất đẹp", "núi cao hùng vĩ", "biển xanh cát trắng"],
        }));
        let results = result["results"].as_array().unwrap();
        assert!(!results.is_empty());
        assert!(results[0].as_str().unwrap().contains("biển"));
    }

    #[test]
    fn test_keyword_search_no_match() {
        let result = keyword_search(&json!({
            "query": "xyz",
            "docs": ["hello world", "foo bar"],
        }));
        let results = result["results"].as_array().unwrap();
        assert!(results.is_empty());
    }

    #[test]
    fn test_rrf_merge() {
        let result = rrf_merge(&json!({
            "r1": ["doc_a", "doc_b", "doc_c"],
            "r2": ["doc_b", "doc_c", "doc_d"],
        }));
        let merged = result["merged"].as_array().unwrap();
        assert!(!merged.is_empty());
        // doc_b and doc_c appear in both lists, should rank higher
        assert!(merged.len() >= 3);
    }

    #[test]
    fn test_keyword_retrieve() {
        let result = keyword_retrieve(&json!({
            "query": "biển",
            "docs": ["bãi biển đẹp", "núi cao", "biển xanh", "rừng rậm"],
        }));
        let candidates = result["candidates"].as_array().unwrap();
        assert_eq!(candidates.len(), 2);
    }

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

    #[test]
    fn test_cosine_search_basic() {
        let result = cosine_search(&json!({
            "qv": [[1.0, 0.0, 0.0]],
            "docs": ["doc_a", "doc_b"],
            "dvs": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        }));
        let results = result["results"].as_array().unwrap();
        assert_eq!(results[0], "doc_a");
    }
}
