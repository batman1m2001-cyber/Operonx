//! 01 Hello World — Rust-side usage demo.
//!
//! Three tiny graphs matching the Python side. Each graph is built
//! **outside** the timed span; only `engine.run_json(...)` counts.
//! Report lands at `examples/bench_results/ex01_hello_world_rust.json`.
//!
//! ```sh
//! cargo run --release -p operonx --example ex01_hello_world
//! cargo run --release -p operonx --example ex01_hello_world -- --runs 20
//! ```

use operonx::op;
use serde_json::{json, Value};

// ── Op bodies mirror `examples/python/ex01_hello_world/workflow.py` ──

#[op(name = "greet")]
fn greet(name: String) -> Value {
    json!({ "greeting": format!("Xin chào, {}!", name) })
}

#[op(name = "greet_en")]
fn greet_en(name: String) -> Value {
    json!({ "greeting": format!("Hello, {}!", name) })
}

#[op(name = "upper")]
fn upper(text: String) -> Value {
    json!({ "result": text.to_uppercase() })
}

#[op(name = "step_a")]
fn step_a(_inputs: &Value) -> Value {
    json!({ "a_result": "Kết quả A" })
}

#[op(name = "step_b")]
fn step_b(_inputs: &Value) -> Value {
    json!({ "b_result": "Kết quả B" })
}

#[op(name = "merge")]
fn merge(a: String, b: String) -> Value {
    json!({ "combined": format!("{} + {}", a, b) })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex01_hello_world";
    let args = common::parse_args(example);

    // Load graph bundle (one entry per scenario) + inputs bundle once —
    // both are pure disk I/O + JSON parse, nothing engine-specific.
    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    let scenarios = ["hello", "chain", "parallel"];
    let mut reporter = common::BenchReporter::new(example);

    for name in scenarios {
        let graph_v = graph_bundle
            .get(name)
            .ok_or_else(|| format!("graph.json missing `{}` entry", name))?
            .clone();
        // Rename so Langfuse traces split python↔rust cleanly.
        let graph_v = common::rename_graph(graph_v, "_rust");
        let graph_json = serde_json::to_string(&graph_v)?;

        let inputs_obj = inputs_bundle
            .get(name)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();

        // ── Engine construction (NOT timed) ──
        let engine = common::build_engine(&graph_json, &args)?;

        // ── Timed span (warmup + `runs` iterations) ──
        reporter.record(name, args.runs, || {
            let out = engine.run_json(inputs_obj.clone(), None, None, None)?;
            Ok(out)
        })?;
    }

    reporter.save()?;
    Ok(())
}
