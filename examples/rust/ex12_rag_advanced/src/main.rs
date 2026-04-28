//! 12 RAG Advanced — Rust-side demo.
//!
//! Keyword RRF (pure compute) + hybrid (vector + keyword) RAG.
//! Mirrors `examples/python/ex12_rag_advanced/main.py`.
//!
//! ⚠️  Rust-limited: the demo doesn't pre-compute `doc_vectors` here, so
//! the `hybrid` scenario receives a zero-vector stub and only the
//! keyword arm meaningfully contributes. Run the Python side for full
//! hybrid behaviour, or wire up a precompute step in your own
//! application.

use std::collections::HashSet;

use operonx::{op, BootstrapOpts, Operon};
use serde_json::Value;

fn keyword_search(query: &str, documents: &[String], top_k: usize) -> Vec<String> {
    let q_terms: HashSet<String> = query
        .to_lowercase()
        .split_whitespace()
        .map(String::from)
        .collect();
    let mut scored: Vec<(usize, &String)> = documents
        .iter()
        .filter_map(|doc| {
            let d_terms: HashSet<String> = doc
                .to_lowercase()
                .split_whitespace()
                .map(String::from)
                .collect();
            let overlap = q_terms.intersection(&d_terms).count();
            if overlap > 0 {
                Some((overlap, doc))
            } else {
                None
            }
        })
        .collect();
    scored.sort_by(|a, b| b.0.cmp(&a.0));
    scored
        .into_iter()
        .take(top_k)
        .map(|(_, d)| d.clone())
        .collect()
}

fn cosine_search(
    query_vec: &[f64],
    doc_vecs: &[Vec<f64>],
    documents: &[String],
    top_k: usize,
) -> Vec<String> {
    let qn: f64 = query_vec.iter().map(|x| x * x).sum::<f64>().sqrt();
    let mut scored: Vec<(f64, String)> = doc_vecs
        .iter()
        .zip(documents.iter())
        .map(|(dv, dtxt)| {
            let dn: f64 = dv.iter().map(|x| x * x).sum::<f64>().sqrt();
            let dot: f64 = query_vec.iter().zip(dv.iter()).map(|(a, b)| a * b).sum();
            let sim = if qn == 0.0 || dn == 0.0 { 0.0 } else { dot / (qn * dn) };
            (sim, dtxt.clone())
        })
        .collect();
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());
    scored.into_iter().take(top_k).map(|(_, d)| d).collect()
}

fn reciprocal_rank_fusion(lists: &[Vec<String>], k: usize) -> Vec<String> {
    let mut scores: Vec<(String, f64)> = Vec::new();
    for list in lists {
        for (rank, doc) in list.iter().enumerate() {
            let score = 1.0 / (k as f64 + rank as f64 + 1.0);
            match scores.iter_mut().find(|(d, _)| d == doc) {
                Some((_, s)) => *s += score,
                None => scores.push((doc.clone(), score)),
            }
        }
    }
    scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    scores.into_iter().map(|(d, _)| d).collect()
}

#[op(name = "search_original")]
fn search_original(query: String, docs: Vec<String>) -> Value {
    serde_json::json!({ "results": keyword_search(&query, &docs, 5) })
}

#[op(name = "search_expanded")]
fn search_expanded(query: String, docs: Vec<String>) -> Value {
    let expanded = format!("{query} thành phố du lịch");
    serde_json::json!({ "results": keyword_search(&expanded, &docs, 5) })
}

#[op(name = "rrf_merge")]
fn rrf_merge(r1: Vec<String>, r2: Vec<String>) -> Value {
    let merged = reciprocal_rank_fusion(&[r1, r2], 60);
    serde_json::json!({ "merged": merged.into_iter().take(5).collect::<Vec<_>>() })
}

#[op(name = "kw_search_fn")]
fn kw_search_fn(query: String, docs: Vec<String>) -> Value {
    serde_json::json!({ "results": keyword_search(&query, &docs, 8) })
}

#[op(name = "vec_search_fn")]
fn vec_search_fn(qv: Value, docs: Vec<String>, dvs: Value) -> Value {
    let q: Vec<f64> = qv
        .get(0)
        .and_then(Value::as_array)
        .map(|arr| arr.iter().filter_map(Value::as_f64).collect())
        .unwrap_or_default();
    let dv: Vec<Vec<f64>> = dvs
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|row| {
                    row.as_array()
                        .map(|r| r.iter().filter_map(Value::as_f64).collect())
                        .unwrap_or_default()
                })
                .collect()
        })
        .unwrap_or_default();
    serde_json::json!({ "results": cosine_search(&q, &dv, &docs, 8) })
}

#[op(name = "merge_results")]
fn merge_results(kw: Vec<String>, vec: Vec<String>) -> Value {
    let merged = reciprocal_rank_fusion(&[kw, vec], 60);
    serde_json::json!({ "context_docs": merged.into_iter().take(5).collect::<Vec<_>>() })
}

const DOCUMENTS: &[&str] = &[
    "Hà Nội là thủ đô của Việt Nam, nằm ở miền Bắc, có hơn 1000 năm lịch sử.",
    "TP.HCM là thành phố lớn nhất Việt Nam về dân số, trung tâm kinh tế phía Nam.",
    "Đà Nẵng là thành phố lớn nhất miền Trung, nổi tiếng với bãi biển Mỹ Khê.",
    "Huế là cố đô của Việt Nam, nổi tiếng với Đại Nội và ẩm thực đặc sắc.",
    "Hạ Long là di sản thiên nhiên thế giới với hàng nghìn hòn đảo đá vôi.",
    "Sapa nằm ở Lào Cai, nổi tiếng với ruộng bậc thang và văn hóa dân tộc.",
    "Phú Quốc là đảo lớn nhất Việt Nam, thuộc tỉnh Kiên Giang, nổi tiếng du lịch biển.",
    "Nha Trang thuộc Khánh Hòa, được biết đến với bãi biển đẹp và du lịch nghỉ dưỡng.",
    "Cần Thơ là thành phố lớn nhất miền Tây, nổi tiếng với chợ nổi Cái Răng.",
    "Đà Lạt là thành phố ngàn hoa, nằm trên cao nguyên Lâm Đồng, khí hậu mát mẻ.",
];

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));

    let opts = BootstrapOpts::new().resources(here.join("resources.yaml"));
    operonx::bootstrap(opts);

    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    let documents: Vec<String> = DOCUMENTS.iter().map(|s| (*s).to_string()).collect();

    for name in ["keyword_rrf", "hybrid"] {
        let graph_v = graph_bundle
            .get(name)
            .ok_or_else(|| format!("graph.json missing `{name}` entry"))?;
        let graph_json = serde_json::to_string(graph_v)?;

        let mut inputs_obj = inputs_bundle
            .get(name)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();

        inputs_obj.insert("documents".into(), serde_json::json!(documents));
        if name == "hybrid" {
            let zeros: Vec<Vec<f64>> = documents.iter().map(|_| vec![0.0_f64; 1]).collect();
            inputs_obj.insert("doc_vectors".into(), serde_json::json!(zeros));
        }

        let engine = Operon::builder(&graph_json).auto_register().build()?;
        match engine.run_json(inputs_obj, None, None, None) {
            Ok(r) => println!("[{name}] {r}"),
            Err(e) => println!("[{name}] error: {e}"),
        }
    }

    Ok(())
}
