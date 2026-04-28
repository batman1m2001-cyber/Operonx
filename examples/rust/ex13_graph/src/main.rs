//! 13 @graph — Rust-side demo.
//!
//! Mirrors `examples/python/ex13_graph/main.py`. Two plain `#[op]`s
//! (`double`, `add`) are declared here; nested `@graph` composition is
//! handled by the engine via the serialised `graph.json`.
//!
//! ⚠️  Rust-runtime limitation: the scheduler currently returns empty
//! for nested `OpType::Graph` ops, so every scenario here is
//! Rust-limited until nested-graph dispatch lands.

use operonx::{op, Operon};
use serde_json::Value;

#[op(name = "double")]
fn double(x: i64) -> Value {
    serde_json::json!({ "result": x * 2 })
}

#[op(name = "add")]
fn add(a: i64, b: i64) -> Value {
    serde_json::json!({ "result": a + b })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    for name in ["basic", "chained", "renamed", "multi_params", "nested"] {
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
