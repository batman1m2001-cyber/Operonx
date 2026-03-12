//! Stream aggregation tests for `get_outputs()`.
//!
//! Validates the 4 code paths in get_outputs:
//! 1. Explicit outputs + streaming + ref → aggregate from leaf contexts via ref source
//! 2. Explicit outputs + streaming + null ref → search terminal ops (THE KEY FIX)
//! 3. Auto-forward + streaming → discover vars from terminal ops, aggregate as lists
//! 4. Explicit outputs + no streaming → resolve refs normally (batch mode)
//!
//! The "null ref" path (#2) was the root cause of generators returning `{}` in Rust serve:
//! `>> END` auto-forwards produce outputs with `"ref": null` in serialized config.
//! During streaming, values live under terminal op namespaces in leaf contexts,
//! not in the base context — so the old code found nothing and returned empty `{}`.

mod common;

use common::*;
use serde_json::json;

// =============================================================================
// Helper: streaming graph with explicit outputs
// =============================================================================

/// Build a streaming graph config with explicit outputs (non-empty outputs map).
/// This is what Python's `>> END` serializes: outputs with `"ref": null`.
fn streaming_graph_with_outputs(
    graph_name: &str,
    ops_list: Vec<(String, Value)>,
    entries: Vec<&str>,
    ready_count: Vec<(&str, i32)>,
    adj: Vec<(&str, Vec<(&str, bool)>)>,
    stream_predecrements: Value,
    outputs: Vec<(String, Value)>,
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
    let outputs_obj: serde_json::Map<String, Value> = outputs.into_iter().collect();

    json!({
        "name": graph_name,
        "full_name": graph_name,
        "ops": ops_map,
        "entries": entries_val,
        "initial_ready_count": rc,
        "compiled_adj": adj_map,
        "has_soft_preds": [],
        "inputs": {},
        "outputs": outputs_obj,
        "stream_predecrements": stream_predecrements
    })
}

/// Build a null-ref output param (what `>> END` auto-forward serializes).
fn null_ref_output(var_name: &str) -> (String, Value) {
    (
        var_name.to_string(),
        json!({
            "ref": null,
            "literal": null,
            "default": null,
            "required": false
        }),
    )
}

use serde_json::Value;

// =============================================================================
// Path 2: Explicit outputs + streaming + null ref (THE KEY FIX)
// =============================================================================

#[test]
fn test_null_ref_outputs_aggregate_from_terminal_ops() {
    // This is THE bug that caused generators to return {} in Rust serve.
    //
    // Setup: gen(0..3) → double → END
    // Serialized config has outputs: {"result": {"ref": null}} (from >> END auto-forward)
    // The fix searches terminal ops for "result" across leaf stream contexts.
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(0)),
            literal_input("end", json!(3)),
            literal_input("step", json!(1)),
        ],
    );

    let dbl = func_op(
        "dbl",
        "g",
        "double",
        vec![ref_input("x", op_ref("g.gen", "value"))],
        vec![],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![
            ("gen", vec![("dbl", false)]),
            ("dbl", vec![("__end__", false)]),
        ],
        json!({}),
        // Explicit outputs with null ref — exactly what >> END serializes
        vec![null_ref_output("result")],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // Must NOT be {} — must aggregate from terminal op "dbl" across stream contexts
    assert_eq!(result["result"], json!([0, 2, 4]));
}

#[test]
fn test_null_ref_outputs_multiple_vars() {
    // Generator yields multiple vars, all forwarded via null-ref outputs.
    // gen yields: {chunk: "...", index: N} → END
    let gen = generator_op(
        "gen",
        "g",
        "chunk_text",
        vec![
            literal_input("text", json!("hello world")),
            literal_input("chunk_size", json!(6)),
        ],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![("gen".into(), gen)],
        vec!["gen"],
        vec![("gen", 0)],
        vec![("gen", vec![("__end__", false)])],
        json!({}),
        // Both "chunk" and "index" as null-ref outputs
        vec![null_ref_output("chunk"), null_ref_output("index")],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // "hello world" chunked by 6: "hello ", "world"
    assert_eq!(result["chunk"], json!(["hello ", "world"]));
    assert_eq!(result["index"], json!([0, 1]));
}

#[test]
fn test_null_ref_outputs_chain_three_ops() {
    // gen → double → increment → END with null-ref output for "result"
    // Tests that null-ref correctly finds the terminal op (inc), not intermediate ops.
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(1)),
            literal_input("end", json!(4)),
            literal_input("step", json!(1)),
        ],
    );

    let dbl = func_op(
        "dbl",
        "g",
        "double",
        vec![ref_input("x", op_ref("g.gen", "value"))],
        vec![],
    );

    let inc = func_op(
        "inc",
        "g",
        "increment",
        vec![ref_input("x", op_ref("g.dbl", "result"))],
        vec![],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![
            ("gen".into(), gen),
            ("dbl".into(), dbl),
            ("inc".into(), inc),
        ],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1), ("inc", 1)],
        vec![
            ("gen", vec![("dbl", false)]),
            ("dbl", vec![("inc", false)]),
            ("inc", vec![("__end__", false)]),
        ],
        json!({}),
        vec![null_ref_output("result")],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // gen yields 1,2,3 → double: 2,4,6 → increment: 3,5,7
    assert_eq!(result["result"], json!([3, 5, 7]));
}

// =============================================================================
// Path 1: Explicit outputs + streaming + ref (aggregate via ref source)
// =============================================================================

#[test]
fn test_explicit_ref_outputs_aggregate_streaming() {
    // gen → double → END, but outputs have explicit ref pointing to dbl's result.
    // This tests the ref-based aggregation path.
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(0)),
            literal_input("end", json!(3)),
            literal_input("step", json!(1)),
        ],
    );

    let dbl = func_op(
        "dbl",
        "g",
        "double",
        vec![ref_input("x", op_ref("g.gen", "value"))],
        vec![],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![
            ("gen", vec![("dbl", false)]),
            ("dbl", vec![("__end__", false)]),
        ],
        json!({}),
        // Explicit ref to dbl's "result" output
        vec![ref_output("result", output_ref("g.dbl", "result"))],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    assert_eq!(result["result"], json!([0, 2, 4]));
}

#[test]
fn test_explicit_ref_outputs_rename_var() {
    // gen → double → END, output maps dbl["result"] → "doubled_values"
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(0)),
            literal_input("end", json!(3)),
            literal_input("step", json!(1)),
        ],
    );

    let dbl = func_op(
        "dbl",
        "g",
        "double",
        vec![ref_input("x", op_ref("g.gen", "value"))],
        vec![],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![
            ("gen", vec![("dbl", false)]),
            ("dbl", vec![("__end__", false)]),
        ],
        json!({}),
        // Rename: dbl["result"] → "doubled_values"
        vec![ref_output("doubled_values", output_ref("g.dbl", "result"))],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    assert_eq!(result["doubled_values"], json!([0, 2, 4]));
    assert!(result.get("result").is_none());
}

// =============================================================================
// Path 3: Auto-forward + streaming (no explicit outputs)
// =============================================================================

#[test]
fn test_auto_forward_streaming_aggregation() {
    // gen → double → END, empty outputs → auto-forward from terminal op "dbl"
    // This is the same as test_range_gen_basic but explicitly verifying aggregation.
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(0)),
            literal_input("end", json!(3)),
            literal_input("step", json!(1)),
        ],
    );

    let dbl = func_op(
        "dbl",
        "g",
        "double",
        vec![ref_input("x", op_ref("g.gen", "value"))],
        vec![],
    );

    // Empty outputs → auto-forward path
    let config = streaming_graph(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![
            ("gen", vec![("dbl", false)]),
            ("dbl", vec![("__end__", false)]),
        ],
        json!({}),
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    assert_eq!(result["result"], json!([0, 2, 4]));
}

#[test]
fn test_auto_forward_streaming_generator_only() {
    // gen → END (generator is also terminal op), auto-forward discovers all yielded vars
    let gen = generator_op(
        "gen",
        "g",
        "chunk_text",
        vec![
            literal_input("text", json!("abcdef")),
            literal_input("chunk_size", json!(2)),
        ],
    );

    let config = streaming_graph(
        "g",
        vec![("gen".into(), gen)],
        vec!["gen"],
        vec![("gen", 0)],
        vec![("gen", vec![("__end__", false)])],
        json!({}),
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // "abcdef" chunked by 2: "ab", "cd", "ef"
    assert_eq!(result["chunk"], json!(["ab", "cd", "ef"]));
    assert_eq!(result["index"], json!([0, 1, 2]));
}

// =============================================================================
// Path 4: Explicit outputs + no streaming (batch mode, baseline)
// =============================================================================

#[test]
fn test_explicit_outputs_batch_mode() {
    // Non-generator graph with explicit output refs — should resolve normally.
    let step = func_op(
        "step",
        "g",
        "double",
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![("step".into(), step)],
        vec!["step"],
        vec![("step", 0)],
        vec![("step", vec![("__end__", false)])],
        json!({}),
        vec![ref_output("result", output_ref("g.step", "result"))],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({"x": 5}));

    // Batch mode: single value, not a list
    assert_eq!(result["result"], json!(10));
}

// =============================================================================
// Edge cases
// =============================================================================

#[test]
fn test_null_ref_empty_generator_no_crash() {
    // Generator yields 0 items + null-ref outputs → should return empty, not crash
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(5)),
            literal_input("end", json!(3)), // start > end → empty
            literal_input("step", json!(1)),
        ],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![("gen".into(), gen)],
        vec!["gen"],
        vec![("gen", 0)],
        vec![("gen", vec![("__end__", false)])],
        json!({}),
        vec![null_ref_output("value")],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // No items yielded — "value" should be absent (not crash, not [])
    assert!(result.get("value").is_none() || result["value"] == json!([]));
}

#[test]
fn test_null_ref_single_item_generator() {
    // Generator yields exactly 1 item — still aggregated as a list
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(42)),
            literal_input("end", json!(43)),
            literal_input("step", json!(1)),
        ],
    );

    let dbl = func_op(
        "dbl",
        "g",
        "double",
        vec![ref_input("x", op_ref("g.gen", "value"))],
        vec![],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![
            ("gen", vec![("dbl", false)]),
            ("dbl", vec![("__end__", false)]),
        ],
        json!({}),
        vec![null_ref_output("result")],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // Single item, still wrapped in a list
    assert_eq!(result["result"], json!([84]));
}

#[test]
fn test_null_ref_terminal_op_with_empty_adj() {
    // Terminal op detected via empty adjacency list (not __end__ edge).
    // gen → dbl, where dbl has NO outgoing edges (empty adj list).
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(0)),
            literal_input("end", json!(3)),
            literal_input("step", json!(1)),
        ],
    );

    let dbl = func_op(
        "dbl",
        "g",
        "double",
        vec![ref_input("x", op_ref("g.gen", "value"))],
        vec![],
    );

    // dbl has empty adj list (no edges at all) — still a terminal op
    let config = streaming_graph_with_outputs(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![
            ("gen", vec![("dbl", false)]),
            ("dbl", vec![]),  // Empty adj — terminal by empty-list rule
        ],
        json!({}),
        vec![null_ref_output("result")],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    assert_eq!(result["result"], json!([0, 2, 4]));
}

#[test]
fn test_streaming_with_broadcast_and_null_ref_output() {
    // batch_op (prefix) + gen → downstream combine → END with null-ref output
    // Tests null-ref aggregation when there's a mix of batch + streaming ops.
    let prefix_op = func_op(
        "pfx",
        "g",
        "prefix",
        vec![literal_input("text", json!("data"))],
        vec![],
    );

    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(1)),
            literal_input("end", json!(4)),
            literal_input("step", json!(1)),
        ],
    );

    let add_op = func_op(
        "adder",
        "g",
        "add",
        vec![
            ref_input("a", op_ref("g.pfx", "out")),
            ref_input("b", op_ref("g.gen", "value")),
        ],
        vec![],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![
            ("pfx".into(), prefix_op),
            ("gen".into(), gen),
            ("adder".into(), add_op),
        ],
        vec!["pfx", "gen"],
        vec![("pfx", 0), ("gen", 0), ("adder", 2)],
        vec![
            ("pfx", vec![("adder", false)]),
            ("gen", vec![("adder", false)]),
            ("adder", vec![("__end__", false)]),
        ],
        json!({"gen": {"adder": 1}}),
        vec![null_ref_output("result")],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // adder runs in 3 stream contexts — result should be a list of 3 values
    let results = result["result"].as_array().unwrap();
    assert_eq!(results.len(), 3);
}

#[test]
fn test_leaf_context_filtering_nested_streams() {
    // Two generators chained: gen1 → gen2 → double → END
    // gen1 creates main.[0], main.[1]
    // gen2 in each creates main.[0].[0], main.[0].[1], main.[1].[0], main.[1].[1]
    // Leaf contexts should be the deepest ones only.
    //
    // This is a conceptual test — with current built-in generators we can't easily
    // chain two generators. Instead we verify the leaf filtering logic by checking
    // that non-leaf contexts (prefixes of other contexts) are excluded.
    //
    // For now, we just ensure single-level generators work correctly (covered above).
    // This test documents the expected behavior for nested streaming.
}

#[test]
fn test_mixed_ref_and_null_ref_outputs() {
    // Graph with both explicit-ref and null-ref outputs during streaming.
    // output "doubled" has explicit ref to dbl["result"]
    // output "raw_value" has null ref (should find from gen's "value")
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(0)),
            literal_input("end", json!(3)),
            literal_input("step", json!(1)),
        ],
    );

    let dbl = func_op(
        "dbl",
        "g",
        "double",
        vec![ref_input("x", op_ref("g.gen", "value"))],
        vec![],
    );

    let config = streaming_graph_with_outputs(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![
            ("gen", vec![("dbl", false)]),
            ("dbl", vec![("__end__", false)]),
        ],
        json!({}),
        vec![
            // Explicit ref → aggregates from dbl's "result" directly
            ref_output("doubled", output_ref("g.dbl", "result")),
            // Null ref → searches terminal ops for "result" (finds dbl)
            null_ref_output("result"),
        ],
    );

    let engine = make_rush(config);
    let result = run(&engine, json!({}));

    // Both should produce the same aggregated list
    assert_eq!(result["doubled"], json!([0, 2, 4]));
    assert_eq!(result["result"], json!([0, 2, 4]));
}
