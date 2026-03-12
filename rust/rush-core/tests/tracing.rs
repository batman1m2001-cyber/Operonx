//! TraceCollector integration tests.
//!
//! Verifies trace data extraction from engine state after workflow execution.
//! Covers the critical bug: root graph MUST have $start_time for TraceCollector
//! to produce any nodes (sort_by_edges does DFS from root).

mod common;

use std::sync::Arc;

use serde_json::{json, Value};

use rush_core::config::GraphConfig;
use rush_core::tracing::collector::TraceCollector;

use common::*;

// =============================================================================
// Engine-level timing tests: verify $start_time/$end_time/$duration_ms stored
// =============================================================================

#[test]
fn test_root_graph_has_timing_metadata() {
    // THE BUG: engine.rs didn't store $start_time for root graph.
    // TraceCollector.iter_executed() uses $start_time as the marker for "was executed".
    // Without root graph timing, sort_by_edges() found no root → returned 0 nodes.
    let config = single_op_graph(
        "myflow",
        "d",
        "double",
        vec![ref_input("x", parent_ref("myflow", "x"))],
        vec![],
    );

    let mut engine = make_rush(config);
    engine.enable_timestamps();
    let result = run_full(&engine, json!({"x": 5}), Some("req-001".into()));

    let values = &result["$state"]["values"];

    // Root graph ("myflow") MUST have timing metadata
    assert!(
        values["myflow"]["$start_time"].is_object(),
        "Root graph 'myflow' must have $start_time in state"
    );
    assert!(
        values["myflow"]["$end_time"].is_object(),
        "Root graph 'myflow' must have $end_time in state"
    );
    assert!(
        values["myflow"]["$duration_ms"].is_object(),
        "Root graph 'myflow' must have $duration_ms in state"
    );

    // Leaf op must also have timing
    assert!(
        values["myflow.d"]["$start_time"].is_object(),
        "Leaf op 'myflow.d' must have $start_time"
    );
}

#[test]
fn test_root_graph_timing_values_are_valid() {
    let config = single_op_graph(
        "g",
        "d",
        "double",
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );

    let mut engine = make_rush(config);
    engine.enable_timestamps();
    let result = run_full(&engine, json!({"x": 5}), None);

    let values = &result["$state"]["values"];

    // $start_time should be an ISO 8601 timestamp string stored under context "main"
    let start_time = &values["g"]["$start_time"]["main"];
    assert!(
        start_time.is_string(),
        "Root $start_time must be a string, got: {:?}",
        start_time
    );
    let st_str = start_time.as_str().unwrap();
    assert!(
        st_str.contains("T") && (st_str.contains("+") || st_str.contains("Z")),
        "Root $start_time must be ISO 8601, got: {}",
        st_str
    );

    // $duration_ms should be a positive number
    let dur = &values["g"]["$duration_ms"]["main"];
    assert!(dur.is_f64() || dur.is_i64() || dur.is_u64(), "duration_ms must be numeric");
    let dur_val = dur.as_f64().unwrap();
    assert!(dur_val >= 0.0, "duration_ms must be non-negative, got: {}", dur_val);
}

#[test]
fn test_chain_graph_both_ops_and_root_have_timing() {
    // Chain: step1 (double) → step2 (increment)
    let config = chain_graph(
        "pipeline",
        vec![
            (
                "step1".into(),
                func_op(
                    "step1",
                    "pipeline",
                    "double",
                    vec![ref_input("x", parent_ref("pipeline", "x"))],
                    vec![],
                ),
            ),
            (
                "step2".into(),
                func_op(
                    "step2",
                    "pipeline",
                    "increment",
                    vec![ref_input("counter", op_ref("pipeline.step1", "result"))],
                    vec![],
                ),
            ),
        ],
    );

    let mut engine = make_rush(config);
    engine.enable_timestamps();
    let result = run_full(&engine, json!({"x": 5}), Some("req-chain".into()));

    let values = &result["$state"]["values"];

    // Root graph timing
    assert!(values["pipeline"]["$start_time"]["main"].is_string());
    assert!(values["pipeline"]["$end_time"]["main"].is_string());
    assert!(values["pipeline"]["$duration_ms"]["main"].as_f64().is_some());

    // Both ops have timing
    assert!(values["pipeline.step1"]["$start_time"]["main"].is_string());
    assert!(values["pipeline.step2"]["$start_time"]["main"].is_string());

    // Root duration >= sum of op durations (root includes scheduling overhead)
    let root_dur = values["pipeline"]["$duration_ms"]["main"].as_f64().unwrap();
    let s1_dur = values["pipeline.step1"]["$duration_ms"]["main"].as_f64().unwrap();
    let s2_dur = values["pipeline.step2"]["$duration_ms"]["main"].as_f64().unwrap();
    assert!(
        root_dur >= s1_dur && root_dur >= s2_dur,
        "Root duration ({:.3}ms) should be >= each op (s1={:.3}ms, s2={:.3}ms)",
        root_dur, s1_dur, s2_dur
    );
}

#[test]
fn test_no_timing_without_timestamps_enabled() {
    let config = single_op_graph(
        "g",
        "d",
        "double",
        vec![ref_input("x", parent_ref("g", "x"))],
        vec![],
    );

    // No enable_timestamps(), no tracers
    let engine = make_rush(config);
    let result = run_full(&engine, json!({"x": 5}), None);

    let values = &result["$state"]["values"];

    // Neither root nor leaf should have timing
    assert!(values["g"]["$start_time"].is_null(), "No timing without enable_timestamps()");
    assert!(values["g.d"]["$start_time"].is_null(), "No timing on leaf either");
}

// =============================================================================
// TraceCollector direct tests: manually populate EngineState, run collector
// =============================================================================

#[test]
fn test_collector_single_op_graph_produces_nodes() {
    use rush_core::states::state::EngineState;

    let config_json = serde_json::to_string(&single_op_graph(
        "wf",
        "step",
        "double",
        vec![ref_input("x", parent_ref("wf", "x"))],
        vec![],
    ))
    .unwrap();
    let graph_config = GraphConfig::from_json(&config_json).unwrap();

    let state = EngineState::new();
    state.set_request_id("req-coll-001".into());

    // Simulate engine: set timing for root graph
    state.set("wf", "$start_time", "main", json!("2026-03-12T10:00:00+00:00"));
    state.set("wf", "$end_time", "main", json!("2026-03-12T10:00:01+00:00"));
    state.set("wf", "$duration_ms", "main", json!(1000.0));

    // Simulate engine: set timing + outputs for leaf op
    state.set("wf.step", "$start_time", "main", json!("2026-03-12T10:00:00.100+00:00"));
    state.set("wf.step", "$end_time", "main", json!("2026-03-12T10:00:00.200+00:00"));
    state.set("wf.step", "$duration_ms", "main", json!(100.0));
    state.set("wf.step", "result", "main", json!(10));

    let collector = TraceCollector::new(&graph_config);
    let trace_data = collector.collect(&state, true);

    let nodes = trace_data["nodes"].as_array().unwrap();

    // Must have 2 nodes: root graph + leaf op
    assert_eq!(nodes.len(), 2, "Expected 2 nodes (root + leaf), got {}", nodes.len());

    // First node should be the root (node_type = "trace")
    let root = &nodes[0];
    assert_eq!(root["node_type"], "trace");
    assert_eq!(root["display_name"], "wf");
    assert!(root["parent_trace_key"].is_null(), "Root has no parent");
    assert!(root["start_time"].is_string());
    assert!(root["end_time"].is_string());
    assert_eq!(root["duration_ms"], 1000.0);

    // Second node should be the leaf op (node_type = "span")
    let leaf = &nodes[1];
    assert_eq!(leaf["node_type"], "span");
    assert_eq!(leaf["display_name"], "step");
    assert!(leaf["parent_trace_key"].is_string(), "Leaf must have parent");
    assert_eq!(leaf["duration_ms"], 100.0);

    // Parent of leaf should be root's trace_key
    let root_key = root["trace_key"].as_str().unwrap();
    assert_eq!(
        leaf["parent_trace_key"].as_str().unwrap(),
        root_key,
        "Leaf parent should be root"
    );

    // Summary
    let summary = &trace_data["summary"];
    assert_eq!(summary["total_records"], 2);

    // Request ID
    assert_eq!(trace_data["request_id"], "req-coll-001");
}

#[test]
fn test_collector_no_root_timing_produces_zero_nodes() {
    // THE BUG SCENARIO: if root graph has no $start_time,
    // TraceCollector produces 0 nodes even though leaf ops have timing.
    use rush_core::states::state::EngineState;

    let config_json = serde_json::to_string(&single_op_graph(
        "wf",
        "step",
        "double",
        vec![ref_input("x", parent_ref("wf", "x"))],
        vec![],
    ))
    .unwrap();
    let graph_config = GraphConfig::from_json(&config_json).unwrap();

    let state = EngineState::new();

    // Only set timing for the leaf op — NOT for root graph
    state.set("wf.step", "$start_time", "main", json!("2026-03-12T10:00:00.100+00:00"));
    state.set("wf.step", "$end_time", "main", json!("2026-03-12T10:00:00.200+00:00"));
    state.set("wf.step", "$duration_ms", "main", json!(100.0));

    let collector = TraceCollector::new(&graph_config);
    let trace_data = collector.collect(&state, true);

    let nodes = trace_data["nodes"].as_array().unwrap();

    // Without root timing, sort_by_edges DFS from roots finds no root → 0 nodes
    // This test documents the behavior that caused the tracing bug.
    // The leaf has a parent_trace_key pointing to "wf:main" but "wf" was never
    // registered as executed (no $start_time), so it's not in node_lookup,
    // and the leaf becomes an orphan that sort_by_edges drops.
    assert_eq!(
        nodes.len(),
        0,
        "Without root $start_time, collector produces 0 nodes (the original bug)"
    );
}

#[test]
fn test_collector_chain_graph_parent_child_structure() {
    use rush_core::states::state::EngineState;

    let config_json = serde_json::to_string(&chain_graph(
        "pipeline",
        vec![
            (
                "analyze".into(),
                func_op(
                    "analyze",
                    "pipeline",
                    "analyze_text",
                    vec![ref_input("text", parent_ref("pipeline", "text"))],
                    vec![],
                ),
            ),
            (
                "classify".into(),
                func_op(
                    "classify",
                    "pipeline",
                    "classify_by_count",
                    vec![ref_input("word_count", op_ref("pipeline.analyze", "word_count"))],
                    vec![],
                ),
            ),
        ],
    ))
    .unwrap();
    let graph_config = GraphConfig::from_json(&config_json).unwrap();

    let state = EngineState::new();
    state.set_request_id("req-chain".into());

    // Root timing
    state.set("pipeline", "$start_time", "main", json!("2026-03-12T10:00:00+00:00"));
    state.set("pipeline", "$end_time", "main", json!("2026-03-12T10:00:00.500+00:00"));
    state.set("pipeline", "$duration_ms", "main", json!(500.0));

    // Op 1: analyze
    state.set("pipeline.analyze", "$start_time", "main", json!("2026-03-12T10:00:00.050+00:00"));
    state.set("pipeline.analyze", "$end_time", "main", json!("2026-03-12T10:00:00.150+00:00"));
    state.set("pipeline.analyze", "$duration_ms", "main", json!(100.0));
    state.set("pipeline.analyze", "word_count", "main", json!(5));
    state.set("pipeline.analyze", "preview", "main", json!("hello world test"));

    // Op 2: classify
    state.set("pipeline.classify", "$start_time", "main", json!("2026-03-12T10:00:00.200+00:00"));
    state.set("pipeline.classify", "$end_time", "main", json!("2026-03-12T10:00:00.300+00:00"));
    state.set("pipeline.classify", "$duration_ms", "main", json!(100.0));
    state.set("pipeline.classify", "category", "main", json!("short"));

    let collector = TraceCollector::new(&graph_config);
    let trace_data = collector.collect(&state, true);

    let nodes = trace_data["nodes"].as_array().unwrap();

    // 3 nodes: root + analyze + classify
    assert_eq!(nodes.len(), 3, "Expected 3 nodes, got {}", nodes.len());

    // Verify tree structure
    let root = &nodes[0];
    assert_eq!(root["node_type"], "trace");
    assert_eq!(root["display_name"], "pipeline");
    assert!(root["parent_trace_key"].is_null());

    // Both children should point to root
    let root_key = root["trace_key"].as_str().unwrap();
    for node in &nodes[1..] {
        assert_eq!(
            node["parent_trace_key"].as_str().unwrap(),
            root_key,
            "Node '{}' should be child of root",
            node["display_name"]
        );
    }

    // Verify topological order: analyze before classify
    let names: Vec<&str> = nodes.iter().map(|n| n["display_name"].as_str().unwrap()).collect();
    let analyze_pos = names.iter().position(|n| *n == "analyze").unwrap();
    let classify_pos = names.iter().position(|n| *n == "classify").unwrap();
    assert!(
        analyze_pos < classify_pos,
        "analyze should come before classify in topo order"
    );
}

#[test]
fn test_collector_streaming_graph_with_generator() {
    use rush_core::states::state::EngineState;

    // Generator → downstream op, 3 stream contexts
    let gen = generator_op(
        "gen",
        "sg",
        "range_gen",
        vec![ref_input("n", parent_ref("sg", "n"))],
    );
    let step = func_op(
        "step",
        "sg",
        "double",
        vec![ref_input("x", op_ref("sg.gen", "value"))],
        vec![],
    );

    let config = streaming_graph(
        "sg",
        vec![("gen".into(), gen), ("step".into(), step)],
        vec!["gen"],
        vec![("gen", 0), ("step", 1)],
        vec![("gen", vec![("step", false), ("__end__", false)])],
        json!({"step": {"gen": 1}}),
    );
    let config_json = serde_json::to_string(&config).unwrap();
    let graph_config = GraphConfig::from_json(&config_json).unwrap();

    let state = EngineState::new();
    state.set_request_id("req-stream".into());

    // Root graph timing
    state.set("sg", "$start_time", "main", json!("2026-03-12T10:00:00+00:00"));
    state.set("sg", "$end_time", "main", json!("2026-03-12T10:00:01+00:00"));
    state.set("sg", "$duration_ms", "main", json!(1000.0));

    // Generator timing (batch context)
    state.set("sg.gen", "$start_time", "main", json!("2026-03-12T10:00:00.010+00:00"));
    state.set("sg.gen", "$end_time", "main", json!("2026-03-12T10:00:00.100+00:00"));
    state.set("sg.gen", "$duration_ms", "main", json!(90.0));

    // 3 stream contexts: main.[0], main.[1], main.[2]
    for i in 0..3 {
        let ctx = format!("main.[{}]", i);
        state.set("sg.step", "$start_time", &ctx, json!(format!("2026-03-12T10:00:00.{}00+00:00", i + 2)));
        state.set("sg.step", "$end_time", &ctx, json!(format!("2026-03-12T10:00:00.{}50+00:00", i + 2)));
        state.set("sg.step", "$duration_ms", &ctx, json!(50.0));
        state.set("sg.step", "result", &ctx, json!(i * 2));
    }

    let collector = TraceCollector::new(&graph_config);
    let trace_data = collector.collect(&state, true);

    let nodes = trace_data["nodes"].as_array().unwrap();

    // Should have: root + generator + 3 synthetic context nodes + 3 stream items = 8
    // (or root + generator + 3 context groups ([0],[1],[2]) + 3 step executions)
    assert!(
        nodes.len() >= 5,
        "Streaming graph should produce at least 5 nodes (root + gen + 3 stream items), got {}",
        nodes.len()
    );

    // Root must be first
    assert_eq!(nodes[0]["node_type"], "trace");
    assert_eq!(nodes[0]["display_name"], "sg");

    // Generator node must exist with kind=generator
    let gen_nodes: Vec<&Value> = nodes.iter().filter(|n| n["kind"] == "generator").collect();
    assert_eq!(gen_nodes.len(), 1, "Should have exactly 1 generator node");
    assert_eq!(gen_nodes[0]["display_name"], "gen");

    // Stream context or stream items should exist
    let stream_nodes: Vec<&Value> = nodes
        .iter()
        .filter(|n| n["kind"] == "stream_context" || n["kind"] == "batch")
        .filter(|n| n["display_name"].as_str().map_or(false, |d| d.starts_with('[') || d == "step"))
        .collect();
    assert!(
        !stream_nodes.is_empty(),
        "Should have stream context or stream item nodes"
    );
}

#[test]
fn test_collector_empty_state_produces_no_nodes() {
    use rush_core::states::state::EngineState;

    let config_json = serde_json::to_string(&single_op_graph(
        "wf",
        "step",
        "double",
        vec![ref_input("x", parent_ref("wf", "x"))],
        vec![],
    ))
    .unwrap();
    let graph_config = GraphConfig::from_json(&config_json).unwrap();

    let state = EngineState::new();
    // No timing data at all

    let collector = TraceCollector::new(&graph_config);
    let trace_data = collector.collect(&state, true);

    let nodes = trace_data["nodes"].as_array().unwrap();
    assert_eq!(nodes.len(), 0, "Empty state should produce 0 nodes");
}

#[test]
fn test_collector_summary_fields() {
    use rush_core::states::state::EngineState;

    let config_json = serde_json::to_string(&chain_graph(
        "g",
        vec![
            (
                "a".into(),
                func_op("a", "g", "double", vec![ref_input("x", parent_ref("g", "x"))], vec![]),
            ),
            (
                "b".into(),
                func_op("b", "g", "increment", vec![ref_input("counter", op_ref("g.a", "result"))], vec![]),
            ),
        ],
    ))
    .unwrap();
    let graph_config = GraphConfig::from_json(&config_json).unwrap();

    let state = EngineState::new();
    state.set("g", "$start_time", "main", json!("2026-03-12T10:00:00+00:00"));
    state.set("g", "$end_time", "main", json!("2026-03-12T10:00:01+00:00"));
    state.set("g", "$duration_ms", "main", json!(1000.0));
    state.set("g.a", "$start_time", "main", json!("2026-03-12T10:00:00.100+00:00"));
    state.set("g.a", "$end_time", "main", json!("2026-03-12T10:00:00.200+00:00"));
    state.set("g.a", "$duration_ms", "main", json!(100.0));
    state.set("g.b", "$start_time", "main", json!("2026-03-12T10:00:00.300+00:00"));
    state.set("g.b", "$end_time", "main", json!("2026-03-12T10:00:00.400+00:00"));
    state.set("g.b", "$duration_ms", "main", json!(100.0));

    let collector = TraceCollector::new(&graph_config);
    let trace_data = collector.collect(&state, true);

    let summary = &trace_data["summary"];
    // total_ops = 3 (root graph + a + b)
    assert_eq!(summary["total_ops"], 3);
    // total_records = 3 (all 3 executed)
    assert_eq!(summary["total_records"], 3);
    // total_duration_ms = 1000 + 100 + 100 = 1200
    assert_eq!(summary["total_duration_ms"], 1200.0);
    assert_eq!(summary["stream_count"], 0);
    assert_eq!(summary["total_yields"], 0);
    assert_eq!(summary["error_count"], 0);
}

#[test]
fn test_collector_tags_and_request_id() {
    use rush_core::states::state::EngineState;

    let config_json = serde_json::to_string(&single_op_graph(
        "wf",
        "step",
        "double",
        vec![ref_input("x", parent_ref("wf", "x"))],
        vec![],
    ))
    .unwrap();
    let graph_config = GraphConfig::from_json(&config_json).unwrap();

    let state = EngineState::new();
    state.set_request_id("req-xyz-789".into());
    state.add_tags(vec!["production".into(), "fast".into()]);

    state.set("wf", "$start_time", "main", json!("2026-03-12T10:00:00+00:00"));
    state.set("wf", "$end_time", "main", json!("2026-03-12T10:00:01+00:00"));
    state.set("wf", "$duration_ms", "main", json!(1000.0));
    state.set("wf.step", "$start_time", "main", json!("2026-03-12T10:00:00.100+00:00"));
    state.set("wf.step", "$end_time", "main", json!("2026-03-12T10:00:00.200+00:00"));
    state.set("wf.step", "$duration_ms", "main", json!(100.0));

    let collector = TraceCollector::new(&graph_config);
    let trace_data = collector.collect(&state, true);

    assert_eq!(trace_data["request_id"], "req-xyz-789");
    assert_eq!(trace_data["workflow_name"], "wf");

    let tags = trace_data["tags"].as_array().unwrap();
    let tag_strs: Vec<&str> = tags.iter().map(|t| t.as_str().unwrap()).collect();
    assert!(tag_strs.contains(&"production"));
    assert!(tag_strs.contains(&"fast"));
}

// =============================================================================
// End-to-end: engine.run_json with timestamps → collect → verify tree
// =============================================================================

#[test]
fn test_engine_run_then_collect_produces_complete_trace() {
    // Full integration: run engine with timestamps, manually create TraceCollector,
    // verify it can extract a proper trace tree from the state embedded in result.
    let config = chain_graph(
        "e2e",
        vec![
            (
                "step1".into(),
                func_op(
                    "step1",
                    "e2e",
                    "double",
                    vec![ref_input("x", parent_ref("e2e", "x"))],
                    vec![],
                ),
            ),
            (
                "step2".into(),
                func_op(
                    "step2",
                    "e2e",
                    "increment",
                    vec![ref_input("x", op_ref("e2e.step1", "result"))],
                    vec![],
                ),
            ),
        ],
    );

    let mut engine = make_rush(config.clone());
    engine.enable_timestamps();
    let result = run_full(&engine, json!({"x": 7}), Some("req-e2e".into()));

    // Verify the actual outputs (auto-forwarded from last op: increment returns {result: x+1})
    assert_eq!(result["result"], 15); // 7*2 = 14, 14+1 = 15

    let values = &result["$state"]["values"];

    // All 3 components must have timing
    for name in &["e2e", "e2e.step1", "e2e.step2"] {
        assert!(
            values[name]["$start_time"]["main"].is_string(),
            "{} missing $start_time",
            name
        );
        assert!(
            values[name]["$end_time"]["main"].is_string(),
            "{} missing $end_time",
            name
        );
        assert!(
            values[name]["$duration_ms"]["main"].as_f64().is_some(),
            "{} missing $duration_ms",
            name
        );
    }

    // Now verify TraceCollector would produce proper nodes
    // by building a fresh EngineState from the values snapshot
    use rush_core::states::state::EngineState;

    let state = EngineState::new();
    state.set_request_id("req-e2e".into());

    // Copy timing from result into fresh state
    for name in &["e2e", "e2e.step1", "e2e.step2"] {
        let st = values[name]["$start_time"]["main"].as_str().unwrap();
        let et = values[name]["$end_time"]["main"].as_str().unwrap();
        let dur = values[name]["$duration_ms"]["main"].as_f64().unwrap();
        state.set(name, "$start_time", "main", json!(st));
        state.set(name, "$end_time", "main", json!(et));
        state.set(name, "$duration_ms", "main", json!(dur));
    }

    let config_str = serde_json::to_string(&config).unwrap();
    let graph_config = GraphConfig::from_json(&config_str).unwrap();
    let collector = TraceCollector::new(&graph_config);
    let trace_data = collector.collect(&state, true);

    let nodes = trace_data["nodes"].as_array().unwrap();
    assert_eq!(nodes.len(), 3, "E2E trace should have 3 nodes");
    assert_eq!(nodes[0]["node_type"], "trace");
    assert_eq!(nodes[0]["display_name"], "e2e");

    // All nodes should have timing
    for node in nodes {
        assert!(node["start_time"].is_string(), "Node '{}' missing start_time", node["display_name"]);
    }
}

#[test]
fn test_engine_run_streaming_graph_collect_has_root() {
    // Streaming graph: generator → downstream, verify root graph is in trace
    let gen = generator_op(
        "gen",
        "sg",
        "range_gen",
        vec![ref_input("n", parent_ref("sg", "n"))],
    );
    let step = func_op(
        "step",
        "sg",
        "double",
        vec![ref_input("x", op_ref("sg.gen", "value"))],
        vec![],
    );

    let config = streaming_graph(
        "sg",
        vec![("gen".into(), gen), ("step".into(), step)],
        vec!["gen"],
        vec![("gen", 0), ("step", 1)],
        vec![("gen", vec![("step", false), ("__end__", false)])],
        json!({"step": {"gen": 1}}),
    );

    let mut engine = make_rush(config);
    engine.enable_timestamps();
    let result = run_full(&engine, json!({"n": 3}), Some("req-sg".into()));

    let values = &result["$state"]["values"];

    // Root graph MUST have timing even for streaming graphs
    assert!(
        values["sg"]["$start_time"]["main"].is_string(),
        "Streaming graph root must have $start_time"
    );
    assert!(
        values["sg"]["$duration_ms"]["main"].as_f64().is_some(),
        "Streaming graph root must have $duration_ms"
    );
}
