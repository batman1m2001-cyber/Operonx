//! Shared test helpers for rush-core integration tests.

use std::sync::Arc;

use rush_core::engine::Rush;
use rush_core::registry::OpRegistry;
use serde_json::{json, Value};

#[path = "../test_ops.rs"]
mod test_ops;

/// Test op registry — dispatches op names to test op functions.
/// Replaces builtin_ops for tests.
pub struct TestRegistry;

impl OpRegistry for TestRegistry {
    fn call(&self, name: &str, inputs: &Value) -> Option<Value> {
        let result = match name {
            "double" => test_ops::double(inputs),
            "add" => test_ops::add(inputs),
            "hash_chain" => test_ops::hash_chain(inputs),
            "identity" => test_ops::identity(inputs),
            "multiply" => test_ops::multiply(inputs),
            "square" => test_ops::square(inputs),
            "increment" => test_ops::increment(inputs),
            "greet" => test_ops::greet(inputs),
            "greet_vi" => test_ops::greet_vi(inputs),
            "make_dict" => test_ops::make_dict(inputs),
            "to_upper" => test_ops::to_upper(inputs),
            "join_strings" => test_ops::join_strings(inputs),
            "dbl" => test_ops::dbl(inputs),
            "ok_op" => test_ops::ok_op(inputs),
            "make_list" => test_ops::make_list(inputs),
            "increment_counter" => test_ops::increment_counter(inputs),
            "accumulate" => test_ops::accumulate(inputs),
            "fib_step" => test_ops::fib_step(inputs),
            "tagged_op" => test_ops::tagged_op(inputs),
            "safe_op" => test_ops::safe_op(inputs),
            "multiply_values" => test_ops::multiply_values(inputs),
            "noop" => test_ops::noop(inputs),
            "passthrough" => test_ops::passthrough(inputs),
            "fail_op" => test_ops::fail_op(inputs),
            "string_concat" => test_ops::string_concat(inputs),
            "string_split" => test_ops::string_split(inputs),
            "string_template" => test_ops::string_template(inputs),
            "json_parse" => test_ops::json_parse(inputs),
            "json_extract" => test_ops::json_extract(inputs),
            "json_merge" => test_ops::json_merge(inputs),
            "math_sum" => test_ops::math_sum(inputs),
            "math_mean" => test_ops::math_mean(inputs),
            "math_max" => test_ops::math_max(inputs),
            "math_min" => test_ops::math_min(inputs),
            "bench_noop" => test_ops::bench_noop(inputs),
            "classify" => test_ops::classify(inputs),
            "process_grade" => test_ops::process_grade(inputs),
            "aggregate" => test_ops::aggregate(inputs),
            "bench_transform" => test_ops::bench_transform(inputs),
            "merge_two" => test_ops::merge_two(inputs),
            "combine_all" => test_ops::combine_all(inputs),
            "cpu_hash_chain" => test_ops::cpu_hash_chain(inputs),
            "cpu_prime_sieve" => test_ops::cpu_prime_sieve(inputs),
            "cpu_matrix_mult" => test_ops::cpu_matrix_mult(inputs),
            "cpu_fibonacci" => test_ops::cpu_fibonacci(inputs),
            "grade_a" => test_ops::grade_a(inputs),
            "grade_b" => test_ops::grade_b(inputs),
            "grade_f" => test_ops::grade_f(inputs),
            "grade_a_mult" => test_ops::grade_a_mult(inputs),
            "grade_f_mult" => test_ops::grade_f_mult(inputs),
            "prefix" => test_ops::prefix(inputs),
            "sum_all" => test_ops::sum_all(inputs),
            "classify_value" => test_ops::classify_value(inputs),
            "multi_output" => test_ops::multi_output(inputs),
            "multi" => test_ops::multi(inputs),
            "compute" => test_ops::compute(inputs),
            "process_list" => test_ops::process_list(inputs),
            "measure" => test_ops::measure(inputs),
            "append_char" => test_ops::append_char(inputs),
            "collect_op" => test_ops::collect_op(inputs),
            "make_start" => test_ops::make_start(inputs),
            "format_grade" => test_ops::format_grade(inputs),
            "multiply_vf" => test_ops::multiply_vf(inputs),
            "accumulate_step" => test_ops::accumulate_step(inputs),
            "raise_op" => test_ops::raise_op(inputs),
            "maybe_fail" => test_ops::maybe_fail(inputs),
            "process_item_text" => test_ops::process_item_text(inputs),
            "square_named" => test_ops::square_named(inputs),
            "excellent" => test_ops::excellent(inputs),
            "good" => test_ops::good(inputs),
            "average_grade" => test_ops::average_grade(inputs),
            "fail_grade" => test_ops::fail_grade(inputs),
            "analyze_text" => test_ops::analyze_text(inputs),
            "classify_by_count" => test_ops::classify_by_count(inputs),
            "step_a" => test_ops::step_a(inputs),
            "step_b" => test_ops::step_b(inputs),
            "fetch_data" => test_ops::fetch_data(inputs),
            "transform_double" => test_ops::transform_double(inputs),
            "aggregate_data" => test_ops::aggregate_data(inputs),
            "clean_text" => test_ops::clean_text(inputs),
            "count_words" => test_ops::count_words(inputs),
            "summarize_stats" => test_ops::summarize_stats(inputs),
            _ => return None,
        };
        Some(result)
    }

    fn call_generator(&self, name: &str, inputs: &Value) -> Option<Vec<Value>> {
        let result = match name {
            "chunk_text" => test_ops::chunk_text(inputs),
            "range_gen" => test_ops::range_gen(inputs),
            "each_item_with_prefix" => test_ops::each_item_with_prefix(inputs),
            "each_number" => test_ops::each_number(inputs),
            "halve_until" => test_ops::halve_until(inputs),
            _ => return None,
        };
        Some(result)
    }
}

/// Create a Rush engine from a JSON Value config, with TestRegistry.
pub fn make_rush(config: Value) -> Rush {
    let mut engine = Rush::new(&serde_json::to_string(&config).unwrap()).unwrap();
    engine.set_registry(Arc::new(TestRegistry));
    engine
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

/// Build a generator op config (is_generator = true).
pub fn generator_op(
    name: &str,
    graph_name: &str,
    rust_op: &str,
    inputs: Vec<(String, Value)>,
) -> Value {
    let inputs_obj: serde_json::Map<String, Value> = inputs.into_iter().collect();
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
        "is_generator": true,
        "inputs": inputs_obj,
        "outputs": {}
    })
}

/// Build a graph config with stream_predecrements (for generator → downstream).
pub fn streaming_graph(
    graph_name: &str,
    ops_list: Vec<(String, Value)>,
    entries: Vec<&str>,
    ready_count: Vec<(&str, i32)>,
    adj: Vec<(&str, Vec<(&str, bool)>)>,
    stream_predecrements: Value,
) -> Value {
    let mut ops_map = serde_json::Map::new();
    for (name, op_val) in &ops_list {
        ops_map.insert(name.clone(), op_val.clone());
    }

    let rc: serde_json::Map<String, Value> = ready_count
        .into_iter()
        .map(|(k, v)| (k.to_string(), json!(v)))
        .collect();

    let adj_map: serde_json::Map<String, Value> = adj
        .into_iter()
        .map(|(from, targets)| {
            let targets_val: Vec<Value> = targets
                .into_iter()
                .map(|(t, soft)| json!([t, soft]))
                .collect();
            (from.to_string(), json!(targets_val))
        })
        .collect();

    let entries_val: Vec<Value> = entries.into_iter().map(|e| json!(e)).collect();

    json!({
        "name": graph_name,
        "full_name": graph_name,
        "ops": ops_map,
        "entries": entries_val,
        "initial_ready_count": rc,
        "compiled_adj": adj_map,
        "has_soft_preds": [],
        "inputs": {},
        "outputs": {},
        "stream_predecrements": stream_predecrements
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
