//! Loop tests — GraphOp.loop with until conditions, max_iterations, variable feedback.
//!
//! Tests the new loop_config system that replaces WhileOp.
//! Loops run the graph repeatedly, feeding outputs back as inputs,
//! until an `until` condition is met or max_iterations is reached.

mod common;

use common::*;
use serde_json::json;

// =============================================================================
// Helper: build a nested graph op with loop_config (fields flattened into op JSON)
// =============================================================================

/// Build a graph op wrapping a single inner op with loop_config.
/// Inner graph fields are flattened into the op JSON (not nested under "inner_graph").
fn loop_graph_op(
    name: &str,
    parent_graph: &str,
    inner_op: serde_json::Value,
    inner_op_name: &str,
    inputs: Vec<(String, serde_json::Value)>,
    outputs: Vec<(String, serde_json::Value)>,
    until: Option<&str>,
    max_iterations: usize,
    loop_vars: Vec<&str>,
) -> serde_json::Value {
    let full_name = format!("{}.{}", parent_graph, name);
    let inputs_map: serde_json::Map<String, serde_json::Value> = inputs.into_iter().collect();
    let outputs_map: serde_json::Map<String, serde_json::Value> = outputs.into_iter().collect();
    let loop_vars_val: Vec<serde_json::Value> = loop_vars.iter().map(|v| json!(v)).collect();

    json!({
        "type": "graph",
        "name": name,
        "full_name": full_name,
        "func_name": null,
        "is_async": false,
        "enabled": true,
        "verbose": false,
        "stream": false,
        "bound": "cpu",
        "inputs": inputs_map,
        "outputs": outputs_map,
        // Inner graph fields (flattened — GraphConfig::from_value reads these):
        "ops": { inner_op_name: inner_op },
        "entries": [inner_op_name],
        "initial_ready_count": { inner_op_name: 0 },
        "compiled_adj": { inner_op_name: [["__end__", false]] },
        "has_soft_preds": [],
        "loop_config": {
            "until": until,
            "max_iterations": max_iterations,
            "loop_vars": loop_vars_val
        }
    })
}

// =============================================================================
// Tests
// =============================================================================

#[test]
fn test_loop_simple_counter() {
    // increment_counter: counter → new_counter = counter + 1
    // Loop until new_counter >= 5, starting from 0
    let inner_graph_name = "g.loop_g";
    let inner_op = {
        let inputs_obj: serde_json::Map<String, serde_json::Value> =
            vec![ref_input("counter", parent_ref(inner_graph_name, "new_counter"))]
                .into_iter()
                .collect();
        let outputs_obj: serde_json::Map<String, serde_json::Value> =
            vec![ref_output("new_counter", output_ref(inner_graph_name, "new_counter"))]
                .into_iter()
                .collect();
        json!({
            "type": "code",
            "name": "step",
            "full_name": format!("{}.step", inner_graph_name),
            "func_name": "increment_counter",
            "is_async": false,
            "enabled": true,
            "verbose": false,
            "stream": false,
            "bound": "cpu",
            "inputs": inputs_obj,
            "outputs": outputs_obj
        })
    };

    let nested = loop_graph_op(
        "loop_g",
        "g",
        inner_op,
        "step",
        vec![ref_input("new_counter", parent_ref("g", "new_counter"))],
        vec![],
        Some("new_counter >= 5"),
        100,
        vec!["new_counter"],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop_g": nested },
        "entries": ["loop_g"],
        "initial_ready_count": { "loop_g": 0 },
        "compiled_adj": { "loop_g": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_hush(config);
    let result = run(&engine, json!({"new_counter": 0}));
    assert_eq!(result["new_counter"], 5);
}

#[test]
fn test_loop_max_iterations() {
    // increment_counter: counter → new_counter = counter + 1
    // No until condition — should stop at max_iterations=3
    let inner_graph_name = "g.loop_g";
    let inner_op = {
        let inputs_obj: serde_json::Map<String, serde_json::Value> =
            vec![ref_input("counter", parent_ref(inner_graph_name, "new_counter"))]
                .into_iter()
                .collect();
        let outputs_obj: serde_json::Map<String, serde_json::Value> =
            vec![ref_output("new_counter", output_ref(inner_graph_name, "new_counter"))]
                .into_iter()
                .collect();
        json!({
            "type": "code",
            "name": "step",
            "full_name": format!("{}.step", inner_graph_name),
            "func_name": "increment_counter",
            "is_async": false,
            "enabled": true,
            "verbose": false,
            "stream": false,
            "bound": "cpu",
            "inputs": inputs_obj,
            "outputs": outputs_obj
        })
    };

    let nested = loop_graph_op(
        "loop_g",
        "g",
        inner_op,
        "step",
        vec![ref_input("new_counter", parent_ref("g", "new_counter"))],
        vec![],
        None,
        3,
        vec!["new_counter"],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "loop_g": nested },
        "entries": ["loop_g"],
        "initial_ready_count": { "loop_g": 0 },
        "compiled_adj": { "loop_g": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_hush(config);
    let result = run(&engine, json!({"new_counter": 0}));
    // After max 3 iterations: 0→1→2→3
    assert_eq!(result["new_counter"], 3);
}

#[test]
fn test_loop_fibonacci() {
    // fib_step: a, b → new_a = b, new_b = a + b
    // Loop until new_b >= 100
    let inner_graph_name = "g.fib";
    let inner_op = {
        let inputs_obj: serde_json::Map<String, serde_json::Value> = vec![
            ref_input("a", parent_ref(inner_graph_name, "new_a")),
            ref_input("b", parent_ref(inner_graph_name, "new_b")),
        ]
        .into_iter()
        .collect();
        let outputs_obj: serde_json::Map<String, serde_json::Value> = vec![
            ref_output("new_a", output_ref(inner_graph_name, "new_a")),
            ref_output("new_b", output_ref(inner_graph_name, "new_b")),
        ]
        .into_iter()
        .collect();
        json!({
            "type": "code",
            "name": "step",
            "full_name": format!("{}.step", inner_graph_name),
            "func_name": "fib_step",
            "is_async": false,
            "enabled": true,
            "verbose": false,
            "stream": false,
            "bound": "cpu",
            "inputs": inputs_obj,
            "outputs": outputs_obj
        })
    };

    let nested = loop_graph_op(
        "fib",
        "g",
        inner_op,
        "step",
        vec![
            ref_input("new_a", parent_ref("g", "new_a")),
            ref_input("new_b", parent_ref("g", "new_b")),
        ],
        vec![],
        Some("new_b >= 100"),
        50,
        vec!["new_a", "new_b"],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "fib": nested },
        "entries": ["fib"],
        "initial_ready_count": { "fib": 0 },
        "compiled_adj": { "fib": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_hush(config);
    let result = run(&engine, json!({"new_a": 0, "new_b": 1}));

    // Fibonacci: 0,1 → 1,1 → 1,2 → 2,3 → 3,5 → 5,8 → 8,13 → 13,21 → 21,34 → 34,55 → 55,89 → 89,144
    // Stops when new_b >= 100 → new_b = 144, new_a = 89
    assert_eq!(result["new_b"], 144);
    assert_eq!(result["new_a"], 89);
}

#[test]
fn test_loop_accumulator() {
    // accumulate: total, step_size → new_total = total + step_size
    // Loop until new_total >= 50, step_size = 10
    let inner_graph_name = "g.acc";
    let inner_op = {
        let inputs_obj: serde_json::Map<String, serde_json::Value> = vec![
            ref_input("total", parent_ref(inner_graph_name, "new_total")),
            ref_input("step_size", parent_ref(inner_graph_name, "step_size")),
        ]
        .into_iter()
        .collect();
        let outputs_obj: serde_json::Map<String, serde_json::Value> =
            vec![ref_output("new_total", output_ref(inner_graph_name, "new_total"))]
                .into_iter()
                .collect();
        json!({
            "type": "code",
            "name": "step",
            "full_name": format!("{}.step", inner_graph_name),
            "func_name": "accumulate",
            "is_async": false,
            "enabled": true,
            "verbose": false,
            "stream": false,
            "bound": "cpu",
            "inputs": inputs_obj,
            "outputs": outputs_obj
        })
    };

    let nested = loop_graph_op(
        "acc",
        "g",
        inner_op,
        "step",
        vec![
            ref_input("new_total", parent_ref("g", "new_total")),
            ref_input("step_size", parent_ref("g", "step_size")),
        ],
        vec![],
        Some("new_total >= 50"),
        100,
        vec!["new_total", "step_size"],
    );

    let config = json!({
        "name": "g",
        "full_name": "g",
        "ops": { "acc": nested },
        "entries": ["acc"],
        "initial_ready_count": { "acc": 0 },
        "compiled_adj": { "acc": [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    });

    let engine = make_hush(config);
    let result = run(&engine, json!({"new_total": 0, "step_size": 10}));

    // 0 + 10 = 10 → 20 → 30 → 40 → 50 (stops at 50)
    assert_eq!(result["new_total"], 50);
}
