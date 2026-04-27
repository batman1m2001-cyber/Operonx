//! 04 LLM Advanced — Rust-side usage demo.
//!
//! Structured output, tool calling, multi-turn chat. Only the plain `@op`s
//! from `workflow.py` are declared here; `PromptOp` + `LLMOp` are
//! runtime-built provider ops.
//!
//! Requires `OPENAI_API_KEY` in `.env`.

use operonx::op;
use serde_json::{json, Value};

// `content` may be `null` → take `Value`. `tool_calls` may be null or list.
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
        // Rust-side stub — production needs a real expression evaluator.
        Value::String(format!("<computed:{}>", expr))
    } else {
        Value::Null
    };
    json!({
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
    list.push(json!({"role": "user", "content": message}));
    list.push(json!({"role": "assistant", "content": response}));
    json!({ "new_history": list })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex04_llm_advanced";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    let scenarios = ["structured", "tool", "multi_turn"];
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
