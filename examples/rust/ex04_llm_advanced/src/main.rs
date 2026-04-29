//! 04 LLM Advanced — Rust-side demo.
//!
//! Structured output, tool calling, multi-turn chat. Only the plain
//! `#[op]`s declared here run as Rust code; `PromptOp` + `LLMOp` are
//! provider ops materialised from `graph.json`.
//!
//! Requires `OPENAI_API_KEY` in `.env` and `llm:gpt-4o-mini` in
//! `resources.yaml`.

use operonx::{op, BootstrapOpts, Operon};
use serde_json::Value;

#[op(name = "process_response")]
fn process_response(content: Value, tool_calls: Value) -> Value {
    let calls: Vec<Value> = match tool_calls {
        Value::Array(a) => a,
        _ => Vec::new(),
    };
    let has_tool_call = !calls.is_empty();
    let tool_result = if has_tool_call {
        let expr = calls[0]
            .pointer("/function/arguments")
            .and_then(Value::as_str)
            .and_then(|s| serde_json::from_str::<Value>(s).ok())
            .and_then(|v| {
                v.get("expression")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .unwrap_or_default();
        // Stub — production needs a real expression evaluator.
        Value::String(format!("<computed:{expr}>"))
    } else {
        Value::Null
    };
    serde_json::json!({
        "has_tool_call": has_tool_call,
        "tool_result": tool_result,
        "llm_response": content,
    })
}

#[op(name = "update_history")]
fn update_history(history: Value, message: String, response: String) -> Value {
    let mut list = match history {
        Value::Array(a) => a,
        _ => Vec::new(),
    };
    list.push(serde_json::json!({"role": "user", "content": message}));
    list.push(serde_json::json!({"role": "assistant", "content": response}));
    serde_json::json!({ "new_history": list })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));

    let opts = BootstrapOpts::new().resources(here.join("resources.yaml"));
    operonx::bootstrap(opts);

    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    for name in ["structured", "tool", "multi_turn"] {
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
