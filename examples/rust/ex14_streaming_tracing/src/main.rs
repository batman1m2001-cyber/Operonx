//! 14 Streaming & Tracing — Rust-side demo.
//!
//! Mirrors `examples/python/ex14_streaming_tracing/main.py`.
//!
//! ⚠️  Rust-runtime limitation: per-item generator dispatch + streaming
//! aren't exposed yet. The `chunk_text` / `async_counter` ops accumulate
//! their list and return single-shot, so downstream ops don't fan out.

use operonx::{op, Operon};
use serde_json::Value;

#[op(name = "chunk_text")]
fn chunk_text(text: String, chunk_size: i64) -> Value {
    let size = chunk_size.max(1) as usize;
    let words: Vec<&str> = text.split_whitespace().collect();
    let mut out: Vec<Value> = Vec::new();
    for (idx, window) in words.chunks(size).enumerate() {
        out.push(serde_json::json!({
            "chunk": window.join(" "),
            "index": idx as i64,
        }));
    }
    serde_json::json!({ "chunks": out })
}

#[op(name = "analyze_chunk")]
fn analyze_chunk(chunk: String, index: i64) -> Value {
    let words: Vec<&str> = chunk.split_whitespace().collect();
    let word_count = words.len();
    let has_long = words.iter().any(|w| w.chars().count() > 6);
    let score = (word_count * 10) + if has_long { 15 } else { 0 };
    let marker = if has_long { "*" } else { "" };
    serde_json::json!({
        "result": format!("[{index}] {word_count}w score={score}{marker}"),
    })
}

#[op(name = "async_counter")]
fn async_counter(n: i64) -> Value {
    let out: Vec<Value> = (1..=n)
        .map(|i| serde_json::json!({ "number": i, "squared": i * i }))
        .collect();
    serde_json::json!({ "items": out })
}

#[op(name = "format_square")]
fn format_square(number: i64, squared: i64) -> Value {
    serde_json::json!({ "label": format!("{number}^2 = {squared}") })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    for name in ["text", "async_counter"] {
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
        match engine.run_json(inputs_obj, None, None, None) {
            Ok(r) => println!("[{name}] {r}"),
            Err(e) => println!("[{name}] error: {e}"),
        }
    }

    Ok(())
}
