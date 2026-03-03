//! Integration tests for built-in Rust ops (string, JSON, math) via cdylib plugin.
//!
//! Converted from test_builtin_ops.py — tests all 13+ ops from rush-ops-builtin.

mod common;

use common::*;
use serde_json::json;

// =============================================================================
// String ops
// =============================================================================

#[test]
fn test_string_concat() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::string_concat", crate_path),
        vec![ref_input("parts", parent_ref("test", "parts"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"parts": ["hello", " ", "world"]}));
    assert_eq!(result["result"], "hello world");
}

#[test]
fn test_string_concat_empty() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::string_concat", crate_path),
        vec![ref_input("parts", parent_ref("test", "parts"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"parts": []}));
    assert_eq!(result["result"], "");
}

#[test]
fn test_string_split() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::string_split", crate_path),
        vec![
            ref_input("text", parent_ref("test", "text")),
            ref_input("delimiter", parent_ref("test", "delim")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"text": "a,b,c", "delim": ","}));
    assert_eq!(result["parts"], json!(["a", "b", "c"]));
}

#[test]
fn test_string_split_no_match() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::string_split", crate_path),
        vec![
            ref_input("text", parent_ref("test", "text")),
            ref_input("delimiter", parent_ref("test", "delim")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"text": "hello", "delim": ","}));
    assert_eq!(result["parts"], json!(["hello"]));
}

#[test]
fn test_string_template() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::string_template", crate_path),
        vec![
            ref_input("template", parent_ref("test", "tpl")),
            ref_input("vars", parent_ref("test", "vars")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(
        &engine,
        json!({"tpl": "Hello {name}, you are {age}!", "vars": {"name": "Alice", "age": "30"}}),
    );
    assert_eq!(result["result"], "Hello Alice, you are 30!");
}

#[test]
fn test_string_template_no_vars() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::string_template", crate_path),
        vec![
            ref_input("template", parent_ref("test", "tpl")),
            ref_input("vars", parent_ref("test", "vars")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"tpl": "no placeholders", "vars": {}}));
    assert_eq!(result["result"], "no placeholders");
}

// =============================================================================
// JSON ops
// =============================================================================

#[test]
fn test_json_parse() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::json_parse", crate_path),
        vec![ref_input("text", parent_ref("test", "text"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"text": r#"{"key": "value", "num": 42}"#}));
    assert_eq!(result["data"], json!({"key": "value", "num": 42}));
}

#[test]
fn test_json_parse_list() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::json_parse", crate_path),
        vec![ref_input("text", parent_ref("test", "text"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"text": "[1, 2, 3]"}));
    assert_eq!(result["data"], json!([1, 2, 3]));
}

#[test]
fn test_json_extract() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::json_extract", crate_path),
        vec![
            ref_input("data", parent_ref("test", "data")),
            ref_input("path", parent_ref("test", "path")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"data": {"a": {"b": 1}}, "path": "a.b"}));
    assert_eq!(result["value"], 1);
}

#[test]
fn test_json_merge() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::json_merge", crate_path),
        vec![
            ref_input("a", parent_ref("test", "a")),
            ref_input("b", parent_ref("test", "b")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(
        &engine,
        json!({"a": {"x": 1, "y": 2}, "b": {"y": 3, "z": 4}}),
    );
    assert_eq!(result["result"], json!({"x": 1, "y": 3, "z": 4}));
}

#[test]
fn test_json_merge_empty() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::json_merge", crate_path),
        vec![
            ref_input("a", parent_ref("test", "a")),
            ref_input("b", parent_ref("test", "b")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"a": {"x": 1}, "b": {}}));
    assert_eq!(result["result"], json!({"x": 1}));
}

// =============================================================================
// Math ops
// =============================================================================

#[test]
fn test_math_sum() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::math_sum", crate_path),
        vec![ref_input("values", parent_ref("test", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": [1, 2, 3, 4]}));
    assert_eq!(result["result"], 10.0);
}

#[test]
fn test_math_sum_empty() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::math_sum", crate_path),
        vec![ref_input("values", parent_ref("test", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": []}));
    assert_eq!(result["result"], 0.0);
}

#[test]
fn test_math_mean() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::math_mean", crate_path),
        vec![ref_input("values", parent_ref("test", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": [2, 4, 6]}));
    assert_eq!(result["result"], 4.0);
}

#[test]
fn test_math_mean_empty() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::math_mean", crate_path),
        vec![ref_input("values", parent_ref("test", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": []}));
    assert_eq!(result["result"], 0.0);
}

#[test]
fn test_math_max() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::math_max", crate_path),
        vec![ref_input("values", parent_ref("test", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": [3, 1, 4, 1, 5]}));
    assert_eq!(result["result"], 5.0);
}

#[test]
fn test_math_min() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::math_min", crate_path),
        vec![ref_input("values", parent_ref("test", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": [3, 1, 4, 1, 5]}));
    assert_eq!(result["result"], 1.0);
}

#[test]
fn test_math_floats() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::math_sum", crate_path),
        vec![ref_input("values", parent_ref("test", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": [1.5, 2.5, 3.0]}));
    assert_eq!(result["result"], 7.0);
}

// =============================================================================
// Integration: built-in ops in workflows (chains)
// =============================================================================

#[test]
fn test_math_chain_sum_then_double() {
    let crate_path = builtin_crate_path();

    let s = func_op(
        "s",
        "chain",
        &format!("{}::math_sum", crate_path),
        vec![ref_input("values", parent_ref("chain", "nums"))],
        vec![],
    );
    let d = func_op(
        "d",
        "chain",
        &format!("{}::double", crate_path),
        vec![ref_input("x", op_ref("chain.s", "result"))],
        vec![],
    );

    let config = chain_graph("chain", vec![("s".into(), s), ("d".into(), d)]);
    let engine = make_rush(config);
    let result = run(&engine, json!({"nums": [1, 2, 3]}));
    assert_eq!(result["result"], 12.0); // sum=6, double=12
}

// =============================================================================
// Core ops: greet, add, multiply, identity, increment, square
// =============================================================================

#[test]
fn test_greet_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::greet", crate_path),
        vec![ref_input("name", parent_ref("test", "name"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"name": "Rush"}));
    assert_eq!(result["greeting"], "Hello, Rush!");
}

#[test]
fn test_add_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::add", crate_path),
        vec![
            ref_input("a", parent_ref("test", "a")),
            ref_input("b", parent_ref("test", "b")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"a": 42, "b": 58}));
    assert_eq!(result["result"], 100);
}

#[test]
fn test_multiply_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::multiply", crate_path),
        vec![
            ref_input("a", parent_ref("test", "a")),
            ref_input("b", parent_ref("test", "b")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"a": 7, "b": 6}));
    assert_eq!(result["result"], 42);
}

#[test]
fn test_square_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::square", crate_path),
        vec![ref_input("n", parent_ref("test", "n"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"n": 9}));
    assert_eq!(result["result"], 81);
}

#[test]
fn test_increment_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::increment", crate_path),
        vec![ref_input("x", parent_ref("test", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 99}));
    assert_eq!(result["result"], 100);
}

#[test]
fn test_to_upper_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::to_upper", crate_path),
        vec![ref_input("text", parent_ref("test", "text"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"text": "hello world"}));
    assert_eq!(result["result"], "HELLO WORLD");
}

#[test]
fn test_hash_chain_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::hash_chain", crate_path),
        vec![
            ref_input("data", parent_ref("test", "data")),
            ref_input("iterations", parent_ref("test", "iterations")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"data": "test", "iterations": 3}));
    // Just verify it returns a hex string
    assert!(result["hash"].is_string());
    assert_eq!(result["hash"].as_str().unwrap().len(), 16);
}

#[test]
fn test_noop_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::noop", crate_path),
        vec![],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    // noop returns {} — no keys
    assert!(result.as_object().unwrap().is_empty() || result.as_object().unwrap().len() == 0);
}

#[test]
fn test_identity_op() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "test",
        "step",
        &format!("{}::identity", crate_path),
        vec![ref_input("x", parent_ref("test", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 42}));
    // identity returns {"x": 42} as-is (all inputs as outputs)
    assert_eq!(result["x"], 42);
}
