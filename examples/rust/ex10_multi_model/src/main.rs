//! 10 Multi-Model — Rust-side demo.
//!
//! Mirrors `examples/python/ex10_multi_model/main.py`. Only plain
//! `#[op]`s are declared; `PromptOp` + `LLMOp` are runtime-built.
//!
//! `routing` uses `if_()` which is stubbed in the Rust scheduler today;
//! it's excluded from runtime here.

use operonx::{op, BootstrapOpts, Operon};
use serde_json::Value;

#[op(name = "is_simple")]
fn is_simple(classification: String) -> Value {
    serde_json::json!({ "is_simple": classification.to_uppercase().contains("SIMPLE") })
}

#[op(name = "compare")]
fn compare(a: String, b: String) -> Value {
    let la = a.chars().count() as i64;
    let lb = b.chars().count() as i64;
    serde_json::json!({
        "gpt4o": a,
        "gpt4o_mini": b,
        "same_length": (la - lb).abs() < 50,
    })
}

#[op(name = "select")]
fn select(choice: String, a1: String, a2: String) -> Value {
    let pick_one = choice.contains('1');
    serde_json::json!({
        "answer": if pick_one { a1 } else { a2 },
        "chosen": if pick_one { "gpt-4o" } else { "gpt-4o-mini" },
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));

    let opts = BootstrapOpts::new().resources(here.join("resources.yaml"));
    operonx::bootstrap(opts);

    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    // `routing` skipped — needs if_() which is stubbed in Rust scheduler.
    for name in ["parallel", "load_balanced", "fallback", "ensemble"] {
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
