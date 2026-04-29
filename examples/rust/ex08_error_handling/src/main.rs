//! 08 Error Handling — Rust-side demo.
//!
//! Mirrors `examples/python/ex08_error_handling/main.py`. The `routing`
//! scenario uses `if_()` which is stubbed in the Rust scheduler today;
//! it's excluded from runtime here. `llm_fallback` needs
//! `OPENAI_API_KEY` + the LLM resources in `resources.yaml`.

use operonx::{op, BootstrapOpts, Operon};
use serde_json::Value;

#[op(name = "failing")]
fn failing(_inputs: &Value) -> Value {
    // Division by zero in f64 yields infinity rather than panicking; we
    // return that to keep the demo running. Python raises
    // ZeroDivisionError — the engine captures it in state.
    serde_json::json!({ "result": 1.0_f64 / 0.0_f64 })
}

#[op(name = "safe_divide")]
fn safe_divide(a: f64, b: f64) -> Value {
    if b == 0.0 {
        serde_json::json!({
            "success": false,
            "result": Value::Null,
            "error": "Cannot divide by zero",
        })
    } else {
        serde_json::json!({
            "success": true,
            "result": a / b,
            "error": Value::Null,
        })
    }
}

#[op(name = "handle_success")]
fn handle_success(result: f64) -> Value {
    serde_json::json!({ "output": format!("Result: {result}") })
}

#[op(name = "handle_error")]
fn handle_error(error: String) -> Value {
    serde_json::json!({ "output": format!("Error occurred: {error}") })
}

#[op(name = "retry_with_backoff")]
fn retry_with_backoff(query: String) -> Value {
    serde_json::json!({
        "success": true,
        "answer": format!("Result for: {query}"),
        "attempts": 3,
    })
}

#[op(name = "with_fallback")]
fn with_fallback(primary_result: String, success: bool) -> Value {
    if success {
        serde_json::json!({ "output": primary_result, "used_fallback": false })
    } else {
        serde_json::json!({ "output": "Default answer (fallback)", "used_fallback": true })
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));

    let opts = BootstrapOpts::new().resources(here.join("resources.yaml"));
    operonx::bootstrap(opts);

    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    // `routing` uses if_() which is stubbed in Rust; skip it.
    // `llm_fallback` may fail without API key — we surface the error.
    for name in ["capture", "retry", "llm_fallback"] {
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
