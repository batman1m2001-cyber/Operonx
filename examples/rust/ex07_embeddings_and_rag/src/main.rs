//! 07 Embeddings & RAG — Rust-side demo.
//!
//! Mirrors `examples/python/ex07_embeddings_and_rag/main.py`. Only the
//! plain `#[op]` functions are declared here; `EmbeddingOp`, `PromptOp`,
//! `LLMOp`, `RerankOp` are runtime-built provider ops.
//!
//! Requires `OPENAI_API_KEY` in `.env` and `resources.yaml` exposing
//! `embedding:openai` + `llm:gpt-4o-mini` (and `reranker:bge-m3` for
//! the rerank scenario).

use operonx::{op, BootstrapOpts, Operon};
use serde_json::Value;

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
    serde_json::json!({ "context_docs": top })
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
];

fn run_scenario(
    graph_v: &Value,
    inputs: serde_json::Map<String, Value>,
) -> Result<Value, Box<dyn std::error::Error>> {
    let graph_json = serde_json::to_string(graph_v)?;
    let engine = Operon::builder(&graph_json).auto_register().build()?;
    Ok(engine.run_json(inputs, None, None, None)?)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));

    let opts = BootstrapOpts::new().resources(here.join("resources.yaml"));
    operonx::bootstrap(opts);

    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;

    let documents: Vec<String> = DOCUMENTS.iter().map(|s| (*s).to_string()).collect();

    // 1. Pre-compute doc vectors via the embed graph.
    let mut precomp = serde_json::Map::new();
    precomp.insert("texts".into(), serde_json::json!(documents));
    let embed_v = graph_bundle.get("embed").expect("embed graph");
    let embed_result = run_scenario(embed_v, precomp.clone())?;
    let doc_vectors = embed_result
        .get("vectors")
        .cloned()
        .unwrap_or(Value::Array(Vec::new()));
    println!("[embed] doc_vectors len = {}", embed_result.get("vectors").and_then(Value::as_array).map(|a| a.len()).unwrap_or(0));

    // 2. Embedding-only on a tiny payload.
    let mut embed_inputs = serde_json::Map::new();
    embed_inputs.insert(
        "texts".into(),
        serde_json::json!(["Xin chào!", "Operon workflow engine"]),
    );
    let r = run_scenario(embed_v, embed_inputs)?;
    println!("[embed-tiny] {}", r);

    // 3. Simple RAG.
    let mut rag_inputs = serde_json::Map::new();
    rag_inputs.insert("query".into(), serde_json::json!("Thủ đô Việt Nam là gì?"));
    rag_inputs.insert("documents".into(), serde_json::json!(documents));
    rag_inputs.insert("doc_vectors".into(), doc_vectors);
    let rag_v = graph_bundle.get("rag").expect("rag graph");
    let r = run_scenario(rag_v, rag_inputs)?;
    println!("[rag] {}", r);

    // 4. RAG + rerank — skip cleanly if the rerank graph isn't bundled
    // (the Python builder leaves it out unless the bge-m3 backend is
    // configured at serialise time) or if the runtime backend isn't
    // available.
    if let Some(rer_v) = graph_bundle.get("rerank") {
        let mut rer_inputs = serde_json::Map::new();
        rer_inputs.insert(
            "query".into(),
            serde_json::json!("Thành phố biển đẹp nhất Việt Nam?"),
        );
        rer_inputs.insert("documents".into(), serde_json::json!(documents));
        match run_scenario(rer_v, rer_inputs) {
            Ok(r) => println!("[rerank] {}", r),
            Err(e) => println!("[rerank] skipped: {e}"),
        }
    } else {
        println!("[rerank] skipped: no `rerank` entry in graph.json (re-pack with reranker:bge-m3 configured)");
    }

    Ok(())
}
