//! Complex graph pattern tests — converted from test_complex_graphs.py.
//!
//! Covers: deep nesting, wide parallelism, diamond patterns, branch+iteration combos,
//! output mapping edge cases, large data handling, WhileOp advanced patterns.

mod common;

use common::*;
use serde_json::json;

// =============================================================================
// Local helpers for complex_graphs tests
// =============================================================================

/// Build a nested graph op with a single inner op chain (simplified).
/// Assumes: first inner op is entry, all ops chain linearly to __end__.
fn nested_graph_op_simple(
    name: &str,
    parent_graph: &str,
    inner_ops: Vec<(String, serde_json::Value)>,
    inputs: Vec<(String, serde_json::Value)>,
) -> serde_json::Value {
    let full_name = format!("{}.{}", parent_graph, name);
    let entries = vec![inner_ops[0].0.clone()];

    let mut ready_count = serde_json::Map::new();
    let mut adj = serde_json::Map::new();

    for (i, (op_name, _)) in inner_ops.iter().enumerate() {
        ready_count.insert(op_name.clone(), json!(if i == 0 { 0 } else { 1 }));
        let target = if i + 1 < inner_ops.len() {
            inner_ops[i + 1].0.clone()
        } else {
            "__end__".to_string()
        };
        adj.insert(op_name.clone(), json!([[target, false]]));
    }

    let ops_map: serde_json::Map<String, serde_json::Value> =
        inner_ops.into_iter().collect();
    let inputs_map: serde_json::Map<String, serde_json::Value> =
        inputs.into_iter().collect();

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
        "outputs": {},
        "ops": ops_map,
        "entries": entries,
        "initial_ready_count": ready_count,
        "compiled_adj": adj,
        "has_soft_preds": [],
    })
}

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
    let inputs_map: serde_json::Map<String, serde_json::Value> =
        inputs.into_iter().collect();
    let outputs_map: serde_json::Map<String, serde_json::Value> =
        outputs.into_iter().collect();

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
        "ops": inner_ops_map,
        "entries": inner_entries,
        "initial_ready_count": inner_ready_count,
        "compiled_adj": inner_adj,
        "has_soft_preds": [],
    })
}

// =============================================================================
// Deep nesting tests
// =============================================================================

#[test]
fn test_three_level_nesting() {

    // Innermost: double(x=PARENT["x"])
    let inner_double = func_op(
        "d",
        "g.top.sub",
        "double",
        vec![ref_input("x", parent_ref("g.top.sub", "x"))],
        vec![],
    );

    // Middle: graph containing inner_double
    let middle = nested_graph_op_simple(
        "sub",
        "g.top",
        vec![("d".into(), inner_double)],
        vec![ref_input("x", parent_ref("g.top", "x"))],
    );

    // Outer: graph containing middle
    let top = nested_graph_op_simple(
        "top",
        "g",
        vec![("sub".into(), middle)],
        vec![ref_input("x", parent_ref("g", "x"))],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "top": top },
        "entries": ["top"],
        "initial_ready_count": { "top": 0 },
        "compiled_adj": { "top": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 5}));
    assert_eq!(result["result"], 10);
}

#[test]
fn test_deep_chain_in_nested_graph() {

    // Inner graph: triple double chain
    let a = func_op(
        "a",
        "g.step",
        "double",
        vec![ref_input("x", parent_ref("g.step", "x"))],
        vec![],
    );
    let b = func_op(
        "b",
        "g.step",
        "double",
        vec![ref_input("x", op_ref("g.step.a", "result"))],
        vec![],
    );
    let c = func_op(
        "c",
        "g.step",
        "double",
        vec![ref_input("x", op_ref("g.step.b", "result"))],
        vec![],
    );

    let inner = json!({
        "type": "graph",
        "name": "step",
        "full_name": "g.step",
        "rust_op": null,
        "is_async": false,
        "enabled": true,
        "verbose": false,
        "stream": false,
        "bound": "cpu",
        "inputs": {
            "x": { "ref": { "source": "g", "var": "x", "transforms": [], "is_output": false }, "literal": null, "default": null, "required": false }
        },
        "outputs": {},
        "ops": { "a": a, "b": b, "c": c },
        "entries": ["a"],
        "initial_ready_count": { "a": 0, "b": 1, "c": 1 },
        "compiled_adj": {
            "a": [["b", false]],
            "b": [["c", false]],
            "c": [["__end__", false]]
        },
        "has_soft_preds": []
    });

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "step": inner },
        "entries": ["step"],
        "initial_ready_count": { "step": 0 },
        "compiled_adj": { "step": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 2}));
    assert_eq!(result["result"], 16); // 2*2*2*2
}

// =============================================================================
// Wide parallelism tests
// =============================================================================

#[test]
fn test_five_parallel_ops() {

    let mut ops = serde_json::Map::new();
    let mut ready_count = serde_json::Map::new();
    let mut adj = serde_json::Map::new();
    let entries: Vec<String> = (0..5).map(|i| format!("s{}", i)).collect();

    // 5 square ops
    for i in 0..5 {
        let name = format!("s{}", i);
        let input_key = format!("n{}", i);
        let op = func_op(
            &name,
            "g",
            "square",
            vec![ref_input("n", parent_ref("g", &input_key))],
            vec![],
        );
        ops.insert(name.clone(), op);
        ready_count.insert(name.clone(), json!(0));
        adj.insert(name, json!([["total", false]]));
    }

    // sum_all collects all 5 results
    let total = func_op(
        "total",
        "g",
        "sum_all",
        vec![
            ref_input("a", op_ref("g.s0", "result")),
            ref_input("b", op_ref("g.s1", "result")),
            ref_input("c", op_ref("g.s2", "result")),
            ref_input("d", op_ref("g.s3", "result")),
            ref_input("e", op_ref("g.s4", "result")),
        ],
        vec![],
    );
    ops.insert("total".into(), total);
    ready_count.insert("total".into(), json!(5));
    adj.insert("total".into(), json!([["__end__", false]]));

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": ops,
        "entries": entries,
        "initial_ready_count": ready_count,
        "compiled_adj": adj,
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(
        &engine,
        json!({"n0": 1, "n1": 2, "n2": 3, "n3": 4, "n4": 5}),
    );
    assert_eq!(result["total"], 55); // 1+4+9+16+25
}

// =============================================================================
// Diamond pattern tests
// =============================================================================

#[test]
fn test_double_diamond() {

    // A → B,C → D → E,F → G
    let a = func_op("a", "g", "double",
        vec![ref_input("x", parent_ref("g", "x"))], vec![]);
    let b = func_op("b", "g", "double",
        vec![ref_input("x", op_ref("g.a", "result"))], vec![]);
    let c = func_op("c", "g", "increment",
        vec![ref_input("x", op_ref("g.a", "result"))], vec![]);
    let d = func_op("d", "g", "add",
        vec![ref_input("a", op_ref("g.b", "result")), ref_input("b", op_ref("g.c", "result"))], vec![]);
    let e = func_op("e", "g", "double",
        vec![ref_input("x", op_ref("g.d", "result"))], vec![]);
    let f = func_op("f", "g", "increment",
        vec![ref_input("x", op_ref("g.d", "result"))], vec![]);
    let fin = func_op("fin", "g", "add",
        vec![ref_input("a", op_ref("g.e", "result")), ref_input("b", op_ref("g.f", "result"))], vec![]);

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "a": a, "b": b, "c": c, "d": d, "e": e, "f": f, "fin": fin },
        "entries": ["a"],
        "initial_ready_count": { "a": 0, "b": 1, "c": 1, "d": 2, "e": 1, "f": 1, "fin": 2 },
        "compiled_adj": {
            "a": [["b", false], ["c", false]],
            "b": [["d", false]],
            "c": [["d", false]],
            "d": [["e", false], ["f", false]],
            "e": [["fin", false]],
            "f": [["fin", false]],
            "fin": [["__end__", false]]
        },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 2}));
    // a:4, b:8, c:5, d:13, e:26, f:14, fin:40
    assert_eq!(result["result"], 40);
}

// =============================================================================
// Output mapping edge cases
// =============================================================================

#[test]
fn test_wildcard_output_forwarding() {

    let step = func_op(
        "step",
        "g",
        "multi",
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![
            ref_output("a", output_ref("g.step", "a")),
            ref_output("b", output_ref("g.step", "b")),
            ref_output("c", output_ref("g.step", "c")),
        ],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "step": step },
        "entries": ["step"],
        "initial_ready_count": { "step": 0 },
        "compiled_adj": { "step": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {
            "a": { "ref": { "source": "g.step", "var": "a", "transforms": [], "is_output": true }, "literal": null, "default": null, "required": false },
            "b": { "ref": { "source": "g.step", "var": "b", "transforms": [], "is_output": true }, "literal": null, "default": null, "required": false },
            "c": { "ref": { "source": "g.step", "var": "c", "transforms": [], "is_output": true }, "literal": null, "default": null, "required": false }
        }
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 10}));
    assert_eq!(result["a"], 11);
    assert_eq!(result["b"], 12);
    assert_eq!(result["c"], 13);
}

#[test]
fn test_output_rename_mapping() {

    let step = func_op(
        "step",
        "g",
        "compute",
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "step": step },
        "entries": ["step"],
        "initial_ready_count": { "step": 0 },
        "compiled_adj": { "step": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {
            "answer": {
                "ref": { "source": "g.step", "var": "result", "transforms": [], "is_output": true },
                "literal": null, "default": null, "required": false
            }
        }
    });

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 7}));
    assert_eq!(result["answer"], 70);
}

// =============================================================================
// Large data handling
// =============================================================================

#[test]
fn test_large_list_passthrough() {
    let config = single_op_graph(
        "g",
        "step",
        "process_list",
        vec![ref_input("items", parent_ref("g", "items"))],
        vec![],
    );
    let engine = make_rush(config);
    let large_list: Vec<i64> = (0..1000).collect();
    let expected_sum: i64 = large_list.iter().sum();
    let result = run(&engine, json!({"items": large_list}));
    assert_eq!(result["count"], 1000);
    assert_eq!(result["sum"], expected_sum);
}

#[test]
fn test_large_string_passthrough() {
    let config = single_op_graph(
        "g",
        "step",
        "measure",
        vec![ref_input("text", parent_ref("g", "text"))],
        vec![],
    );
    let engine = make_rush(config);
    let large_text = "x".repeat(100_000);
    let result = run(&engine, json!({"text": large_text}));
    assert_eq!(result["length"], 100_000);
    assert_eq!(result["first"], "x".repeat(10));
}

#[test]
fn test_many_for_iterations() {

    let items: Vec<i64> = (0..100).collect();

    // ForOp: double each of 100 items
    let inner_node = func_op(
        "node",
        "g.loop",
        "dbl",
        vec![ref_input("value", parent_ref("g.loop", "value"))],
        vec![],
    );

    let loop_op = for_op(
        "loop",
        "g",
        json!({
            "value": {
                "ref": null,
                "literal": items
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
    let results = result["result"].as_array().unwrap();
    assert_eq!(results.len(), 100);
    for i in 0..100 {
        assert_eq!(results[i], json!(i as i64 * 2));
    }
}