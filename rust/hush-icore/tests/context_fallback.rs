//! Context hierarchy fallback tests.
//!
//! Tests that EngineState.get() walks up the dot-separated context hierarchy:
//! "main.[0].[1]" → "main.[0]" → "main"

mod common;

use hush_icore::states::state::EngineState;
use serde_json::json;

// =============================================================================
// Direct EngineState fallback tests
// =============================================================================

#[test]
fn test_exact_match() {
    let state = EngineState::new();
    state.set("op1", "x", "main", json!(42));

    let val = state.get("op1", "x", "main").unwrap();
    assert_eq!(*val, json!(42));
}

#[test]
fn test_fallback_one_level() {
    let state = EngineState::new();
    state.set("op1", "x", "main", json!(42));

    // Query from stream context "main.[0]" — should find "main"
    let val = state.get("op1", "x", "main.[0]").unwrap();
    assert_eq!(*val, json!(42));
}

#[test]
fn test_fallback_two_levels() {
    let state = EngineState::new();
    state.set("op1", "x", "main", json!(42));

    // Query from "main.[0].[1]" — should walk up to "main"
    let val = state.get("op1", "x", "main.[0].[1]").unwrap();
    assert_eq!(*val, json!(42));
}

#[test]
fn test_stream_context_overrides_parent() {
    let state = EngineState::new();
    state.set("op1", "x", "main", json!(100));
    state.set("op1", "x", "main.[0]", json!(200));

    // Exact match at stream context takes priority
    let val = state.get("op1", "x", "main.[0]").unwrap();
    assert_eq!(*val, json!(200));

    // Parent context still has its own value
    let val = state.get("op1", "x", "main").unwrap();
    assert_eq!(*val, json!(100));
}

#[test]
fn test_deep_stream_context_falls_back_to_mid() {
    let state = EngineState::new();
    state.set("op1", "x", "main", json!(1));
    state.set("op1", "x", "main.[0]", json!(2));

    // "main.[0].[3]" should fall back to "main.[0]", not "main"
    let val = state.get("op1", "x", "main.[0].[3]").unwrap();
    assert_eq!(*val, json!(2));
}

#[test]
fn test_no_fallback_for_missing_var() {
    let state = EngineState::new();
    state.set("op1", "x", "main", json!(42));

    // Different var name — should not find it
    let val = state.get("op1", "y", "main.[0]");
    assert!(val.is_none());
}

#[test]
fn test_no_fallback_for_missing_op() {
    let state = EngineState::new();
    state.set("op1", "x", "main", json!(42));

    // Different op — should not find it
    let val = state.get("op2", "x", "main.[0]");
    assert!(val.is_none());
}

#[test]
fn test_set_does_not_write_to_parent() {
    let state = EngineState::new();
    state.set("op1", "x", "main.[0]", json!(99));

    // Parent context should not have the value
    let val = state.get("op1", "x", "main");
    assert!(val.is_none());

    // Exact match works
    let val = state.get("op1", "x", "main.[0]").unwrap();
    assert_eq!(*val, json!(99));
}

#[test]
fn test_multiple_stream_contexts_independent() {
    let state = EngineState::new();
    state.set("gen", "chunk", "main.[0]", json!("hello"));
    state.set("gen", "chunk", "main.[1]", json!("world"));
    state.set("gen", "shared", "main", json!("common"));

    // Each stream context has its own value
    let v0 = state.get("gen", "chunk", "main.[0]").unwrap();
    assert_eq!(*v0, json!("hello"));
    let v1 = state.get("gen", "chunk", "main.[1]").unwrap();
    assert_eq!(*v1, json!("world"));

    // Both inherit shared value from parent
    let s0 = state.get("gen", "shared", "main.[0]").unwrap();
    assert_eq!(*s0, json!("common"));
    let s1 = state.get("gen", "shared", "main.[1]").unwrap();
    assert_eq!(*s1, json!("common"));
}

#[test]
fn test_loop_context_fallback() {
    let state = EngineState::new();
    state.set("g", "x", "main", json!(10));
    state.set("g", "x", "main.loop_1", json!(20));

    // Loop context 2 falls back to base "main"
    let val = state.get("g", "x", "main.loop_2").unwrap();
    assert_eq!(*val, json!(10));

    // Loop context 1 has its own value
    let val = state.get("g", "x", "main.loop_1").unwrap();
    assert_eq!(*val, json!(20));
}
