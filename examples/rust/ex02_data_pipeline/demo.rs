//! 02 Data Pipeline — Rust-side usage demo.
//!
//! Two pure-compute pipelines mirroring
//! `examples/python/ex02_data_pipeline/workflow.py`.
//!
//! ```sh
//! cargo run --release -p operonx --example ex02_data_pipeline
//! cargo run --release -p operonx --example ex02_data_pipeline -- --runs 20
//! ```

use operonx::op;
use serde_json::{json, Value};

// ── Pipeline 1: data transformation ────────────────────────────────────

#[op(name = "fetch_data")]
fn fetch_data(_inputs: &Value) -> Value {
    json!({ "data": [1, 2, 3, 4, 5] })
}

#[op(name = "transform")]
fn transform(data: Vec<i64>) -> Value {
    let doubled: Vec<i64> = data.iter().map(|x| x * 2).collect();
    json!({ "transformed": doubled })
}

#[op(name = "aggregate")]
fn aggregate(data: Vec<f64>) -> Value {
    let count = data.len();
    let total: f64 = data.iter().sum();
    let average = if count > 0 { total / count as f64 } else { 0.0 };
    json!({
        "total": total,
        "average": average,
        "count": count,
    })
}

// ── Pipeline 2: text processing ────────────────────────────────────────

#[op(name = "clean_text")]
fn clean_text(text: String) -> Value {
    let cleaned: String = text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    json!({ "cleaned_text": cleaned })
}

#[op(name = "count_words")]
fn count_words(text: String) -> Value {
    let words: Vec<&str> = text.split_whitespace().collect();
    let unique: std::collections::HashSet<&str> = words.iter().copied().collect();
    json!({
        "word_count": words.len(),
        "unique_words": unique.len(),
        "words": words,
    })
}

#[op(name = "summarize_stats")]
fn summarize_stats(word_count: i64, unique_words: i64, cleaned_text: String) -> Value {
    let _ = cleaned_text;
    let ratio = if word_count > 0 {
        (unique_words as f64) / (word_count as f64) * 100.0
    } else {
        0.0
    };
    let report = format!(
        "Văn bản có {} từ, {} từ unique, tỉ lệ unique: {:.0}%",
        word_count, unique_words, ratio
    );
    json!({ "report": report })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex02_data_pipeline";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    let scenarios = ["data", "text"];
    let mut reporter = common::BenchReporter::new(example);

    for name in scenarios {
        let graph_v = graph_bundle
            .get(name)
            .ok_or_else(|| format!("graph.json missing `{}` entry", name))?
            .clone();
        let graph_v = common::rename_graph(graph_v, "_rust");
        let graph_json = serde_json::to_string(&graph_v)?;

        let inputs_obj = inputs_bundle
            .get(name)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();

        let engine = common::build_engine(&graph_json, &args)?;

        reporter.record(name, args.runs, || {
            let out = engine.run_json(inputs_obj.clone(), None, None, None)?;
            Ok(out)
        })?;
    }

    reporter.save()?;
    Ok(())
}
