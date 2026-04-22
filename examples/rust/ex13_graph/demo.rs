//! 13 @graph — Rust-side usage demo.
//!
//! Mirrors `examples/python/ex13_graph/workflow.py`. The two plain `@op`s
//! (`double`, `add`) are declared here; the nested `@graph` composition
//! is handled by the engine via the serialized `graph.json`.

use operonx::op;
use serde_json::{json, Value};

#[op(name = "double")]
fn double(x: i64) -> Value {
    json!({ "result": x * 2 })
}

#[op(name = "add")]
fn add(a: i64, b: i64) -> Value {
    json!({ "result": a + b })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex13_graph";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // TODO: nested `@graph` composition — the Rust scheduler currently
    // returns empty for `OpType::Graph`, so every scenario here is
    // Rust-limited until nested graph dispatch lands.
    let scenarios = ["basic", "chained", "renamed", "multi_params", "nested"];
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
