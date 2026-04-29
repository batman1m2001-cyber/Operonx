//! 05 Loops & Branches — Rust-side demo.
//!
//! Generator ops + `if_()` branch routing, mirroring
//! `examples/python/ex05_loops_and_branches/main.py`. Generator ops
//! (`each_item`, `each_number`) return `Value::Array`; the Rust
//! scheduler fans each element out as one yield-frame so downstream
//! ops dispatch per item, matching Python's `yield` semantics.
//! `OpType::Branch` dispatch + ref-transform evaluator land the
//! `branch` scenario end-to-end.

use operonx::{op, Operon};
use serde_json::Value;

#[op(name = "each_item")]
fn each_item(items: Vec<Value>, prefix: String) -> Value {
    // Generator op (graph.json marks `is_generator: true`): return a
    // `Value::Array` of per-yield frames. The Rust scheduler iterates
    // and dispatches downstream once per element, mirroring Python's
    // `for item in items: yield {...}`.
    Value::Array(
        items
            .into_iter()
            .map(|item| serde_json::json!({ "item": item, "prefix": prefix.clone() }))
            .collect(),
    )
}

#[op(name = "process_item")]
fn process_item(item: String, prefix: String) -> Value {
    serde_json::json!({ "result": format!("{prefix}: {item}") })
}

#[op(name = "each_number")]
fn each_number(numbers: Vec<i64>) -> Value {
    Value::Array(
        numbers
            .into_iter()
            .map(|x| serde_json::json!({ "x": x }))
            .collect(),
    )
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

    for name in ["for_loop", "map_op", "while_loop", "branch"] {
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
