//! Verify the scheduler dispatches `OpType::Parser` to the executor.
//!
//! Stage 7 of the operonx Rust sync. Pre-Stage-7 the scheduler routed Parser
//! ops to `Err("op type Parser not yet implemented")`. This file exercises
//! end-to-end graphs that produce parser output to confirm:
//!
//! - `mode: "json"` → strips fences + parses + extracts via dot path
//! - `mode: "xml"`  → repeated tags collapse to lists, single root flattens
//! - `mode: "yaml"` → basic key/value parses
//! - Missing field → `null`
//! - Type coercion (`bool` / `int` / `str`)
//! - `error: null` on success, `error: "<msg>"` on parse failure
//! - `validators` `@DEFAULT` fallback semantics

use operonx::Operon;
use serde_json::{json, Map, Value};

/// One-op graph: a parser op consuming the literal `text` input and emitting
/// `extract`'s fields as PARENT-forwarded outputs.
fn parser_only_graph(
    text: &str,
    mode: &str,
    extract: Vec<String>,
    validators: Option<Value>,
    outputs: Vec<&str>,
) -> String {
    let mut inputs = Map::new();
    inputs.insert(
        "text".into(),
        json!({"required": true, "literal": text}),
    );
    inputs.insert(
        "mode".into(),
        json!({"required": false, "literal": mode}),
    );
    inputs.insert(
        "schema".into(),
        json!({"required": false, "literal": extract}),
    );
    if let Some(v) = validators {
        inputs.insert(
            "validators".into(),
            json!({"required": false, "literal": v}),
        );
    }

    let mut output_map = Map::new();
    for var in &outputs {
        output_map.insert(
            (*var).to_string(),
            json!({
                "ref": {
                    "source": "__PARENT__",
                    "var": var,
                    "is_output": true
                }
            }),
        );
    }

    let mut parent_outputs = Map::new();
    for var in &outputs {
        parent_outputs.insert((*var).to_string(), json!({}));
    }

    json!({
        "schema_version": "1.0",
        "type": "graph",
        "name": "main",
        "full_name": "main",
        "entries": ["p"],
        "exits": ["p"],
        "initial_ready_count": {"p": 0},
        "compiled_adj": {"p": []},
        "inputs": {},
        "outputs": parent_outputs,
        "ops": {
            "p": {
                "type": "parser",
                "name": "p",
                "full_name": "main.p",
                "bound": "sync",
                "inputs": inputs,
                "outputs": output_map
            }
        }
    })
    .to_string()
}

async fn run_parser(graph: String) -> Value {
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .build()
        .expect("build engine");
    engine
        .run_json_async(Map::new(), None, None, None)
        .await
        .expect("run")
}

#[tokio::test]
async fn parser_dispatch_json_with_code_fence() {
    let graph = parser_only_graph(
        "```json\n{\"user\": {\"name\": \"Alice\", \"age\": 30}}\n```",
        "json",
        vec!["user.name: str".into(), "user.age: int".into()],
        None,
        vec!["name", "age", "error"],
    );
    let out = run_parser(graph).await;
    assert_eq!(out["name"], json!("Alice"));
    assert_eq!(out["age"], json!(30));
    assert_eq!(out["error"], Value::Null);
}

#[tokio::test]
async fn parser_dispatch_xml_single_root() {
    let graph = parser_only_graph(
        "<root><name>Alice</name><age>30</age></root>",
        "xml",
        vec!["root.name: str".into(), "root.age: int".into()],
        None,
        vec!["name", "age", "error"],
    );
    let out = run_parser(graph).await;
    assert_eq!(out["name"], json!("Alice"));
    assert_eq!(out["age"], json!(30));
    assert_eq!(out["error"], Value::Null);
}

#[tokio::test]
async fn parser_dispatch_yaml_basic() {
    let graph = parser_only_graph(
        "user:\n  name: Alice\n  age: 30",
        "yaml",
        vec!["user.name: str".into(), "user.age: int".into()],
        None,
        vec!["name", "age", "error"],
    );
    let out = run_parser(graph).await;
    assert_eq!(out["name"], json!("Alice"));
    assert_eq!(out["age"], json!(30));
}

#[tokio::test]
async fn parser_dispatch_missing_field_returns_null() {
    let graph = parser_only_graph(
        r#"{"present": "x"}"#,
        "json",
        vec!["missing: str".into()],
        None,
        vec!["missing", "error"],
    );
    let out = run_parser(graph).await;
    // Python parity: at parser_op.py:228, `if value is None: return None`
    // runs *before* the str-type branch, so the str-typed None never gets
    // coerced to "". Missing fields stay Null in the output.
    assert_eq!(out["missing"], Value::Null);
    assert_eq!(out["error"], Value::Null);
}

#[tokio::test]
async fn parser_dispatch_bool_coercion_from_xml_string() {
    let graph = parser_only_graph(
        "<root><flag>true</flag></root>",
        "xml",
        vec!["root.flag: bool".into()],
        None,
        vec!["flag", "error"],
    );
    let out = run_parser(graph).await;
    assert_eq!(out["flag"], json!(true));
}

#[tokio::test]
async fn parser_dispatch_parse_error_returns_error_field() {
    let graph = parser_only_graph(
        "{ not valid json",
        "json",
        vec!["x: str".into()],
        None,
        vec!["error"],
    );
    let out = run_parser(graph).await;
    let err = out["error"].as_str().unwrap_or("");
    assert!(err.contains("Parse error"), "got: {err}");
    assert!(err.contains("json"), "got: {err}");
}

#[tokio::test]
async fn parser_dispatch_empty_text_returns_error_field() {
    let graph = parser_only_graph(
        "",
        "json",
        vec!["x: str".into()],
        None,
        vec!["error"],
    );
    let out = run_parser(graph).await;
    assert_eq!(out["error"], json!("Empty input text"));
}

#[tokio::test]
async fn parser_dispatch_validator_at_default_fallback() {
    let graph = parser_only_graph(
        r#"{"mode": "weird_unknown_value"}"#,
        "json",
        vec!["mode: str".into()],
        Some(json!({"mode": ["a", "b", "@FALLBACK"]})),
        vec!["mode", "error"],
    );
    let out = run_parser(graph).await;
    assert_eq!(out["mode"], json!("FALLBACK"));
    assert_eq!(out["error"], Value::Null);
}

#[tokio::test]
async fn parser_dispatch_validator_no_default_returns_error() {
    let graph = parser_only_graph(
        r#"{"mode": "weird"}"#,
        "json",
        vec!["mode: str".into()],
        Some(json!({"mode": ["a", "b"]})),
        vec!["mode", "error"],
    );
    let out = run_parser(graph).await;
    let err = out["error"].as_str().unwrap_or("");
    assert!(err.contains("Validation failed"), "got: {err}");
    assert!(err.contains("'mode'"), "got: {err}");
    assert!(err.contains("weird"), "got: {err}");
}

#[tokio::test]
async fn parser_dispatch_xml_repeated_tags_become_list() {
    let graph = parser_only_graph(
        "<root><item>a</item><item>b</item></root>",
        "xml",
        vec!["root.item: list".into()],
        None,
        vec!["item", "error"],
    );
    let out = run_parser(graph).await;
    assert_eq!(out["item"], json!(["a", "b"]));
}
