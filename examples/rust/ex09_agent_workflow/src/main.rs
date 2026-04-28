//! 09 Agent Workflow — Rust-side demo.
//!
//! Tool-calling agent mirroring `examples/python/ex09_agent_workflow/main.py`.
//! Only the plain `#[op]`s are declared; `LLMOp` is runtime-built.
//!
//! ⚠️  **Rust-runtime limitation** (today, v0.6.2): `@graph.loop` produces
//! a nested `OpType::Graph` with loop config that the Rust scheduler
//! currently returns empty for, so the agent will not iterate. The Python
//! side is fully functional.

use operonx::{op, BootstrapOpts, Operon};
use serde_json::Value;

#[op(name = "init_agent")]
fn init_agent(query: String) -> Value {
    serde_json::json!({
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant with access to tools. Use them when needed.",
            },
            {"role": "user", "content": query},
        ],
        "done": false,
        "answer": "",
    })
}

fn calculator(expr: &str) -> Value {
    // Stub — production needs a safe evaluator.
    serde_json::json!({ "result": format!("<computed:{expr}>") })
}

fn search(query: &str) -> Value {
    let q = query.to_lowercase();
    let knowledge = [
        ("python", "Python is a high-level programming language created by Guido van Rossum in 1991."),
        ("operonx", "Operonx is an async workflow orchestration engine for GenAI applications."),
        ("vietnam", "Vietnam is a country in Southeast Asia. Capital: Hanoi. Population: ~100 million."),
        ("machine learning", "Machine learning is a subset of AI that learns patterns from data."),
    ];
    for (k, v) in knowledge.iter() {
        if q.contains(k) {
            return serde_json::json!({ "result": v });
        }
    }
    serde_json::json!({ "result": "No information found." })
}

#[op(name = "process_response")]
fn process_response(content: Value, tool_calls: Value, messages: Value) -> Value {
    let mut new_messages: Vec<Value> = messages.as_array().cloned().unwrap_or_default();
    let calls: Vec<Value> = tool_calls.as_array().cloned().unwrap_or_default();

    let content_str = content.as_str().unwrap_or("").to_string();
    let mut assistant = serde_json::json!({
        "role": "assistant",
        "content": content_str.clone(),
    });
    if !calls.is_empty() {
        assistant["tool_calls"] = Value::Array(calls.clone());
    }
    new_messages.push(assistant);

    if !calls.is_empty() {
        for call in &calls {
            let func_name = call
                .pointer("/function/name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let args_raw = call
                .pointer("/function/arguments")
                .and_then(Value::as_str)
                .unwrap_or("{}");
            let args: Value = serde_json::from_str(args_raw).unwrap_or(serde_json::json!({}));

            let result = match func_name.as_str() {
                "calculator" => {
                    let expr = args.get("expression").and_then(Value::as_str).unwrap_or("");
                    calculator(expr)
                }
                "search" => {
                    let q = args.get("query").and_then(Value::as_str).unwrap_or("");
                    search(q)
                }
                other => serde_json::json!({ "error": format!("Unknown tool: {other}") }),
            };

            new_messages.push(serde_json::json!({
                "role": "tool",
                "tool_call_id": call.get("id").cloned().unwrap_or(Value::Null),
                "content": serde_json::to_string(&result).unwrap_or_else(|_| "{}".to_string()),
            }));
        }
        serde_json::json!({ "messages": new_messages, "done": false, "answer": "" })
    } else {
        serde_json::json!({ "messages": new_messages, "done": true, "answer": content_str })
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));

    let opts = BootstrapOpts::new().resources(here.join("resources.yaml"));
    operonx::bootstrap(opts);

    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    for name in ["calc", "search", "combined"] {
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
