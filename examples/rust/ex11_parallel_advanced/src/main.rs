//! 11 Parallel Advanced — Rust-side demo.
//!
//! Mirrors `examples/python/ex11_parallel_advanced/main.py`.
//!
//! ⚠️  Generator scenarios (`iteration`, `partial_failure`) accumulate a
//! list rather than dispatching per-item — the Rust streaming scheduler
//! does not yet fan out yields.

use operonx::{op, Operon};
use serde_json::Value;

#[op(name = "analyze_sentiment")]
fn analyze_sentiment(text: String) -> Value {
    let positive_words = ["good", "great", "excellent", "love", "happy"];
    let negative_words = ["bad", "terrible", "hate", "awful", "sad"];
    let lower = text.to_lowercase();
    let words: Vec<&str> = lower.split_whitespace().collect();
    let positive = words.iter().filter(|w| positive_words.contains(w)).count();
    let negative = words.iter().filter(|w| negative_words.contains(w)).count();
    let sentiment = if positive > negative {
        "positive"
    } else if negative > positive {
        "negative"
    } else {
        "neutral"
    };
    serde_json::json!({ "sentiment": sentiment })
}

#[op(name = "extract_keywords")]
fn extract_keywords(text: String) -> Value {
    let stop: std::collections::HashSet<&str> = [
        "the", "is", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    ]
    .into_iter()
    .collect();
    let cleaned: Vec<String> = text
        .split_whitespace()
        .map(|w| {
            w.to_lowercase()
                .trim_matches(|c: char| ".,!?".contains(c))
                .to_string()
        })
        .filter(|w| !stop.contains(w.as_str()) && w.chars().count() > 2)
        .collect();
    let top: Vec<&String> = cleaned.iter().take(5).collect();
    serde_json::json!({ "keywords": top })
}

#[op(name = "count_stats")]
fn count_stats(text: String) -> Value {
    let words: Vec<&str> = text.split_whitespace().collect();
    let char_count = text.chars().count();
    let word_count = words.len();
    let avg = if word_count > 0 {
        (char_count as f64) / (word_count as f64)
    } else {
        0.0
    };
    serde_json::json!({
        "word_count": word_count,
        "char_count": char_count,
        "avg_word_len": (avg * 10.0).round() / 10.0,
    })
}

#[op(name = "merge_analysis")]
fn merge_analysis(s: Value, k: Value, wc: Value, cc: Value, awl: Value) -> Value {
    serde_json::json!({
        "analysis": {
            "sentiment": s,
            "keywords": k,
            "word_count": wc,
            "char_count": cc,
            "avg_word_len": awl,
        }
    })
}

#[op(name = "each_item")]
fn each_item(items: Vec<Value>) -> Value {
    let out: Vec<Value> = items
        .into_iter()
        .map(|item| serde_json::json!({ "item": item }))
        .collect();
    serde_json::json!({ "items": out })
}

#[op(name = "process_item")]
fn process_item(item: i64) -> Value {
    serde_json::json!({ "result": item * item, "status": "ok" })
}

#[op(name = "safe_process")]
fn safe_process(item: i64) -> Value {
    if item % 2 != 0 {
        serde_json::json!({ "result": item * 10, "error": Value::Null })
    } else {
        serde_json::json!({ "result": Value::Null, "error": format!("Even number: {item}") })
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    for name in ["fan_out", "iteration", "partial_failure"] {
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
