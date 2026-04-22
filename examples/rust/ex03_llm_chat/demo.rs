//! 03 LLM Chat — Rust-side usage demo.
//!
//! Mirrors `examples/python/ex03_llm_chat/workflow.py`. `PromptOp` + `LLMOp`
//! are runtime-built provider ops — we only declare the plain `@op`s here.
//!
//! Requires `OPENAI_API_KEY` in `.env` and a `resources.yaml` that exposes
//! a `gpt-4o-mini` LLM resource.
//!
//! ```sh
//! cargo run --release -p operonx --example ex03_llm_chat
//! cargo run --release -p operonx --example ex03_llm_chat -- --runs 5
//! ```

use operonx::op;
use serde_json::{json, Value};

#[op(name = "clean_text")]
fn clean_text(text: String) -> Value {
    let cleaned: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    json!({ "cleaned_text": cleaned })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex03_llm_chat";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    let scenarios = ["basic", "chain", "summarize"];
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
