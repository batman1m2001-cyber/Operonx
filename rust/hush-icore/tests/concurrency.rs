//! Concurrent execution tests — state isolation and correctness under load.
//!
//! Converted from test_concurrency.py. Uses Arc<Hush> + std::thread::spawn
//! since Hush::run_json(&self) creates a fresh EngineState per call.

mod common;

use common::*;
use serde_json::json;
use std::sync::Arc;

// =============================================================================
// Test 1: Concurrent correctness — 30 parallel runs
// =============================================================================

#[test]
fn test_concurrent_correctness_30_runs() {
    let config = single_op_graph(
        "g",
        "d",
        "double",
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = Arc::new(make_hush(config));

    let handles: Vec<_> = (0..30)
        .map(|i| {
            let engine = Arc::clone(&engine);
            std::thread::spawn(move || {
                let result = run(&engine, json!({"x": i}));
                (i, result)
            })
        })
        .collect();

    let mut results: Vec<(i64, i64)> = handles
        .into_iter()
        .map(|h| {
            let (i, r) = h.join().unwrap();
            (i, r["result"].as_i64().unwrap())
        })
        .collect();
    results.sort_by_key(|&(i, _)| i);

    for (i, val) in results {
        assert_eq!(val, i * 2, "double({}) should be {}", i, i * 2);
    }
}

// =============================================================================
// Test 2: 100 concurrent runs — state isolation
// =============================================================================

#[test]
fn test_100_concurrent_runs_isolated() {
    let config = single_op_graph(
        "g",
        "d",
        "double",
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let engine = Arc::new(make_hush(config));

    let handles: Vec<_> = (0..100)
        .map(|i| {
            let engine = Arc::clone(&engine);
            std::thread::spawn(move || run(&engine, json!({"x": i})))
        })
        .collect();

    let mut seen = std::collections::HashMap::new();
    for h in handles {
        let result = h.join().unwrap();
        let val = result["result"].as_i64().unwrap();
        *seen.entry(val).or_insert(0) += 1;
    }

    // Every even number 0,2,4,...,198 should appear exactly once
    for i in 0..100i64 {
        let expected = i * 2;
        assert!(
            seen.contains_key(&expected),
            "Missing result {} for input {}",
            expected,
            i
        );
        assert_eq!(
            seen[&expected], 1,
            "Duplicate result {} (appeared {} times)",
            expected, seen[&expected]
        );
    }
}

// =============================================================================
// Test 3: Concurrent square ops — different op, same pattern
// =============================================================================

#[test]
fn test_concurrent_square_30_runs() {
    let config = single_op_graph(
        "g",
        "s",
        "square",
        vec![ref_input("n", parent_ref("g", "n"))],
        vec![],
    );
    let engine = Arc::new(make_hush(config));

    let handles: Vec<_> = (0..30)
        .map(|i| {
            let engine = Arc::clone(&engine);
            std::thread::spawn(move || {
                let result = run(&engine, json!({"n": i}));
                (i, result)
            })
        })
        .collect();

    let mut results: Vec<(i64, i64)> = handles
        .into_iter()
        .map(|h| {
            let (i, r) = h.join().unwrap();
            (i, r["result"].as_i64().unwrap())
        })
        .collect();
    results.sort_by_key(|&(i, _)| i);

    for (i, val) in results {
        assert_eq!(val, i * i, "square({}) should be {}", i, i * i);
    }
}

// =============================================================================
// Test 4: Concurrent chain execution — multi-op graph under load
// =============================================================================

#[test]
fn test_concurrent_chain_50_runs() {

    // Chain: double -> increment (x*2 + 1)
    let d = func_op(
        "d",
        "g",
        "double",
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );
    let inc = func_op(
        "inc",
        "g",
        "increment",
        vec![ref_input("x", op_ref("g.d", "result"))],
        vec![],
    );

    let config = chain_graph("g", vec![("d".into(), d), ("inc".into(), inc)]);
    let engine = Arc::new(make_hush(config));

    let handles: Vec<_> = (0..50)
        .map(|i| {
            let engine = Arc::clone(&engine);
            std::thread::spawn(move || {
                let result = run(&engine, json!({"x": i}));
                (i, result)
            })
        })
        .collect();

    let mut results: Vec<(i64, i64)> = handles
        .into_iter()
        .map(|h| {
            let (i, r) = h.join().unwrap();
            (i, r["result"].as_i64().unwrap())
        })
        .collect();
    results.sort_by_key(|&(i, _)| i);

    for (i, val) in results {
        let expected = i * 2 + 1;
        assert_eq!(val, expected, "double({}) + 1 should be {}", i, expected);
    }
}