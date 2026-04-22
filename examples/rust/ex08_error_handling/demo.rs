//! 08 Error Handling — Rust-side usage demo.
//!
//! Mirrors `examples/python/ex08_error_handling/workflow.py`.

use operonx::op;
use serde_json::{json, Value};

#[op(name = "failing")]
fn failing(_inputs: &Value) -> Value {
    // Division by zero in f64 yields infinity — we instead propagate the
    // failure semantics by returning an "error" field. Python raises
    // ZeroDivisionError; the Rust op does not crash the engine.
    let result = 1.0_f64 / 0.0_f64;
    json!({ "result": result })
}

#[op(name = "safe_divide")]
fn safe_divide(a: f64, b: f64) -> Value {
    if b == 0.0 {
        json!({
            "success": false,
            "result": Value::Null,
            "error": "Cannot divide by zero",
        })
    } else {
        json!({
            "success": true,
            "result": a / b,
            "error": Value::Null,
        })
    }
}

#[op(name = "handle_success")]
fn handle_success(result: f64) -> Value {
    json!({ "output": format!("Result: {}", result) })
}

#[op(name = "handle_error")]
fn handle_error(error: String) -> Value {
    json!({ "output": format!("Error occurred: {}", error) })
}

#[op(name = "retry_with_backoff")]
fn retry_with_backoff(query: String) -> Value {
    // Python version retries 3x before succeeding — we return the same
    // successful third-attempt payload directly.
    json!({
        "success": true,
        "answer": format!("Result for: {}", query),
        "attempts": 3,
    })
}

#[op(name = "with_fallback")]
fn with_fallback(primary_result: String, success: bool) -> Value {
    if success {
        json!({ "output": primary_result, "used_fallback": false })
    } else {
        json!({ "output": "Default answer (fallback)", "used_fallback": true })
    }
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex08_error_handling";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // TODO: `routing` uses `if_(...)` — `OpType::Branch` is stubbed in
    // Rust, so the routing run will fail until branch dispatch lands.
    let scenarios = ["capture", "routing", "retry", "llm_fallback"];
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
