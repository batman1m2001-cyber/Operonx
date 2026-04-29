//! 03 LLM Chat — Rust-side demo.
//!
//! Mirrors `examples/python/ex03_llm_chat/main.py`. `PromptOp` + `LLMOp`
//! are runtime-built provider ops materialised from `graph.json`; we
//! only declare the plain `#[op]`s the engine needs to resolve.
//!
//! Requires `OPENAI_API_KEY` in `.env` and `llm:gpt-4o-mini` in
//! `resources.yaml` (both shipped alongside this crate).
//!
//! ```sh
//! cargo run --release
//! ```

use operonx::{op, BootstrapOpts, Operon};
use serde_json::Value;

#[op(name = "clean_text")]
fn clean_text(text: String) -> Value {
    let cleaned: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    serde_json::json!({ "cleaned_text": cleaned })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));

    // Load .env + resources.yaml from this crate's own directory so the
    // demo runs no matter where `cargo run` is invoked from.
    let opts = BootstrapOpts::new().resources(here.join("resources.yaml"));
    operonx::bootstrap(opts);

    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    for name in ["basic", "chain", "summarize"] {
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
