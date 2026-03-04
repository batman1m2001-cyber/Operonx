//! Provider-like workflow pattern tests — converted from test_provider_ops.py.
//!
//! The Python tests used mock @op functions for provider patterns (prompt → LLM → parse).
//! In pure Rust we test equivalent patterns using rush-ops-builtin ops:
//! chains, data flow, nested dicts, error handling, complex data types.

mod common;

use common::*;
use serde_json::json;

// =============================================================================
// Chain pattern: template → process (simulating prompt → LLM → parse)
// =============================================================================

#[test]
fn test_chain_template_then_upper() {

    // Pattern: string_template → to_upper (simulates prompt formatting → processing)
    let tpl = func_op(
        "tpl",
        "g",
        "string_template",
        vec![
            ref_input("template", parent_ref("g", "template")),
            ref_input("vars", parent_ref("g", "vars")),
        ],
        vec![],
    );
    let up = func_op(
        "up",
        "g",
        "to_upper",
        vec![ref_input("text", op_ref("g.tpl", "result"))],
        vec![],
    );

    let config = chain_graph("g", vec![("tpl".into(), tpl), ("up".into(), up)]);
    let engine = make_rush(config);
    let result = run(
        &engine,
        json!({"template": "Hello {name}!", "vars": {"name": "Alice"}}),
    );
    assert_eq!(result["result"], "HELLO ALICE!");
}

#[test]
fn test_chain_three_step_greet_upper() {

    // Pattern: greet → to_upper (simulates prompt formatting → post-processing)
    let g_op = func_op(
        "greet",
        "g",
        "greet",
        vec![ref_input("name", parent_ref("g", "name"))],
        vec![],
    );
    let up = func_op(
        "up",
        "g",
        "to_upper",
        vec![ref_input("text", op_ref("g.greet", "greeting"))],
        vec![],
    );

    let config = chain_graph(
        "g",
        vec![("greet".into(), g_op), ("up".into(), up)],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"name": "World"}));
    assert_eq!(result["result"], "HELLO, WORLD!");
}

// =============================================================================
// Data flow — nested dicts through Rush engine
// =============================================================================

#[test]
fn test_nested_dict_passthrough() {

    // json_extract navigates nested dicts: {"a": {"b": {"c": 42}}} path="a.b.c" → 42
    let config = single_op_graph(
        "g",
        "step",
        "json_extract",
        vec![
            ref_input("data", parent_ref("g", "data")),
            ref_input("path", parent_ref("g", "path")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(
        &engine,
        json!({"data": {"model": "gpt-4o", "params": {"temperature": 0.7}}, "path": "params.temperature"}),
    );
    assert_eq!(result["value"], 0.7);
}

#[test]
fn test_json_merge_nested_configs() {

    // Merge two config-like dicts (simulates provider config merging)
    let config = single_op_graph(
        "g",
        "step",
        "json_merge",
        vec![
            ref_input("a", parent_ref("g", "base")),
            ref_input("b", parent_ref("g", "override")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(
        &engine,
        json!({
            "base": {"model": "gpt-3.5", "temperature": 0.5, "max_tokens": 100},
            "override": {"model": "gpt-4o", "temperature": 0.7}
        }),
    );
    assert_eq!(result["result"]["model"], "gpt-4o");
    assert_eq!(result["result"]["temperature"], 0.7);
    assert_eq!(result["result"]["max_tokens"], 100);
}

// =============================================================================
// Float precision through Rush engine
// =============================================================================

#[test]
fn test_float_precision_math_sum() {

    let config = single_op_graph(
        "g",
        "step",
        "math_sum",
        vec![ref_input("values", parent_ref("g", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": [0.1, 0.2, 0.3]}));
    let sum = result["result"].as_f64().unwrap();
    assert!((sum - 0.6).abs() < 1e-10, "Expected ~0.6, got {}", sum);
}

#[test]
fn test_float_precision_math_mean() {

    let config = single_op_graph(
        "g",
        "step",
        "math_mean",
        vec![ref_input("values", parent_ref("g", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": [0.9, 0.8, 0.7]}));
    let mean = result["result"].as_f64().unwrap();
    assert!((mean - 0.8).abs() < 1e-10, "Expected ~0.8, got {}", mean);
}

// =============================================================================
// Empty collections
// =============================================================================

#[test]
fn test_empty_list_math_sum() {

    let config = single_op_graph(
        "g",
        "step",
        "math_sum",
        vec![ref_input("values", parent_ref("g", "values"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"values": []}));
    assert_eq!(result["result"], 0.0);
}

#[test]
fn test_empty_dict_json_merge() {

    let config = single_op_graph(
        "g",
        "step",
        "json_merge",
        vec![
            ref_input("a", parent_ref("g", "a")),
            ref_input("b", parent_ref("g", "b")),
        ],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"a": {}, "b": {}}));
    assert_eq!(result["result"], json!({}));
}

// =============================================================================
// Error handling — fail_op produces error in state
// =============================================================================

#[test]
fn test_error_op_stores_error_in_state() {

    let config = single_op_graph(
        "g",
        "step",
        "fail_op",
        vec![ref_input("message", parent_ref("g", "message"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run_full(&engine, json!({"message": "API rate limit exceeded"}), None);

    // fail_op returns {"error": "...", "$error": true}
    // The $error flag is set in the op's output
    let state = &result["$state"];
    assert!(state.is_object(), "Expected $state metadata");
}

// =============================================================================
// Large data through Rush engine
// =============================================================================

#[test]
fn test_large_list_processing() {

    let config = single_op_graph(
        "g",
        "step",
        "process_list",
        vec![ref_input("items", parent_ref("g", "items"))],
        vec![],
    );
    let engine = make_rush(config);
    let large_list: Vec<i64> = (0..500).collect();
    let expected_sum: i64 = large_list.iter().sum();
    let result = run(&engine, json!({"items": large_list}));
    assert_eq!(result["count"], 500);
    assert_eq!(result["sum"], expected_sum);
}

// =============================================================================
// String data flow patterns
// =============================================================================

#[test]
fn test_string_split_then_concat() {

    // split "a,b,c" by "," → ["a","b","c"], then concat back → "abc"
    let split = func_op(
        "split",
        "g",
        "string_split",
        vec![
            ref_input("text", parent_ref("g", "text")),
            ref_input("delimiter", parent_ref("g", "delim")),
        ],
        vec![],
    );
    let concat = func_op(
        "concat",
        "g",
        "string_concat",
        vec![ref_input("parts", op_ref("g.split", "parts"))],
        vec![],
    );

    let config = chain_graph("g", vec![("split".into(), split), ("concat".into(), concat)]);
    let engine = make_rush(config);
    let result = run(&engine, json!({"text": "hello,world,foo", "delim": ","}));
    assert_eq!(result["result"], "helloworldfoo");
}