//! 14 Streaming & Tracing — Rust-side usage demo.
//!
//! Mirrors `examples/python/ex14_streaming_tracing/workflow.py`.

use operonx::op;
use serde_json::{json, Value};

// TODO: generator ops — return accumulated lists; per-item dispatch not
// yet in the Rust scheduler.

#[op(name = "chunk_text")]
fn chunk_text(text: String, chunk_size: i64) -> Value {
    let size = chunk_size.max(1) as usize;
    let words: Vec<&str> = text.split_whitespace().collect();
    let mut out: Vec<Value> = Vec::new();
    let mut idx = 0;
    for window in words.chunks(size) {
        out.push(json!({
            "chunk": window.join(" "),
            "index": idx,
        }));
        idx += 1;
    }
    json!({ "chunks": out })
}

#[op(name = "analyze_chunk")]
fn analyze_chunk(chunk: String, index: i64) -> Value {
    let words: Vec<&str> = chunk.split_whitespace().collect();
    let word_count = words.len();
    let has_long = words.iter().any(|w| w.chars().count() > 6);
    let score = (word_count * 10) + if has_long { 15 } else { 0 };
    let marker = if has_long { "*" } else { "" };
    json!({
        "result": format!("[{}] {}w score={}{}", index, word_count, score, marker),
    })
}

// TODO: async generator — single-shot accumulator.
#[op(name = "async_counter")]
fn async_counter(n: i64) -> Value {
    let out: Vec<Value> = (1..=n)
        .map(|i| json!({ "number": i, "squared": i * i }))
        .collect();
    json!({ "items": out })
}

#[op(name = "format_square")]
fn format_square(number: i64, squared: i64) -> Value {
    json!({ "label": format!("{}^2 = {}", number, squared) })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex14_streaming_tracing";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // TODO: both scenarios rely on per-item generator dispatch + streaming,
    // neither of which the Rust scheduler exposes yet.
    let scenarios = ["text", "async_counter"];
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
