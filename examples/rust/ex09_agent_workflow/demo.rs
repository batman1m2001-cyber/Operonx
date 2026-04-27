//! 09 Agent Workflow — Rust-side usage demo.
//!
//! Tool-calling agent mirroring `examples/python/ex09_agent_workflow/workflow.py`.
//! Only the plain `@op`s are declared; `LLMOp` is runtime-built.
//!
//! Requires `OPENAI_API_KEY` in `.env`.

use operonx::op;
use serde_json::{json, Value};

#[op(name = "init_agent")]
fn init_agent(query: String) -> Value {
    json!({
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
    // TODO: Rust side has no safe expression evaluator here. We return a
    // placeholder so the agent loop terminates. Python uses `eval()`.
    json!({ "result": format!("<computed:{}>", expr) })
}

fn search(query: &str) -> Value {
    let q = query.to_lowercase();
    let knowledge = [
        (
            "python",
            "Python is a high-level programming language created by Guido van Rossum in 1991.",
        ),
        (
            "operonx",
            "Operon is an async workflow orchestration engine for GenAI applications.",
        ),
        (
            "vietnam",
            "Vietnam is a country in Southeast Asia. Capital: Hanoi. Population: ~100 million.",
        ),
        (
            "machine learning",
            "Machine learning is a subset of AI that learns patterns from data.",
        ),
    ];
    for (k, v) in knowledge.iter() {
        if q.contains(k) {
            return json!({ "result": v });
        }
    }
    json!({ "result": "No information found." })
}

#[op(name = "process_response")]
fn process_response(content: Value, tool_calls: Value, messages: Value) -> Value {
    let mut new_messages: Vec<Value> = messages.as_array().cloned().unwrap_or_default();
    let calls: Vec<Value> = tool_calls.as_array().cloned().unwrap_or_default();

    let content_str = content.as_str().unwrap_or("").to_string();
    let mut assistant = json!({
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
            let args: Value = serde_json::from_str(args_raw).unwrap_or(json!({}));

            let result = match func_name.as_str() {
                "calculator" => {
                    let expr = args.get("expression").and_then(Value::as_str).unwrap_or("");
                    calculator(expr)
                }
                "search" => {
                    let q = args.get("query").and_then(Value::as_str).unwrap_or("");
                    search(q)
                }
                other => json!({ "error": format!("Unknown tool: {}", other) }),
            };

            new_messages.push(json!({
                "role": "tool",
                "tool_call_id": call.get("id").cloned().unwrap_or(Value::Null),
                "content": serde_json::to_string(&result).unwrap_or_else(|_| "{}".to_string()),
            }));
        }
        json!({ "messages": new_messages, "done": false, "answer": "" })
    } else {
        json!({ "messages": new_messages, "done": true, "answer": content_str })
    }
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex09_agent_workflow";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // TODO: `@graph.loop` produces a nested `OpType::Graph` with loop
    // config — the Rust scheduler currently returns empty for loop graphs,
    // so the agent will not iterate. All three scenarios are Rust-limited.
    let scenarios = ["calc", "search", "combined"];
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
