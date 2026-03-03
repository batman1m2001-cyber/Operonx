//! Core engine integration tests — converted from test_engine.py.
//!
//! Covers: single ops, chains, output forwarding, parallel ops, engine reuse,
//! data types, branches, nested graphs, ForOp, WhileOp, observability, error resilience.

mod common;

use common::*;
use serde_json::json;

// =============================================================================
// Single op tests
// =============================================================================

#[test]
fn test_single_rust_op() {
    let crate_path = builtin_crate_path();
    let rust_op = format!("{}::double", crate_path);

    let config = single_op_graph(
        "g",
        "d",
        &rust_op,
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 5}));
    assert_eq!(result["result"], 10);
}

#[test]
fn test_single_greet_op() {
    let crate_path = builtin_crate_path();
    let rust_op = format!("{}::greet", crate_path);

    let config = single_op_graph(
        "g",
        "step",
        &rust_op,
        vec![ref_input("name", parent_ref("g", "name"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"name": "World"}));
    assert_eq!(result["greeting"], "Hello, World!");
}

#[test]
fn test_literal_input() {
    let crate_path = builtin_crate_path();
    let rust_op = format!("{}::prefix", crate_path);

    let config = single_op_graph(
        "g",
        "step",
        &rust_op,
        vec![literal_input("text", json!("hello"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["out"], "[INFO] hello");
}

// =============================================================================
// Linear chain tests
// =============================================================================

#[test]
fn test_two_op_chain() {
    let crate_path = builtin_crate_path();

    let d = func_op(
        "d",
        "g",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let a = func_op(
        "a",
        "g",
        &format!("{}::add", crate_path),
        vec![
            ref_input("a", op_ref("g.d", "result")),
            ref_input("b", parent_ref("g", "y")),
        ],
        vec![],
    );

    let config = chain_graph("g", vec![("d".into(), d), ("a".into(), a)]);
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 5, "y": 3}));
    assert_eq!(result["result"], 13); // (5*2) + 3
}

#[test]
fn test_three_op_chain() {
    let crate_path = builtin_crate_path();

    let d1 = func_op(
        "d1",
        "g",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let d2 = func_op(
        "d2",
        "g",
        &format!("{}::double", crate_path),
        vec![ref_input("x", op_ref("g.d1", "result"))],
        vec![],
    );
    let a = func_op(
        "a",
        "g",
        &format!("{}::add", crate_path),
        vec![
            ref_input("a", op_ref("g.d2", "result")),
            ref_input("b", parent_ref("g", "y")),
        ],
        vec![],
    );

    let config = chain_graph(
        "g",
        vec![("d1".into(), d1), ("d2".into(), d2), ("a".into(), a)],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 3, "y": 1}));
    assert_eq!(result["result"], 13); // ((3*2)*2) + 1
}

// =============================================================================
// Output forwarding tests
// =============================================================================

#[test]
fn test_auto_forward_via_end() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "d",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 7}));
    assert_eq!(result["result"], 14);
}

#[test]
fn test_explicit_output_mapping() {
    let crate_path = builtin_crate_path();

    let d = func_op(
        "d",
        "g",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![ref_output("result", output_ref("g.d", "result"))],
    );

    let mut config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "d": d },
        "entries": ["d"],
        "initial_ready_count": { "d": 0 },
        "compiled_adj": { "d": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {
            "answer": {
                "ref": {
                    "source": "g.d",
                    "var": "result",
                    "ops": [],
                    "is_output": true
                },
                "literal": null,
                "default": null,
                "required": false
            }
        }
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 4}));
    assert_eq!(result["answer"], 8);
}

// =============================================================================
// Parallel (fork-join) tests
// =============================================================================

#[test]
fn test_fork_join() {
    let crate_path = builtin_crate_path();

    let d1 = func_op(
        "d1",
        "g",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let d2 = func_op(
        "d2",
        "g",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "y"))],
        vec![],
    );
    let a = func_op(
        "a",
        "g",
        &format!("{}::add", crate_path),
        vec![
            ref_input("a", op_ref("g.d1", "result")),
            ref_input("b", op_ref("g.d2", "result")),
        ],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "d1": d1, "d2": d2, "a": a },
        "entries": ["d1", "d2"],
        "initial_ready_count": { "d1": 0, "d2": 0, "a": 2 },
        "compiled_adj": {
            "d1": [["a", false]],
            "d2": [["a", false]],
            "a": [["__end__", false]]
        },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 3, "y": 5}));
    assert_eq!(result["result"], 16); // (3*2) + (5*2)
}

// =============================================================================
// Engine reuse tests
// =============================================================================

#[test]
fn test_run_twice() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "d",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = make_rush(config);

    let r1 = run(&engine, json!({"x": 5}));
    let r2 = run(&engine, json!({"x": 10}));
    assert_eq!(r1["result"], 10);
    assert_eq!(r2["result"], 20);
}

#[test]
fn test_different_inputs() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "a",
        &format!("{}::add", crate_path),
        vec![
            ref_input("a", parent_ref("g", "a")),
            ref_input("b", parent_ref("g", "b")),
        ],
        vec![],
    );
    let engine = make_rush(config);

    assert_eq!(run(&engine, json!({"a": 1, "b": 2}))["result"], 3);
    assert_eq!(run(&engine, json!({"a": 100, "b": 200}))["result"], 300);
}

// =============================================================================
// Data type tests
// =============================================================================

#[test]
fn test_float_inputs() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "d",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 2.5}));
    assert_eq!(result["result"], 5.0);
}

#[test]
fn test_string_passthrough() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "step",
        &format!("{}::passthrough", crate_path),
        vec![ref_input("value", parent_ref("g", "msg"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"msg": "hello"}));
    assert_eq!(result["out"], "hello");
}

#[test]
fn test_list_passthrough() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "step",
        &format!("{}::passthrough", crate_path),
        vec![ref_input("value", parent_ref("g", "items"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"items": [1, 2, 3]}));
    assert_eq!(result["out"], json!([1, 2, 3]));
}

#[test]
fn test_dict_passthrough() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "step",
        &format!("{}::passthrough", crate_path),
        vec![ref_input("value", parent_ref("g", "data"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run(&engine, json!({"data": {"a": 1, "b": 2}}));
    assert_eq!(result["out"], json!({"a": 1, "b": 2}));
}

// =============================================================================
// Branch op tests
// =============================================================================

/// Build a branch op config with cases and a default.
fn branch_op(
    name: &str,
    graph_name: &str,
    cases: Vec<(serde_json::Value, &str)>,
    default: Option<&str>,
) -> serde_json::Value {
    let cases_json: Vec<serde_json::Value> = cases
        .into_iter()
        .map(|(condition, target)| {
            json!({"condition": condition, "target": target})
        })
        .collect();

    json!({
        "type": "branch",
        "name": name,
        "full_name": format!("{}.{}", graph_name, name),
        "rust_op": null,
        "is_async": false,
        "enabled": true,
        "verbose": false,
        "stream": false,
        "bound": "cpu",
        "inputs": {},
        "outputs": {},
        "cases": cases_json,
        "default": default
    })
}

/// Build a `>=` condition ref for branch ops.
fn gte_condition(graph_name: &str, key: &str, value: i64) -> serde_json::Value {
    json!({
        "source": graph_name,
        "var": key,
        "ops": [["ge", [value]]],
        "is_output": false
    })
}

#[test]
fn test_simple_branch_true() {
    let crate_path = builtin_crate_path();

    let router = branch_op(
        "router",
        "g",
        vec![(gte_condition("g", "score", 90), "a")],
        Some("f"),
    );
    let a = func_op(
        "a",
        "g",
        &format!("{}::grade_a", crate_path),
        vec![],
        vec![
            ref_output("grade", output_ref("g", "grade")),
            ref_output("message", output_ref("g", "message")),
        ],
    );
    let f = func_op(
        "f",
        "g",
        &format!("{}::grade_f", crate_path),
        vec![],
        vec![
            ref_output("grade", output_ref("g", "grade")),
            ref_output("message", output_ref("g", "message")),
        ],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "router": router, "a": a, "f": f },
        "entries": ["router"],
        "initial_ready_count": { "router": 0, "a": 1, "f": 1 },
        "compiled_adj": {
            "router": [["a", true], ["f", true]],
            "a": [["__end__", true]],
            "f": [["__end__", true]]
        },
        "has_soft_preds": ["a", "f", "__end__"],
        "inputs": {},
        "outputs": {
            "grade": {
                "ref": null, "literal": null, "default": null, "required": false
            },
            "message": {
                "ref": null, "literal": null, "default": null, "required": false
            }
        }
    });

    let engine = make_rush(config);

    let result = run(&engine, json!({"score": 95}));
    assert_eq!(result["grade"], "A");
    assert_eq!(result["message"], "Excellent!");
}

#[test]
fn test_simple_branch_false() {
    let crate_path = builtin_crate_path();

    let router = branch_op(
        "router",
        "g",
        vec![(gte_condition("g", "score", 90), "a")],
        Some("f"),
    );
    let a = func_op(
        "a",
        "g",
        &format!("{}::grade_a", crate_path),
        vec![],
        vec![
            ref_output("grade", output_ref("g", "grade")),
            ref_output("message", output_ref("g", "message")),
        ],
    );
    let f = func_op(
        "f",
        "g",
        &format!("{}::grade_f", crate_path),
        vec![],
        vec![
            ref_output("grade", output_ref("g", "grade")),
            ref_output("message", output_ref("g", "message")),
        ],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "router": router, "a": a, "f": f },
        "entries": ["router"],
        "initial_ready_count": { "router": 0, "a": 1, "f": 1 },
        "compiled_adj": {
            "router": [["a", true], ["f", true]],
            "a": [["__end__", true]],
            "f": [["__end__", true]]
        },
        "has_soft_preds": ["a", "f", "__end__"],
        "inputs": {},
        "outputs": {
            "grade": {
                "ref": null, "literal": null, "default": null, "required": false
            },
            "message": {
                "ref": null, "literal": null, "default": null, "required": false
            }
        }
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"score": 50}));
    assert_eq!(result["grade"], "F");
    assert_eq!(result["message"], "Try again!");
}

#[test]
fn test_multi_condition_branch() {
    let crate_path = builtin_crate_path();

    let router = branch_op(
        "router",
        "g",
        vec![
            (gte_condition("g", "score", 90), "a"),
            (gte_condition("g", "score", 70), "b"),
        ],
        Some("f"),
    );
    let a = func_op("a", "g", &format!("{}::grade_a", crate_path), vec![], vec![
        ref_output("grade", output_ref("g", "grade")),
        ref_output("message", output_ref("g", "message")),
    ]);
    let b = func_op("b", "g", &format!("{}::grade_b", crate_path), vec![], vec![
        ref_output("grade", output_ref("g", "grade")),
        ref_output("message", output_ref("g", "message")),
    ]);
    let f = func_op("f", "g", &format!("{}::grade_f", crate_path), vec![], vec![
        ref_output("grade", output_ref("g", "grade")),
        ref_output("message", output_ref("g", "message")),
    ]);

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "router": router, "a": a, "b": b, "f": f },
        "entries": ["router"],
        "initial_ready_count": { "router": 0, "a": 1, "b": 1, "f": 1 },
        "compiled_adj": {
            "router": [["a", true], ["b", true], ["f", true]],
            "a": [["__end__", true]],
            "b": [["__end__", true]],
            "f": [["__end__", true]]
        },
        "has_soft_preds": ["a", "b", "f", "__end__"],
        "inputs": {},
        "outputs": {
            "grade": { "ref": null, "literal": null, "default": null, "required": false },
            "message": { "ref": null, "literal": null, "default": null, "required": false }
        }
    });

    let engine = make_rush(config);

    let r1 = run(&engine, json!({"score": 95}));
    assert_eq!(r1["grade"], "A");

    let r2 = run(&engine, json!({"score": 75}));
    assert_eq!(r2["grade"], "B");

    let r3 = run(&engine, json!({"score": 50}));
    assert_eq!(r3["grade"], "F");
}

// =============================================================================
// Nested GraphOp tests
// =============================================================================

/// Build a nested graph op config containing inner ops.
fn nested_graph_op(
    name: &str,
    parent_graph: &str,
    inner_ops: Vec<(String, serde_json::Value)>,
    inner_entries: Vec<&str>,
    inner_ready_count: serde_json::Value,
    inner_adj: serde_json::Value,
    inner_soft_preds: Vec<&str>,
    inputs: Vec<(String, serde_json::Value)>,
    outputs: Vec<(String, serde_json::Value)>,
) -> serde_json::Value {
    let full_name = format!("{}.{}", parent_graph, name);
    let inner_ops_map: serde_json::Map<String, serde_json::Value> =
        inner_ops.into_iter().collect();
    let inputs_map: serde_json::Map<String, serde_json::Value> = inputs.into_iter().collect();
    let outputs_map: serde_json::Map<String, serde_json::Value> = outputs.into_iter().collect();

    json!({
        "type": "graph",
        "name": name,
        "full_name": full_name,
        "rust_op": null,
        "is_async": false,
        "enabled": true,
        "verbose": false,
        "stream": false,
        "bound": "cpu",
        "inputs": inputs_map,
        "outputs": outputs_map,
        // Inner graph fields (GraphConfig fields):
        "ops": inner_ops_map,
        "entries": inner_entries,
        "initial_ready_count": inner_ready_count,
        "compiled_adj": inner_adj,
        "has_soft_preds": inner_soft_preds,
    })
}

#[test]
fn test_simple_nested_graph() {
    let crate_path = builtin_crate_path();

    // Inner: double(x=PARENT["val"])
    let inner_double = func_op(
        "step",
        "g.d",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g.d", "val"))],
        vec![],
    );

    let nested = nested_graph_op(
        "d",
        "g",
        vec![("step".into(), inner_double)],
        vec!["step"],
        json!({"step": 0}),
        json!({"step": [["__end__", false]]}),
        vec![],
        vec![ref_input("val", parent_ref("g", "x"))],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "d": nested },
        "entries": ["d"],
        "initial_ready_count": { "d": 0 },
        "compiled_adj": { "d": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 5}));
    assert_eq!(result["result"], 10);
}

#[test]
fn test_chained_nested_graphs() {
    let crate_path = builtin_crate_path();

    // d1: nested graph with double
    let inner_d1 = func_op(
        "step",
        "g.d1",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g.d1", "val"))],
        vec![],
    );
    let d1 = nested_graph_op(
        "d1",
        "g",
        vec![("step".into(), inner_d1)],
        vec!["step"],
        json!({"step": 0}),
        json!({"step": [["__end__", false]]}),
        vec![],
        vec![ref_input("val", parent_ref("g", "x"))],
        vec![],
    );

    // d2: nested graph with double, reading d1's output
    let inner_d2 = func_op(
        "step",
        "g.d2",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g.d2", "val"))],
        vec![],
    );
    let d2 = nested_graph_op(
        "d2",
        "g",
        vec![("step".into(), inner_d2)],
        vec!["step"],
        json!({"step": 0}),
        json!({"step": [["__end__", false]]}),
        vec![],
        vec![ref_input("val", op_ref("g.d1", "result"))],
        vec![],
    );

    let config = chain_graph("g", vec![("d1".into(), d1), ("d2".into(), d2)]);
    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 3}));
    assert_eq!(result["result"], 12); // (3*2)*2
}

#[test]
fn test_nested_graph_with_multiple_ops() {
    let crate_path = builtin_crate_path();

    // Inner graph: double(x) -> add(double_result, y)
    let inner_d = func_op(
        "d",
        "g.step",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g.step", "x"))],
        vec![],
    );
    let inner_a = func_op(
        "a",
        "g.step",
        &format!("{}::add", crate_path),
        vec![
            ref_input("a", op_ref("g.step.d", "result")),
            ref_input("b", parent_ref("g.step", "y")),
        ],
        vec![],
    );

    let nested = nested_graph_op(
        "step",
        "g",
        vec![("d".into(), inner_d), ("a".into(), inner_a)],
        vec!["d"],
        json!({"d": 0, "a": 1}),
        json!({"d": [["a", false]], "a": [["__end__", false]]}),
        vec![],
        vec![
            ref_input("x", parent_ref("g", "x")),
            ref_input("y", parent_ref("g", "y")),
        ],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "step": nested },
        "entries": ["step"],
        "initial_ready_count": { "step": 0 },
        "compiled_adj": { "step": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 5, "y": 3}));
    assert_eq!(result["result"], 13); // (5*2) + 3
}

// =============================================================================
// ForOp tests
// =============================================================================

/// Build a ForOp config.
fn for_op(
    name: &str,
    parent_graph: &str,
    each: serde_json::Value,
    broadcast: serde_json::Value,
    inner_ops: Vec<(String, serde_json::Value)>,
    inner_entries: Vec<&str>,
    inner_ready_count: serde_json::Value,
    inner_adj: serde_json::Value,
    inputs: Vec<(String, serde_json::Value)>,
    outputs: Vec<(String, serde_json::Value)>,
) -> serde_json::Value {
    let full_name = format!("{}.{}", parent_graph, name);
    let inner_ops_map: serde_json::Map<String, serde_json::Value> =
        inner_ops.into_iter().collect();
    let inputs_map: serde_json::Map<String, serde_json::Value> = inputs.into_iter().collect();
    let outputs_map: serde_json::Map<String, serde_json::Value> = outputs.into_iter().collect();

    json!({
        "type": "for",
        "name": name,
        "full_name": full_name,
        "rust_op": null,
        "is_async": false,
        "enabled": true,
        "verbose": false,
        "stream": false,
        "bound": "cpu",
        "inputs": inputs_map,
        "outputs": outputs_map,
        "each": each,
        "broadcast": broadcast,
        "fail_fast": false,
        // Inner graph fields:
        "ops": inner_ops_map,
        "entries": inner_entries,
        "initial_ready_count": inner_ready_count,
        "compiled_adj": inner_adj,
        "has_soft_preds": [],
    })
}

#[test]
fn test_simple_for_literal_each() {
    let crate_path = builtin_crate_path();

    let inner_node = func_op(
        "node",
        "g.loop",
        &format!("{}::dbl", crate_path),
        vec![ref_input("value", parent_ref("g.loop", "value"))],
        vec![],
    );

    let loop_op = for_op(
        "loop",
        "g",
        json!({
            "value": {
                "ref": null,
                "literal": [1, 2, 3]
            }
        }),
        json!({}),
        vec![("node".into(), inner_node)],
        vec!["node"],
        json!({"node": 0}),
        json!({"node": [["__end__", false]]}),
        vec![],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_op },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["result"], json!([2, 4, 6]));
}

#[test]
fn test_for_with_broadcast() {
    let crate_path = builtin_crate_path();

    let inner_node = func_op(
        "node",
        "g.loop",
        &format!("{}::multiply_values", crate_path),
        vec![
            ref_input("value", parent_ref("g.loop", "value")),
            ref_input("multiplier", parent_ref("g.loop", "multiplier")),
        ],
        vec![],
    );

    let loop_op = for_op(
        "loop",
        "g",
        json!({
            "value": {
                "ref": null,
                "literal": [1, 2, 3]
            }
        }),
        json!({
            "multiplier": {
                "ref": null,
                "literal": 10
            }
        }),
        vec![("node".into(), inner_node)],
        vec!["node"],
        json!({"node": 0}),
        json!({"node": [["__end__", false]]}),
        vec![],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_op },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["result"], json!([10, 20, 30]));
}

#[test]
fn test_for_multiple_each_zip() {
    let crate_path = builtin_crate_path();

    let inner_node = func_op(
        "node",
        "g.loop",
        &format!("{}::add", crate_path),
        vec![
            ref_input("a", parent_ref("g.loop", "a")),
            ref_input("b", parent_ref("g.loop", "b")),
        ],
        vec![],
    );

    let loop_op = for_op(
        "loop",
        "g",
        json!({
            "a": { "ref": null, "literal": [1, 2, 3] },
            "b": { "ref": null, "literal": [10, 20, 30] }
        }),
        json!({}),
        vec![("node".into(), inner_node)],
        vec!["node"],
        json!({"node": 0}),
        json!({"node": [["__end__", false]]}),
        vec![],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_op },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["result"], json!([11, 22, 33]));
}

#[test]
fn test_for_empty_list() {
    let crate_path = builtin_crate_path();

    let inner_node = func_op(
        "node",
        "g.loop",
        &format!("{}::dbl", crate_path),
        vec![ref_input("value", parent_ref("g.loop", "value"))],
        vec![],
    );

    // Empty list needs explicit output param since auto-detection can't work with 0 iterations
    let loop_op = for_op(
        "loop",
        "g",
        json!({ "value": { "ref": null, "literal": [] } }),
        json!({}),
        vec![("node".into(), inner_node)],
        vec!["node"],
        json!({"node": 0}),
        json!({"node": [["__end__", false]]}),
        vec![],
        vec![("result".into(), json!({"ref": null, "literal": null, "default": null, "required": false}))],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_op },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["result"], json!([]));
}

#[test]
fn test_for_with_upstream_ref() {
    let crate_path = builtin_crate_path();

    // Source op: make_list() -> {"items": [10, 20, 30]}
    let src = func_op(
        "src",
        "g",
        &format!("{}::make_list", crate_path),
        vec![],
        vec![],
    );

    // Inner op: double(x=PARENT["value"])
    let inner_node = func_op(
        "node",
        "g.loop",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g.loop", "value"))],
        vec![],
    );

    let loop_op = for_op(
        "loop",
        "g",
        json!({
            "value": {
                "ref": {
                    "source": "g.src",
                    "var": "items",
                    "ops": [],
                    "is_output": false
                },
                "literal": null
            }
        }),
        json!({}),
        vec![("node".into(), inner_node)],
        vec!["node"],
        json!({"node": 0}),
        json!({"node": [["__end__", false]]}),
        vec![],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "src": src, "loop": loop_op },
        "entries": ["src"],
        "initial_ready_count": { "src": 0, "loop": 1 },
        "compiled_adj": {
            "src": [["loop", false]],
            "loop": [["__end__", false]]
        },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["result"], json!([20, 40, 60]));
}

// =============================================================================
// WhileOp tests
// =============================================================================

/// Build a WhileOp config.
fn while_op(
    name: &str,
    parent_graph: &str,
    broadcast: serde_json::Value,
    until: Option<&str>,
    max_iterations: Option<usize>,
    inner_ops: Vec<(String, serde_json::Value)>,
    inner_entries: Vec<&str>,
    inner_ready_count: serde_json::Value,
    inner_adj: serde_json::Value,
    inputs: Vec<(String, serde_json::Value)>,
    outputs: Vec<(String, serde_json::Value)>,
) -> serde_json::Value {
    let full_name = format!("{}.{}", parent_graph, name);
    let inner_ops_map: serde_json::Map<String, serde_json::Value> =
        inner_ops.into_iter().collect();
    let inputs_map: serde_json::Map<String, serde_json::Value> = inputs.into_iter().collect();
    let outputs_map: serde_json::Map<String, serde_json::Value> = outputs.into_iter().collect();

    json!({
        "type": "while",
        "name": name,
        "full_name": full_name,
        "rust_op": null,
        "is_async": false,
        "enabled": true,
        "verbose": false,
        "stream": false,
        "bound": "cpu",
        "inputs": inputs_map,
        "outputs": outputs_map,
        "each": {},
        "broadcast": broadcast,
        "until": until,
        "max_iterations": max_iterations,
        // Inner graph fields:
        "ops": inner_ops_map,
        "entries": inner_entries,
        "initial_ready_count": inner_ready_count,
        "compiled_adj": inner_adj,
        "has_soft_preds": [],
    })
}

#[test]
fn test_while_simple_counter() {
    let crate_path = builtin_crate_path();

    let inner_step = func_op(
        "step",
        "g.loop",
        &format!("{}::increment_counter", crate_path),
        vec![ref_input("counter", parent_ref("g.loop", "counter"))],
        vec![ref_output("new_counter", output_ref("g.loop.step", "new_counter"))],
    );

    let loop_op = while_op(
        "loop",
        "g",
        json!({
            "counter": { "ref": null, "literal": 0 }
        }),
        Some("counter >= 5"),
        None,
        vec![("step".into(), inner_step)],
        vec!["step"],
        json!({"step": 0}),
        json!({"step": [["__end__", false]]}),
        vec![],
        vec![],
    );

    // Add output mapping for counter
    let mut loop_val = loop_op;
    loop_val["outputs"] = json!({
        "counter": {
            "ref": {
                "source": "g.loop.step",
                "var": "new_counter",
                "ops": [],
                "is_output": true
            },
            "literal": null,
            "default": null,
            "required": false
        }
    });

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_val },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["counter"], 5);
}

#[test]
fn test_while_max_iterations() {
    let crate_path = builtin_crate_path();

    let inner_step = func_op(
        "step",
        "g.loop",
        &format!("{}::increment_counter", crate_path),
        vec![ref_input("counter", parent_ref("g.loop", "counter"))],
        vec![],
    );

    let mut loop_op = while_op(
        "loop",
        "g",
        json!({ "counter": { "ref": null, "literal": 0 } }),
        None,
        Some(5),
        vec![("step".into(), inner_step)],
        vec!["step"],
        json!({"step": 0}),
        json!({"step": [["__end__", false]]}),
        vec![],
        vec![],
    );
    loop_op["outputs"] = json!({
        "counter": {
            "ref": {
                "source": "g.loop.step",
                "var": "new_counter",
                "ops": [],
                "is_output": true
            },
            "literal": null, "default": null, "required": false
        }
    });

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_op },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["counter"], 5);
}

#[test]
fn test_while_accumulator() {
    let crate_path = builtin_crate_path();

    let inner_step = func_op(
        "step",
        "g.loop",
        &format!("{}::accumulate", crate_path),
        vec![
            ref_input("total", parent_ref("g.loop", "total")),
            ref_input("step_size", parent_ref("g.loop", "step_size")),
        ],
        vec![],
    );

    let mut loop_op = while_op(
        "loop",
        "g",
        json!({
            "total": { "ref": null, "literal": 0 },
            "step_size": { "ref": null, "literal": 15 }
        }),
        Some("total >= 100"),
        None,
        vec![("step".into(), inner_step)],
        vec!["step"],
        json!({"step": 0}),
        json!({"step": [["__end__", false]]}),
        vec![],
        vec![],
    );
    loop_op["outputs"] = json!({
        "total": {
            "ref": {
                "source": "g.loop.step",
                "var": "new_total",
                "ops": [],
                "is_output": true
            },
            "literal": null, "default": null, "required": false
        }
    });

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_op },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["total"], 105); // 15 * 7
}

#[test]
fn test_while_fibonacci() {
    let crate_path = builtin_crate_path();

    let inner_step = func_op(
        "step",
        "g.loop",
        &format!("{}::fib_step", crate_path),
        vec![
            ref_input("a", parent_ref("g.loop", "a")),
            ref_input("b", parent_ref("g.loop", "b")),
        ],
        vec![],
    );

    let mut loop_op = while_op(
        "loop",
        "g",
        json!({
            "a": { "ref": null, "literal": 0 },
            "b": { "ref": null, "literal": 1 }
        }),
        Some("b >= 21"),
        None,
        vec![("step".into(), inner_step)],
        vec!["step"],
        json!({"step": 0}),
        json!({"step": [["__end__", false]]}),
        vec![],
        vec![],
    );
    loop_op["outputs"] = json!({
        "a": {
            "ref": { "source": "g.loop.step", "var": "new_a", "ops": [], "is_output": true },
            "literal": null, "default": null, "required": false
        },
        "b": {
            "ref": { "source": "g.loop.step", "var": "new_b", "ops": [], "is_output": true },
            "literal": null, "default": null, "required": false
        }
    });

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_op },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));
    assert_eq!(result["b"], 21); // fib: 0,1,1,2,3,5,8,13,21
}

// =============================================================================
// Observability tests
// =============================================================================

#[test]
fn test_disabled_op_skipped() {
    let crate_path = builtin_crate_path();

    let mut d = func_op(
        "d",
        "g",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    d["enabled"] = json!(false);

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "d": d },
        "entries": ["d"],
        "initial_ready_count": { "d": 0 },
        "compiled_adj": { "d": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 5}));
    assert!(result.get("result").is_none());
}

#[test]
fn test_timing_metadata() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "d",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run_full(&engine, json!({"x": 5}), None);

    assert_eq!(result["result"], 10);
    let state = &result["$state"];
    let values = &state["values"];
    assert!(values["g.d"].is_object());
    assert!(values["g.d"]["$duration_ms"].is_object() || values["g.d"]["$start_time"].is_object());
}

#[test]
fn test_tags_extraction() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "t",
        &format!("{}::tagged_op", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run_full(&engine, json!({"x": 5}), None);

    assert_eq!(result["result"], 10);
    let tags = result["$state"]["tags"].as_array().unwrap();
    let tag_strs: Vec<&str> = tags.iter().map(|t| t.as_str().unwrap()).collect();
    assert!(tag_strs.contains(&"fast"));
    assert!(tag_strs.contains(&"cached"));
}

#[test]
fn test_request_id_in_state() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "d",
        &format!("{}::double", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run_full(&engine, json!({"x": 1}), Some("req-123".into()));

    assert_eq!(result["$state"]["request_id"], "req-123");
}

// =============================================================================
// Error resilience tests
// =============================================================================

#[test]
fn test_error_in_op_stored_in_state() {
    let crate_path = builtin_crate_path();
    let config = single_op_graph(
        "g",
        "bad",
        &format!("{}::raise_op", crate_path),
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = make_rush(config);
    let result = run_full(&engine, json!({"x": 42}), None);

    // raise_op returns {"error": "boom with x=42", "$error": true}
    // Engine should store error in $state
    let state = &result["$state"];
    let values = &state["values"];
    assert!(values["g.bad"].is_object());
}

#[test]
fn test_for_op_error_in_iteration() {
    let crate_path = builtin_crate_path();

    let inner_node = func_op(
        "node",
        "g.loop",
        &format!("{}::maybe_fail", crate_path),
        vec![ref_input("value", parent_ref("g.loop", "value"))],
        vec![],
    );

    let loop_op = for_op(
        "loop",
        "g",
        json!({
            "value": { "ref": null, "literal": [1, 2, 3] }
        }),
        json!({}),
        vec![("node".into(), inner_node)],
        vec!["node"],
        json!({"node": 0}),
        json!({"node": [["__end__", false]]}),
        vec![],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop": loop_op },
        "entries": ["loop"],
        "initial_ready_count": { "loop": 0 },
        "compiled_adj": { "loop": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // maybe_fail: value==2 fails, others succeed
    let results = result["result"].as_array().unwrap();
    assert_eq!(results[0], 10);       // 1 * 10
    assert!(results[1].is_null());    // Failed (value==2)
    assert_eq!(results[2], 30);       // 3 * 10
}
