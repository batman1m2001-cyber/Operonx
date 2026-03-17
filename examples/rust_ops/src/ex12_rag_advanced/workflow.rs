//! ex12_rag_advanced — keyword search, expanded search, and RRF merge ops.

use hush_serve::hush_op;
use serde_json::{json, Value};
use std::collections::HashMap;

/// search_original: query, docs -> results (keyword search)
#[hush_op]
pub fn search_original(inputs: &Value) -> Value {
    let query = inputs["query"].as_str().unwrap_or("");
    let docs = inputs["docs"].as_array().cloned().unwrap_or_default();
    let top_k = 5_usize;

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

/// search_expanded: query, docs -> results (keyword search with expanded query)
#[hush_op]
pub fn search_expanded(inputs: &Value) -> Value {
    let query = inputs["query"].as_str().unwrap_or("");
    let docs = inputs["docs"].as_array().cloned().unwrap_or_default();
    let top_k = 5_usize;

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

/// rrf_merge: r1, r2 -> merged (reciprocal rank fusion)
#[hush_op]
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

    let merged: Vec<Value> = sorted
        .into_iter()
        .filter_map(|(key, _)| serde_json::from_str(&key).ok())
        .collect();

    json!({"merged": merged})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_search_original() {
        let result = search_original(&json!({
            "query": "biển đẹp",
            "docs": ["bãi biển Mỹ Khê rất đẹp", "núi cao hùng vĩ", "biển xanh cát trắng"],
        }));
        let results = result["results"].as_array().unwrap();
        assert!(!results.is_empty());
        assert!(results[0].as_str().unwrap().contains("biển"));
    }

    #[test]
    fn test_search_original_no_match() {
        let result = search_original(&json!({
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
        assert!(merged.len() >= 3);
    }
}
