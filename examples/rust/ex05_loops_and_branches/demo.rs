//! 05 Loops & Branches — Rust-side usage demo.
//!
//! Generator ops + `if_` branch routing. Mirrors
//! `examples/python/ex05_loops_and_branches/workflow.py`.

use operonx::op;
use serde_json::{json, Value};

// TODO: Rust scheduler does not yet dispatch generator yields per item —
// we return the accumulated list as a single-shot value instead. Downstream
// per-item ops won't fan out until generator support lands.

#[op(name = "each_item")]
fn each_item(items: Vec<Value>, prefix: String) -> Value {
    let out: Vec<Value> = items
        .into_iter()
        .map(|item| json!({ "item": item, "prefix": prefix.clone() }))
        .collect();
    json!({ "items": out })
}

#[op(name = "process_item")]
fn process_item(item: String, prefix: String) -> Value {
    json!({ "result": format!("{}: {}", prefix, item) })
}

#[op(name = "each_number")]
fn each_number(numbers: Vec<i64>) -> Value {
    let out: Vec<Value> = numbers.into_iter().map(|x| json!({ "x": x })).collect();
    json!({ "items": out })
}

#[op(name = "square")]
fn square(x: i64) -> Value {
    json!({ "squared": x * x })
}

#[op(name = "halve_until")]
fn halve_until(value: i64) -> Value {
    let mut v = value;
    let mut out = Vec::new();
    while v >= 5 {
        v /= 2;
        out.push(v);
    }
    json!({ "value": out })
}

// ── Branch op bodies ─────────────────────────────────────────────────

#[op(name = "excellent")]
fn excellent(_inputs: &Value) -> Value {
    json!({ "grade": "A", "message": "Xuất sắc!" })
}

#[op(name = "good")]
fn good(_inputs: &Value) -> Value {
    json!({ "grade": "B", "message": "Tốt!" })
}

#[op(name = "average")]
fn average(_inputs: &Value) -> Value {
    json!({ "grade": "C", "message": "Trung bình" })
}

#[op(name = "fail")]
fn fail(_inputs: &Value) -> Value {
    json!({ "grade": "F", "message": "Cần cải thiện" })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex05_loops_and_branches";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // TODO: `branch` uses `if_(...)` which is an `OpType::Branch` stub in
    // the current Rust scheduler — the run will fail on that scenario.
    // Generator scenarios (`for_loop`, `map_op`, `while_loop`) also differ
    // from Python: the Rust op returns the accumulated list instead of
    // per-item yields.
    let scenarios = ["for_loop", "map_op", "while_loop", "branch"];
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
