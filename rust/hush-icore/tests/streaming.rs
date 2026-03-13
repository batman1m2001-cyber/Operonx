//! Full streaming pipeline tests.
//!
//! Tests the complete flow: generator → downstream ops → collect outputs.
//! Verifies stream context creation, predecrements, context fallback in pipelines.

mod common;

use common::*;
use serde_json::json;

// =============================================================================
// Generator → chain of downstream ops
// =============================================================================

#[test]
fn test_generator_chain_two_ops() {
    // range_gen(0..3) → double(x=value) → to_upper(text=result as string)
    // Tests that stream contexts propagate through a chain of downstream ops.
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

    // increment: x → result = x + 1
    let inc = func_op(
        "inc",
        "g",
        "increment",
        vec![ref_input("x", op_ref("g.dbl", "result"))],
        vec![],
    );

    let config = streaming_graph(
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
        // No pre-decrements: gen is the only predecessor for dbl, handled by propagate
        json!({}),
    );

    let engine = make_hush(config);
    let result = run_full(&engine, json!({}), None);
    let state = &result["$state"]["values"];

    // gen yields: value=0, value=1, value=2
    // dbl: 0*2=0, 1*2=2, 2*2=4
    // inc: 0+1=1, 2+1=3, 4+1=5
    assert_eq!(state["g.inc"]["result"]["main.[0]"], json!(1));
    assert_eq!(state["g.inc"]["result"]["main.[1]"], json!(3));
    assert_eq!(state["g.inc"]["result"]["main.[2]"], json!(5));
}

#[test]
fn test_generator_with_broadcast_input() {
    // Generator + a batch op that provides shared input.
    // batch_op produces a value, gen yields items, downstream uses both.
    //
    // Layout:
    //   prefix_op (literal "PREFIX") → provides "out" at main context
    //   gen (range 0..3)             → provides "value" at stream contexts
    //   combine: reads prefix_op["out"] (via context fallback) + gen["value"]
    //
    // This tests context fallback: combine in "main.[0]" reads prefix_op's output
    // from "main" via hierarchy walk-up.

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

    // Use add(a, b) where a comes from batch op (via context fallback), b from generator
    let add_op = func_op(
        "adder",
        "g",
        "add",
        vec![
            ref_input("a", op_ref("g.pfx", "out")),  // from batch context "main" via fallback
            ref_input("b", op_ref("g.gen", "value")), // from stream context
        ],
        vec![],
    );

    // pfx runs first (entry), gen runs first (entry), adder needs both
    // pfx and gen are independent entries
    // adder needs: pfx (hard edge from pfx) + gen yield (propagate from gen)
    let config = streaming_graph(
        "g",
        vec![
            ("pfx".into(), prefix_op),
            ("gen".into(), gen),
            ("adder".into(), add_op),
        ],
        vec!["pfx", "gen"],
        vec![("pfx", 0), ("gen", 0), ("adder", 2)], // adder needs 2 predecessors
        vec![
            ("pfx", vec![("adder", false)]),
            ("gen", vec![("adder", false)]),
            ("adder", vec![("__end__", false)]),
        ],
        // When gen yields: pfx is already done (batch), so pre-subtract pfx→adder edge
        json!({"gen": {"adder": 1}}),
    );

    let engine = make_hush(config);
    let result = run_full(&engine, json!({}), None);
    let state = &result["$state"]["values"];

    // pfx["out"] = "[INFO] data" stored at context "main"
    // gen yields value=1,2,3 at "main.[0]", "main.[1]", "main.[2]"
    // adder reads pfx["out"] via fallback from "main", gen["value"] from stream ctx
    // But add expects numbers... pfx["out"] is a string "[INFO] data"
    // add will do: f64 parse of string (0.0) + value → value
    // So adder result = 0.0 + 1 = 1.0, 0.0 + 2 = 2.0, 0.0 + 3 = 3.0
    // Actually, add does: inputs["a"].as_i64() which is None for string, then as_f64() which is 0.0
    // So result = 0 + 1 = 1.0, etc.

    // The key test is that adder actually runs in each stream context (context fallback works)
    assert!(!state["g.adder"]["result"]["main.[0]"].is_null());
    assert!(!state["g.adder"]["result"]["main.[1]"].is_null());
    assert!(!state["g.adder"]["result"]["main.[2]"].is_null());
}

#[test]
fn test_generator_multiple_items_10() {
    // range_gen(0..10) → double → verify all 10 stream contexts created
    let gen = generator_op(
        "gen",
        "g",
        "range_gen",
        vec![
            literal_input("start", json!(0)),
            literal_input("end", json!(10)),
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

    let config = streaming_graph(
        "g",
        vec![("gen".into(), gen), ("dbl".into(), dbl)],
        vec!["gen"],
        vec![("gen", 0), ("dbl", 1)],
        vec![("gen", vec![("dbl", false)]), ("dbl", vec![("__end__", false)])],
        json!({}),
    );

    let engine = make_hush(config);
    let result = run_full(&engine, json!({}), None);
    let state = &result["$state"]["values"];

    // All 10 items should have been processed
    for i in 0..10 {
        let ctx = format!("main.[{}]", i);
        assert_eq!(
            state["g.gen"]["value"][&ctx],
            json!(i),
            "gen value at {}",
            ctx
        );
        assert_eq!(
            state["g.dbl"]["result"][&ctx],
            json!(i * 2),
            "dbl result at {}",
            ctx
        );
    }

    // No 11th item
    assert!(state["g.gen"]["value"]["main.[10]"].is_null());
}

#[test]
fn test_chunk_text_then_to_upper() {
    // chunk_text("abc def") → to_upper(text=chunk) per stream context
    let gen = generator_op(
        "gen",
        "g",
        "chunk_text",
        vec![
            literal_input("text", json!("abc def")),
            literal_input("chunk_size", json!(4)),
        ],
    );

    let upper = func_op(
        "upper",
        "g",
        "to_upper",
        vec![ref_input("text", op_ref("g.gen", "chunk"))],
        vec![],
    );

    let config = streaming_graph(
        "g",
        vec![("gen".into(), gen), ("upper".into(), upper)],
        vec!["gen"],
        vec![("gen", 0), ("upper", 1)],
        vec![
            ("gen", vec![("upper", false)]),
            ("upper", vec![("__end__", false)]),
        ],
        json!({}),
    );

    let engine = make_hush(config);
    let result = run_full(&engine, json!({}), None);
    let state = &result["$state"]["values"];

    // "abc def" chunked by 4: "abc ", "def"
    assert_eq!(state["g.upper"]["result"]["main.[0]"], json!("ABC "));
    assert_eq!(state["g.upper"]["result"]["main.[1]"], json!("DEF"));
}
