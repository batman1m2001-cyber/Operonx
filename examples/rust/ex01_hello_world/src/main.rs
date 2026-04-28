//! 01 Hello World — Rust-side demo.
//!
//! Three tiny graphs matching the Python side. No API keys.
//!
//! ```sh
//! cargo run --release
//! ```

use operonx::{op, Operon};
use serde_json::Value;

#[op(name = "greet")]
fn greet(name: String) -> Value {
    serde_json::json!({ "greeting": format!("Xin chào, {}!", name) })
}

#[op(name = "greet_en")]
fn greet_en(name: String) -> Value {
    serde_json::json!({ "greeting": format!("Hello, {}!", name) })
}

#[op(name = "upper")]
fn upper(text: String) -> Value {
    serde_json::json!({ "result": text.to_uppercase() })
}

#[op(name = "step_a")]
fn step_a(_inputs: &Value) -> Value {
    serde_json::json!({ "a_result": "Kết quả A" })
}

#[op(name = "step_b")]
fn step_b(_inputs: &Value) -> Value {
    serde_json::json!({ "b_result": "Kết quả B" })
}

#[op(name = "merge")]
fn merge(a: String, b: String) -> Value {
    serde_json::json!({ "combined": format!("{} + {}", a, b) })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    for name in ["hello", "chain", "parallel"] {
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
