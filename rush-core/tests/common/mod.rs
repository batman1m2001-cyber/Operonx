//! Shared test helpers for rush-core integration tests.

use rush_core::engine::Rush;
use serde_json::{json, Value};

/// Create a Rush engine from a JSON Value config.
pub fn make_rush(config: Value) -> Rush {
    Rush::new(&serde_json::to_string(&config).unwrap()).unwrap()
}

/// Run and return result, stripping $-prefixed internal keys.
pub fn run(rush: &Rush, inputs: Value) -> Value {
    let result = rush.run_json(inputs, None, None, None).unwrap();
    filter_internal(result)
}

/// Run with a request_id and return the full result (including $state).
pub fn run_full(rush: &Rush, inputs: Value, request_id: Option<String>) -> Value {
    rush.run_json(inputs, request_id, None, None).unwrap()
}

/// Remove $-prefixed keys from a JSON object.
pub fn filter_internal(mut val: Value) -> Value {
    if let Some(obj) = val.as_object_mut() {
        obj.retain(|k, _| !k.starts_with('$'));
    }
    val
}

// =============================================================================
// Config builders — construct graph JSON configs directly
// =============================================================================

/// Build a ref config: `{"source": source, "var": var, "transforms": [...], "is_output": is_output}`
pub fn ref_config(source: &str, var: &str, transforms: Vec<Value>, is_output: bool) -> Value {
    json!({
        "source": source,
        "var": var,
        "transforms": transforms,
        "is_output": is_output
    })
}

/// Build a PARENT ref: `{"source": graph_name, "var": key, "transforms": [], "is_output": false}`
///
/// Matches Python's `PARENT["key"]` serialization: the `var` field IS the key,
/// no `getitem` op needed (state lookup by `(source, var, context)` is direct).
pub fn parent_ref(graph_full_name: &str, key: &str) -> Value {
    ref_config(graph_full_name, key, vec![], false)
}

/// Build a sibling op ref: `{"source": op_full_name, "var": output_key, "transforms": [], ...}`
///
/// Matches Python's `op["key"]` serialization.
pub fn op_ref(op_full_name: &str, key: &str) -> Value {
    ref_config(op_full_name, key, vec![], false)
}

/// Build an output ref (is_output=true): used for graph outputs and output mappings.
pub fn output_ref(source: &str, key: &str) -> Value {
    ref_config(source, key, vec![], true)
}

/// Build an input param with a ref.
pub fn ref_input(var_name: &str, ref_val: Value) -> (String, Value) {
    (
        var_name.to_string(),
        json!({
            "ref": ref_val,
            "literal": null,
            "default": null,
            "required": false
        }),
    )
}

/// Build an input param with a literal value.
pub fn literal_input(var_name: &str, value: Value) -> (String, Value) {
    (
        var_name.to_string(),
        json!({
            "ref": null,
            "literal": value,
            "default": null,
            "required": false
        }),
    )
}

/// Build an output param with a ref (for output mapping).
pub fn ref_output(var_name: &str, ref_val: Value) -> (String, Value) {
    (
        var_name.to_string(),
        json!({
            "ref": ref_val,
            "literal": null,
            "default": null,
            "required": false
        }),
    )
}

/// Build a simple func op config.
pub fn func_op(
    name: &str,
    graph_name: &str,
    rust_op: &str,
    inputs: Vec<(String, Value)>,
    outputs: Vec<(String, Value)>,
) -> Value {
    let inputs_obj: serde_json::Map<String, Value> = inputs.into_iter().collect();
    let outputs_obj: serde_json::Map<String, Value> = outputs.into_iter().collect();
    json!({
        "type": "func",
        "name": name,
        "full_name": format!("{}.{}", graph_name, name),
        "rust_op": rust_op,
        "is_async": false,
        "enabled": true,
        "verbose": false,
        "stream": false,
        "bound": "cpu",
        "inputs": inputs_obj,
        "outputs": outputs_obj
    })
}

/// Build a single-op graph config: one func op with PARENT input, auto-forward to END.
pub fn single_op_graph(
    graph_name: &str,
    op_name: &str,
    rust_op_path: &str,
    inputs: Vec<(String, Value)>,
    outputs: Vec<(String, Value)>,
) -> Value {
    let op = func_op(op_name, graph_name, rust_op_path, inputs, outputs);
    json!({
        "name": graph_name,
        "full_name": graph_name,
        "ops": { op_name: op },
        "entries": [op_name],
        "initial_ready_count": { op_name: 0 },
        "compiled_adj": { op_name: [["__end__", false]] },
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    })
}

/// Build a linear chain graph: op1 -> op2 -> ... -> END.
pub fn chain_graph(
    graph_name: &str,
    ops: Vec<(String, Value)>,
) -> Value {
    let mut ops_map = serde_json::Map::new();
    let mut ready_count = serde_json::Map::new();
    let mut adj = serde_json::Map::new();

    let entries = vec![ops[0].0.clone()];

    for (i, (name, op_val)) in ops.iter().enumerate() {
        ops_map.insert(name.clone(), op_val.clone());

        // First op has ready_count=0, rest have ready_count=1
        ready_count.insert(
            name.clone(),
            json!(if i == 0 { 0 } else { 1 }),
        );

        // Each op points to the next, last points to __end__
        let target = if i + 1 < ops.len() {
            ops[i + 1].0.clone()
        } else {
            "__end__".to_string()
        };
        adj.insert(name.clone(), json!([[target, false]]));
    }

    json!({
        "name": graph_name,
        "full_name": graph_name,
        "ops": ops_map,
        "entries": entries,
        "initial_ready_count": ready_count,
        "compiled_adj": adj,
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {}
    })
}
