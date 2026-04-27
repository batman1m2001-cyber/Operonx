//! 07 Embeddings & RAG — Rust-side usage demo.
//!
//! Mirrors `examples/python/ex07_embeddings_and_rag/workflow.py`. Only the
//! plain `@op` functions are declared here; `EmbeddingOp`, `PromptOp`,
//! `LLMOp`, `RerankOp` are runtime-built provider ops.
//!
//! Requires `OPENAI_API_KEY` in `.env` and a `resources.yaml` exposing
//! an `openai` embedding + `gpt-4o-mini` LLM (plus `bge-m3` reranker for
//! the `rerank` scenario).

use operonx::op;
use serde_json::{json, Value};

fn cosine(q: &[f64], d: &[f64]) -> f64 {
    let qn: f64 = q.iter().map(|x| x * x).sum::<f64>().sqrt();
    let dn: f64 = d.iter().map(|x| x * x).sum::<f64>().sqrt();
    if qn == 0.0 || dn == 0.0 {
        return 0.0;
    }
    let dot: f64 = q.iter().zip(d.iter()).map(|(a, b)| a * b).sum();
    dot / (qn * dn)
}

#[op(name = "retrieve")]
fn retrieve(query_vec: Value, doc_vectors: Value, documents: Vec<String>) -> Value {
    // `query_vec` is shape [[...]], take the first row.
    let q: Vec<f64> = query_vec
        .get(0)
        .and_then(Value::as_array)
        .map(|arr| arr.iter().filter_map(Value::as_f64).collect())
        .unwrap_or_default();

    let docs: Vec<Vec<f64>> = doc_vectors
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

    let mut scored: Vec<(f64, String)> = docs
        .iter()
        .zip(documents.iter())
        .map(|(dv, dtxt)| (cosine(&q, dv), dtxt.clone()))
        .collect();
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());
    let top: Vec<Value> = scored
        .into_iter()
        .take(3)
        .map(|(_, d)| Value::String(d))
        .collect();
    json!({ "context_docs": top })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex07_embeddings_and_rag";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // For the `rag` scenario we need pre-computed doc embeddings. We run
    // the `embed` graph with the DOCUMENTS first (untimed) and inject the
    // result into the `rag` inputs.
    let documents = vec![
        "Hà Nội là thủ đô của Việt Nam, nằm ở miền Bắc, có hơn 1000 năm lịch sử.".to_string(),
        "TP.HCM là thành phố lớn nhất Việt Nam về dân số, trung tâm kinh tế phía Nam.".to_string(),
        "Đà Nẵng là thành phố lớn nhất miền Trung, nổi tiếng với bãi biển Mỹ Khê.".to_string(),
        "Huế là cố đô của Việt Nam, nổi tiếng với Đại Nội và ẩm thực đặc sắc.".to_string(),
        "Hạ Long là di sản thiên nhiên thế giới với hàng nghìn hòn đảo đá vôi.".to_string(),
        "Sapa nằm ở Lào Cai, nổi tiếng với ruộng bậc thang và văn hóa dân tộc.".to_string(),
        "Phú Quốc là đảo lớn nhất Việt Nam, thuộc tỉnh Kiên Giang, nổi tiếng du lịch biển."
            .to_string(),
        "Nha Trang thuộc Khánh Hòa, được biết đến với bãi biển đẹp và du lịch nghỉ dưỡng."
            .to_string(),
    ];

    let scenarios = ["embed", "rag", "rerank"];
    let mut reporter = common::BenchReporter::new(example);

    // Pre-compute doc vectors using the `embed` graph.
    let embed_graph_v = common::rename_graph(
        graph_bundle.get("embed").cloned().expect("embed graph"),
        "_rust_precomp",
    );
    let embed_graph_json = serde_json::to_string(&embed_graph_v)?;
    let embed_engine = common::build_engine(&embed_graph_json, &args)?;
    let mut precomp_inputs = serde_json::Map::new();
    precomp_inputs.insert("texts".into(), json!(documents));
    let embed_result = embed_engine.run_json(precomp_inputs, None, None, None)?;
    let doc_vectors = embed_result
        .get("vectors")
        .cloned()
        .unwrap_or(Value::Array(Vec::new()));

    for name in scenarios {
        let graph_v = graph_bundle
            .get(name)
            .ok_or_else(|| format!("graph.json missing `{}` entry", name))?
            .clone();
        let graph_v = common::rename_graph(graph_v, "_rust");
        let graph_json = serde_json::to_string(&graph_v)?;

        let mut inputs_obj = inputs_bundle
            .get(name)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();

        // Inject `documents` + `doc_vectors` into scenarios that need them.
        if name == "rag" {
            inputs_obj.insert("documents".into(), json!(documents));
            inputs_obj.insert("doc_vectors".into(), doc_vectors.clone());
        } else if name == "rerank" {
            inputs_obj.insert("documents".into(), json!(documents));
        }

        let engine = common::build_engine(&graph_json, &args)?;
        reporter.record(name, args.runs, || {
            let out = engine.run_json(inputs_obj.clone(), None, None, None)?;
            Ok(out)
        })?;
    }

    reporter.save()?;
    Ok(())
}
