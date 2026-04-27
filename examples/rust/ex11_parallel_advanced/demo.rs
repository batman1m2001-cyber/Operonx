//! 11 Parallel Advanced — Rust-side usage demo.
//!
//! Mirrors `examples/python/ex11_parallel_advanced/workflow.py`.

use operonx::op;
use serde_json::{json, Value};

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
    json!({ "sentiment": sentiment })
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
    json!({ "keywords": top })
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
    json!({
        "word_count": word_count,
        "char_count": char_count,
        "avg_word_len": (avg * 10.0).round() / 10.0,
    })
}

#[op(name = "merge_analysis")]
fn merge_analysis(s: Value, k: Value, wc: Value, cc: Value, awl: Value) -> Value {
    json!({
        "analysis": {
            "sentiment": s,
            "keywords": k,
            "word_count": wc,
            "char_count": cc,
            "avg_word_len": awl,
        }
    })
}

// TODO: generator ops — return the accumulated list; per-item dispatch not
// yet available in the Rust scheduler.
#[op(name = "each_item")]
fn each_item(items: Vec<Value>) -> Value {
    let out: Vec<Value> = items
        .into_iter()
        .map(|item| json!({ "item": item }))
        .collect();
    json!({ "items": out })
}

#[op(name = "process_item")]
fn process_item(item: i64) -> Value {
    json!({ "result": item * item, "status": "ok" })
}

#[op(name = "safe_process")]
fn safe_process(item: i64) -> Value {
    if item % 2 != 0 {
        json!({ "result": item * 10, "error": Value::Null })
    } else {
        json!({ "result": Value::Null, "error": format!("Even number: {}", item) })
    }
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex11_parallel_advanced";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // TODO: `iteration` + `partial_failure` rely on generator per-item
    // dispatch, not yet implemented in the Rust scheduler.
    let scenarios = ["fan_out", "iteration", "partial_failure"];
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
