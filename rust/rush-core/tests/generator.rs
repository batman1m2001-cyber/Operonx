//! Generator op tests — built-in generators with the streaming scheduler.
//!
//! Tests chunk_text and range_gen generators, verifying that:
//! - Items are yielded into stream contexts (main.[0], main.[1], ...)
//! - Downstream ops execute per stream context
//! - Results are collected from all stream contexts

mod common;

use common::*;
use serde_json::json;

// =============================================================================
// Simple generator: range_gen → downstream double
// =============================================================================

#[test]
fn test_range_gen_basic() {
    // range_gen yields: {value: 0}, {value: 1}, {value: 2}
    // downstream: double(x=gen["value"]) → {result: value*2} per stream context
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

    // stream_predecrements is empty: dbl's only predecessor IS the generator.
    // Pre-decrements are for OTHER batch predecessors already done, not the gen edge itself.
    let config = streaming_graph(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![("gen", vec![("dbl", false)]), ("dbl", vec![("__end__", false)])],
        json!({}),
    );

    let engine = make_rush(config);
    let result = run_full(&engine, json!({}), None);

    // The result should contain outputs from all stream contexts.
    // Auto-forward collects from terminal op "dbl" across all contexts.
    // With the current get_outputs, it may only see the base context.
    // Let's check the $state for stream context values.
    let state = &result["$state"]["values"];

    // Generator should store values in stream contexts
    assert_eq!(state["g.gen"]["value"]["main.[0]"], json!(0));
    assert_eq!(state["g.gen"]["value"]["main.[1]"], json!(1));
    assert_eq!(state["g.gen"]["value"]["main.[2]"], json!(2));

    // Downstream double should have results in stream contexts
    assert_eq!(state["g.dbl"]["result"]["main.[0]"], json!(0));
    assert_eq!(state["g.dbl"]["result"]["main.[1]"], json!(2));
    assert_eq!(state["g.dbl"]["result"]["main.[2]"], json!(4));

    // Verify stream aggregation in top-level output
    let output = filter_internal(result);
    assert_eq!(output["result"], json!([0, 2, 4]));
}

#[test]
fn test_chunk_text_basic() {
    // chunk_text yields: {chunk: "hel", index: 0}, {chunk: "lo ", index: 1}, {chunk: "wor", index: 2}, ...
    let gen = generator_op(
        "gen",
        "g",
        "chunk_text",
        vec![
            literal_input("text", json!("hello world")),
            literal_input("chunk_size", json!(3)),
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
    let result = run_full(&engine, json!({}), None);
    let state = &result["$state"]["values"];

    // "hello world" chunked by 3: "hel", "lo ", "wor", "ld"
    assert_eq!(state["g.gen"]["chunk"]["main.[0]"], json!("hel"));
    assert_eq!(state["g.gen"]["chunk"]["main.[1]"], json!("lo "));
    assert_eq!(state["g.gen"]["chunk"]["main.[2]"], json!("wor"));
    assert_eq!(state["g.gen"]["chunk"]["main.[3]"], json!("ld"));

    assert_eq!(state["g.gen"]["index"]["main.[0]"], json!(0));
    assert_eq!(state["g.gen"]["index"]["main.[3]"], json!(3));
}

#[test]
fn test_range_gen_empty() {
    // range_gen with start >= end → no items yielded
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(5)),
            literal_input("end", json!(3)),
            literal_input("step", json!(1)),
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
    // Should complete without error even with no items
    let _result = run(&engine, json!({}));
}

#[test]
fn test_range_gen_with_step() {
    // range_gen with step=2: 0, 2, 4
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(0)),
            literal_input("end", json!(5)),
            literal_input("step", json!(2)),
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
    let result = run_full(&engine, json!({}), None);
    let state = &result["$state"]["values"];

    assert_eq!(state["g.gen"]["value"]["main.[0]"], json!(0));
    assert_eq!(state["g.gen"]["value"]["main.[1]"], json!(2));
    assert_eq!(state["g.gen"]["value"]["main.[2]"], json!(4));

    // Should only have 3 items (0, 2, 4)
    assert!(state["g.gen"]["value"]["main.[3]"].is_null());
}

#[test]
fn test_range_gen_from_parent_input() {
    // range_gen reading start/end from PARENT inputs
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            ref_input("start", parent_ref("g", "start")),
            ref_input("end", parent_ref("g", "end")),
            literal_input("step", json!(1)),
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
    let result = run_full(&engine, json!({"start": 10, "end": 13}), None);
    let state = &result["$state"]["values"];

    assert_eq!(state["g.gen"]["value"]["main.[0]"], json!(10));
    assert_eq!(state["g.gen"]["value"]["main.[1]"], json!(11));
    assert_eq!(state["g.gen"]["value"]["main.[2]"], json!(12));
}
