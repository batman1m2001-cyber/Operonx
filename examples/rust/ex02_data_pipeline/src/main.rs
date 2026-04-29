//! 02 Data Pipeline — Rust-side demo.
//!
//! Two pure-compute pipelines mirroring `examples/python/ex02_data_pipeline/main.py`.
//!
//! ```sh
//! cargo run --release
//! ```

use operonx::{op, Operon};
use serde_json::Value;

// ── Pipeline 1: data transformation ────────────────────────────────────

#[op(name = "fetch_data")]
fn fetch_data(_inputs: &Value) -> Value {
    serde_json::json!({ "data": [1, 2, 3, 4, 5] })
}

#[op(name = "transform")]
fn transform(data: Vec<i64>) -> Value {
    let doubled: Vec<i64> = data.iter().map(|x| x * 2).collect();
    serde_json::json!({ "transformed": doubled })
}

#[op(name = "aggregate")]
fn aggregate(data: Vec<f64>) -> Value {
    let count = data.len();
    let total: f64 = data.iter().sum();
    let average = if count > 0 { total / count as f64 } else { 0.0 };
    serde_json::json!({
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
    serde_json::json!({ "cleaned_text": cleaned })
}

#[op(name = "count_words")]
fn count_words(text: String) -> Value {
    let words: Vec<&str> = text.split_whitespace().collect();
    let unique: std::collections::HashSet<&str> = words.iter().copied().collect();
    serde_json::json!({
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
    serde_json::json!({ "report": report })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    for name in ["data", "text"] {
        let graph_v = graph_bundle
            .get(name)
            .ok_or_else(|| format!("graph.json missing `{name}` entry"))?;
        let graph_json = serde_json::to_string(graph_v)?;

        let inputs_obj = inputs_bundle
            .get(name)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();

        let engine = Operon::builder(&graph_json).auto_register().build()?;
        let result = engine.run_json(inputs_obj, None, None, None)?;
        println!("[{name}] {result}");
    }

    Ok(())
}
