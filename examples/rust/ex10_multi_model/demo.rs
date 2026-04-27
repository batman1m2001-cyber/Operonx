//! 10 Multi-Model — Rust-side usage demo.
//!
//! Mirrors `examples/python/ex10_multi_model/workflow.py`. Only plain
//! `@op`s are declared; `PromptOp`/`LLMOp` are runtime-built.

use operonx::op;
use serde_json::{json, Value};

#[op(name = "is_simple")]
fn is_simple(classification: String) -> Value {
    json!({ "is_simple": classification.to_uppercase().contains("SIMPLE") })
}

#[op(name = "compare")]
fn compare(a: String, b: String) -> Value {
    let la = a.chars().count() as i64;
    let lb = b.chars().count() as i64;
    json!({
        "gpt4o": a,
        "gpt4o_mini": b,
        "same_length": (la - lb).abs() < 50,
    })
}

#[op(name = "select")]
fn select(choice: String, a1: String, a2: String) -> Value {
    let pick_one = choice.contains('1');
    json!({
        "answer": if pick_one { a1 } else { a2 },
        "chosen": if pick_one { "gpt-4o" } else { "gpt-4o-mini" },
    })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex10_multi_model";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // TODO: `routing` uses `if_(...)` — `OpType::Branch` is stubbed in
    // the Rust scheduler; that scenario will fail until branch dispatch
    // lands.
    let scenarios = [
        "parallel",
        "routing",
        "load_balanced",
        "fallback",
        "ensemble",
    ];
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
