//! 05 Loops & Branches — Rust-side demo.
//!
//! Generator ops + `if_()` branch routing, mirroring
//! `examples/python/ex05_loops_and_branches/main.py`.
//!
//! ⚠️  **Rust-runtime limitations** (today, v0.6.2):
//! - The streaming scheduler does not yet dispatch generator yields per
//!   item, so `each_item` / `each_number` / `halve_until` return the
//!   accumulated list as a single-shot value. Downstream per-item ops
//!   won't fan out the way they do in Python.
//! - `if_()` branch routing is a stub in the Rust scheduler — the
//!   `branch` scenario will fail at run time. We still ship its
//!   `#[op]` bodies so the regen path can keep both languages aligned.

use operonx::{op, Operon};
use serde_json::Value;

#[op(name = "each_item")]
fn each_item(items: Vec<Value>, prefix: String) -> Value {
    let out: Vec<Value> = items
        .into_iter()
        .map(|item| serde_json::json!({ "item": item, "prefix": prefix.clone() }))
        .collect();
    serde_json::json!({ "items": out })
}

#[op(name = "process_item")]
fn process_item(item: String, prefix: String) -> Value {
    serde_json::json!({ "result": format!("{prefix}: {item}") })
}

#[op(name = "each_number")]
fn each_number(numbers: Vec<i64>) -> Value {
    let out: Vec<Value> = numbers
        .into_iter()
        .map(|x| serde_json::json!({ "x": x }))
        .collect();
    serde_json::json!({ "items": out })
}

#[op(name = "square")]
fn square(x: i64) -> Value {
    serde_json::json!({ "squared": x * x })
}

#[op(name = "halve_until")]
fn halve_until(value: i64) -> Value {
    let mut v = value;
    let mut out = Vec::new();
    while v >= 5 {
        v /= 2;
        out.push(v);
    }
    serde_json::json!({ "value": out })
}

#[op(name = "excellent")]
fn excellent(_inputs: &Value) -> Value {
    serde_json::json!({ "grade": "A", "message": "Xuất sắc!" })
}

#[op(name = "good")]
fn good(_inputs: &Value) -> Value {
    serde_json::json!({ "grade": "B", "message": "Tốt!" })
}

#[op(name = "average")]
fn average(_inputs: &Value) -> Value {
    serde_json::json!({ "grade": "C", "message": "Trung bình" })
}

#[op(name = "fail")]
fn fail(_inputs: &Value) -> Value {
    serde_json::json!({ "grade": "F", "message": "Cần cải thiện" })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    // Branch scenario is known-broken on the Rust runtime today; skip it
    // to keep the demo runnable. Drop `branch` from this list once
    // scheduler support lands.
    for name in ["for_loop", "map_op", "while_loop"] {
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
